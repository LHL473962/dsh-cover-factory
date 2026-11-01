# -*- coding: utf-8 -*-
"""逐段音高对齐：检测换声输出相对源人声的持续音高偏差，用 pitch_shift 局部修正（保留换声音色）。
CLI: python pitch_align.py --source S.wav --converted C.wav --out O.wav [--tol 0.7] [--maxwin 1.2]
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, librosa, soundfile as sf
from assets import audio, analysis
FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"
ap = argparse.ArgumentParser()
ap.add_argument("--source", required=True); ap.add_argument("--converted", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--tol", type=float, default=0.7)
ap.add_argument("--maxwin", type=float, default=1.2); ap.add_argument("--fade", type=float, default=0.06)
a = ap.parse_args()
sr = 44100
s = audio.decode(FF, a.source, sr=sr, channels=1).astype(np.float32)
c = audio.decode(FF, a.converted, sr=sr, channels=1).astype(np.float32)
n = min(len(s), len(c)); s, c = s[:n], c[:n]
fs = np.array(analysis.f0_track(s, sr)); fc = np.array(analysis.f0_track(c, sr))
k = min(len(fs), len(fc)); fs, fc = fs[:k], fc[:k]
dur = n/sr; fps = k/dur
dev = np.full(k, np.nan)
ok = np.isfinite(fs)&np.isfinite(fc)&(fs>40)&(fc>40)
dev[ok] = 12*np.log2(fc[ok]/fs[ok])
out = c.copy(); fixed = 0; total = 0.0
i = 0
while i < k:
    # 八度级偏差单独处理：|dev|>10 时按 12 的半音修正
    if np.isfinite(dev[i]) and abs(dev[i]) > 10:
        j = i
        while j < k and np.isfinite(dev[j]) and abs(dev[j]) > 10: j += 1
        t0 = i/fps; t1 = min(dur, j/fps)
        if (t1-t0) <= 0.6:
            shift = 12.0*np.sign(float(np.median(dev[i:j])))
            a0 = int(max(0,(t0-a.fade))*sr); b0 = int(min(n,(t1+a.fade))*sr)
            try:
                sh = librosa.effects.pitch_shift(c[a0:b0], sr=sr, n_steps=-shift)
                ramp = np.linspace(0,1,b0-a0,dtype=np.float32); w = 1-np.abs(2*ramp-1)
                out[a0:b0] = out[a0:b0]*(1-w) + sh*w
                fixed += 1; total += (t1-t0)
            except Exception:
                pass
        i = j
        continue
    if np.isfinite(dev[i]) and abs(dev[i]) > a.tol:
        j = i
        while j < k and np.isfinite(dev[j]) and abs(dev[j]) > a.tol: j += 1
        if (j-i) >= 3:  # 至少 60ms
            t0 = i/fps; t1 = min(dur, j/fps)
            if (t1-t0) <= a.maxwin and fixed < 40:
                shift = float(np.median(dev[i:j]))
                a0 = int(max(0, (t0-a.fade))*sr); b0 = int(min(n, (t1+a.fade)*sr))
                seg = c[a0:b0]
                try:
                    sh = librosa.effects.pitch_shift(seg, sr=sr, n_steps=-shift)
                    ramp = np.linspace(0, 1, b0-a0, dtype=np.float32); w = 1-np.abs(2*ramp-1)
                    out[a0:b0] = c[a0:b0]*(1-w) + sh*w
                    fixed += 1; total += (t1-t0)
                except Exception:
                    pass
        i = j
    else:
        i += 1
sf.write(a.out, out, sr, subtype="PCM_16")
print({"fixed": fixed, "seconds": round(total, 2)})
