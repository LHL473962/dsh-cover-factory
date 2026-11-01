# -*- coding: utf-8 -*-
"""断音修补：源人声有声、换声输出却塌陷的短段，用源人声补回（电平匹配+交叉淡化）。
CLI: python repair_dropouts.py --source Vocals.wav --converted X.wav --out Y.wav [--max-ms 200] [--blend 1.0]
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, soundfile as sf
from assets import audio

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True); ap.add_argument("--converted", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--max-ms", type=float, default=200)
    ap.add_argument("--blend", type=float, default=1.0)
    a = ap.parse_args()
    s = audio.decode(FF, a.source, sr=44100, channels=1).astype(np.float32)
    c = audio.decode(FF, a.converted, sr=44100, channels=1).astype(np.float32)
    n = min(len(s), len(c)); s, c = s[:n], c[:n]
    hop = int(0.02*44100); m = n//hop
    env = lambda v: np.array([np.sqrt((v[i*hop:(i+1)*hop].astype(np.float64)**2).mean()) for i in range(m)])
    ds = 20*np.log10(np.maximum(env(s), 1e-9)); dc = 20*np.log10(np.maximum(env(c), 1e-9))
    ms = np.median(ds[ds > -40]); mc = np.median(dc[dc > -40])
    bad = (ds > ms-20) & (dc < mc-30)
    out = c.copy()
    filled = 0; total = 0.0
    i = 0
    while i < m:
        if bad[i]:
            j = i
            while j < m and bad[j]: j += 1
            dur_ms = (j-i)*20
            if dur_ms <= a.max_ms:
                a0 = max(0, i*hop - 220); b0 = min(n, j*hop + 220)
                # 电平匹配：源段 RMS → 换声中位
                seg_s = s[a0:b0].astype(np.float64)
                rs = np.sqrt((seg_s**2).mean())
                target = 10**(mc/20)
                g = float(np.clip(target/max(rs, 1e-9), 0.2, 4.0))
                ramp = np.linspace(0, 1, b0-a0, dtype=np.float32)
                w = np.minimum(ramp, ramp[::-1])  # 三角淡入淡出
                out[a0:b0] = c[a0:b0]*(1-a.blend*w) + s[a0:b0]*g*(a.blend*w)
                filled += 1; total += dur_ms/1000
            i = j
        else:
            i += 1
    sf.write(a.out, out, 44100, subtype="PCM_16")
    print({"filled": filled, "seconds": round(total, 2)})

if __name__ == "__main__":
    main()
