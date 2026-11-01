# -*- coding: utf-8 -*-
"""人声状态检测（判断层）：把换声前的人声切成
silence / normal / breathy / humming（无词哼唱过渡段）
输出 JSON，供人工校对。CLI:
python vocal_state.py --vocal X.wav [--words words.json] [--out states.json]
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, librosa
from assets import audio, analysis

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"

def harmonicity(x, f0, sr, win=1024):
    """谐波能量占比（0-1），越低越气声/噪声"""
    S = np.abs(librosa.stft(x, n_fft=win, hop_length=win//2))
    f = librosa.fft_frequencies(sr=sr, n_fft=win)
    e = (S**2).sum(0); tot = np.maximum(e, 1e-12)
    mask = np.zeros_like(f, dtype=bool)
    for k in range(1, 4):
        if np.isfinite(f0) and f0 > 40:
            for h in (f0*k-15, f0*k+15):
                pass
    # 简化：用 f0 附近 ±3% 频带能量占比
    if not (np.isfinite(f0) and f0 > 40):
        return 0.0
    idx = (np.abs(f - f0) < f0*0.04) | (np.abs(f - 2*f0) < 2*f0*0.03) | (np.abs(f - 3*f0) < 3*f0*0.03)
    harm = (S[idx]**2).sum()
    return float(harm / max(float((S**2).sum()), 1e-12))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocal", required=True); ap.add_argument("--words", default="")
    ap.add_argument("--out", default=""); ap.add_argument("--sr", type=int, default=22050)
    a = ap.parse_args()
    x = audio.decode(FF, a.vocal, sr=a.sr, channels=1).astype(np.float32)
    dur = len(x)/a.sr
    hop = int(0.05*a.sr)
    f0 = analysis.f0_track(x, a.sr); fps = len(f0)/dur
    words = []
    if a.words and os.path.isfile(a.words):
        w = json.load(open(a.words, encoding="utf-8-sig"))
        for it in w:
            if isinstance(it, (list, tuple)) and len(it) >= 2: words.append((float(it[0]), float(it[1])))
            elif isinstance(it, dict) and "start" in it: words.append((float(it["start"]), float(it["end"])))
    def has_word(t):
        return any(s-0.2 <= t <= e+0.2 for s, e in words)
    S = np.abs(librosa.stft(x, n_fft=1024, hop_length=hop))**2
    flat = librosa.feature.spectral_flatness(S=np.sqrt(S))[0]
    rms = librosa.feature.rms(y=x, frame_length=1024, hop_length=hop)[0]
    n = min(len(flat), len(rms), len(f0), len(x)//hop)
    states = []
    for i in range(n):
        t = i*hop/a.sr
        db = 20*np.log10(max(float(rms[min(i, len(rms)-1)]), 1e-9))
        h = harmonicity(x[i*hop:i*hop+1024], float(f0[min(i, len(f0)-1)]), a.sr)
        f = float(flat[min(i, len(flat)-1)])
        voiced = np.isfinite(f0[min(i, len(f0)-1)]) if i < len(f0) else False
        if db < -45: st = "silence"
        elif not voiced and h < 0.2: st = "silence"
        elif voiced and not has_word(t) and db > -40: st = "humming"
        elif voiced and (h < 0.18 or f > 0.12): st = "breathy"
        else: st = "normal"
        states.append(st)
    # 合并相邻同类
    segs = []
    for i, st in enumerate(states):
        t0, t1 = i*hop/a.sr, (i+1)*hop/a.sr
        if segs and segs[-1]["state"] == st: segs[-1]["b"] = t1
        else: segs.append({"a": round(t0, 2), "b": round(t1, 2), "state": st})
    segs = [s for s in segs if s["b"] - s["a"] >= 0.15]
    out = {"duration": round(dur, 2), "segments": segs,
           "summary": {k: round(sum(s["b"]-s["a"] for s in segs if s["state"]==k)/max(dur,1e-9), 3) for k in ("normal","breathy","humming","silence")}}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(json.dumps(out["summary"], ensure_ascii=False))
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)

if __name__ == "__main__":
    main()
