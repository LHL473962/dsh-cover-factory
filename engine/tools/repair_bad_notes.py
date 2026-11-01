# -*- coding: utf-8 -*-
"""坏音修补：源 f0 出现八度跳变（跟踪错误）的位置，用源人声盖掉换声输出（电平匹配+淡化）。
CLI: python repair_bad_notes.py --source Vocals.wav --converted X.wav --out Y.wav [--pad 0.15]
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, soundfile as sf
from assets import audio, analysis

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True); ap.add_argument("--converted", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--pad", type=float, default=0.15)
    a = ap.parse_args()
    s = audio.decode(FF, a.source, sr=44100, channels=1).astype(np.float32)
    c = audio.decode(FF, a.converted, sr=44100, channels=1).astype(np.float32)
    n = min(len(s), len(c)); s, c = s[:n], c[:n]; sr = 44100
    f0 = np.array(analysis.f0_track(s, sr)); dur = n/sr; fps = len(f0)/dur
    lg = np.log2(np.maximum(f0, 1e-9)); v = f0 > 0
    jumps = np.zeros(len(f0), dtype=bool)
    for i in range(1, len(lg)):
        if v[i] and v[i-1] and 0.75 < abs(lg[i]-lg[i-1]) < 1.25:  # 只认八度级跳变
            jumps[i-1:i+1] = True
    out = c.copy(); fixed = 0; total = 0.0
    i = 0
    while i < len(jumps):
        if jumps[i]:
            j = i
            while j < len(jumps) and jumps[j]: j += 1
            t0 = max(0.0, i/fps - a.pad); t1 = min(dur, j/fps + a.pad)
            if (t1 - t0) <= 0.6 and total < 3.0 and fixed < 30:
                a0, b0 = int(t0*sr), int(t1*sr)
                seg_s = s[a0:b0].astype(np.float64)
                rs = np.sqrt((seg_s**2).mean())
                mc = np.median(20*np.log10(np.maximum(np.abs(c[np.isfinite(c)]), 1e-9))) if False else None
                # 用换声段自身电平做目标
                rc = np.sqrt((c[a0:b0].astype(np.float64)**2).mean())
                g = float(np.clip((rc/max(rs, 1e-9)), 0.2, 4.0))
                ramp = np.linspace(0, 1, b0-a0, dtype=np.float32)
                w = np.minimum(ramp, ramp[::-1]) ** 0.5
                out[a0:b0] = c[a0:b0]*(1-w) + s[a0:b0]*g*w
                fixed += 1; total += (t1-t0)
            i = j
        else:
            i += 1
    sf.write(a.out, out, sr, subtype="PCM_16")
    print({"fixed": fixed, "seconds": round(total, 2)})

if __name__ == "__main__":
    main()
