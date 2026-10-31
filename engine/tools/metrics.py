# -*- coding: utf-8 -*-
"""统一音质指标：给回归集和 qa 用。CLI:
python metrics.py --vocal A.wav [--inst B.wav] [--mix C.mp3] [--json out.json]
输出：LUFS/峰值/谱平坦度/频段/浊音率/串音/比例
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from assets import audio, analysis

FF = os.environ.get("SUIS_FFMPEG", r"D:/VoiceData/tools/bin/ffmpeg.exe")

def _load(p, sr=44100):
    return audio.decode(FF, p, sr=sr, channels=1).astype(np.float32)

def lufs(p):
    try:
        import pyloudnorm as pyln
        x = _load(p, 48000)
        meter = pyln.Meter(48000)
        return round(float(meter.integrated_loudness(x)), 2)
    except Exception:
        return None

def peak_db(p):
    """用 ffmpeg volumedetect 量峰值（对 mp3 解码过冲更真实）"""
    import subprocess, re
    out = subprocess.run([FF, "-hide_banner", "-nostats", "-i", p, "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, errors="ignore")
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", out.stderr)
    if m:
        return round(float(m.group(1)), 2)
    x = _load(p)
    return round(float(20*np.log10(max(np.abs(x).max(), 1e-9))), 2)

def spec_metrics(p):
    import librosa
    x = _load(p)
    S = np.abs(librosa.stft(x, n_fft=2048))**2
    f = librosa.fft_frequencies(sr=44100, n_fft=2048)
    e = S.sum(1); tot = max(e.sum(), 1e-12)
    band = lambda lo, hi: round(float(10*np.log10(max(float(e[(f>=lo)&(f<hi)].sum())/tot, 1e-12))), 2)
    flat = float(np.mean(librosa.feature.spectral_flatness(S=np.sqrt(S))))
    cent = float(np.sum(f*e)/tot)
    return {"flatness": round(flat, 5), "centroid": round(cent), "b2_4k": band(2000, 4000),
            "b4_8k": band(4000, 8000), "b8_16k": band(8000, 16000)}

def vocal_metrics(vocal, inst=None):
    import librosa
    v = _load(vocal)
    out = {}
    out.update(spec_metrics(vocal))
    f0 = analysis.f0_track(v, 44100); s = analysis.f0_summary(f0)
    out["voiced"] = round(s["voiced_frames"]/max(s["frames"], 1), 3)
    H, P = librosa.effects.hpss(v)
    out["perc"] = round(float((P*P).sum()/((H*H).sum()+(P*P).sum()+1e-9)), 3)
    if inst:
        i = _load(inst); n = min(len(v), len(i))
        out["corr"] = round(float(np.corrcoef(v[:n], i[:n])[0, 1]), 4)
        vr = float(np.sqrt((v.astype(np.float64)**2).mean()))
        ir = float(np.sqrt((i.astype(np.float64)**2).mean()))
        out["ratioDb"] = round(20*np.log10(max(vr, 1e-9)/max(ir, 1e-9)), 2)
    out["peak"] = peak_db(vocal)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocal"); ap.add_argument("--inst"); ap.add_argument("--mix")
    ap.add_argument("--json")
    a = ap.parse_args()
    res = {}
    if a.mix:
        res["mix"] = {"lufs": lufs(a.mix), "peak": peak_db(a.mix), **spec_metrics(a.mix)}
    if a.vocal:
        res["vocal"] = vocal_metrics(a.vocal, a.inst)
    text = json.dumps(res, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        open(a.json, "w", encoding="utf-8").write(text)

if __name__ == "__main__":
    main()
