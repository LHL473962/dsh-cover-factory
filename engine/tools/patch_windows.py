# -*- coding: utf-8 -*-
"""定点源声替换：指定时间段用源人声盖掉换声输出（电平匹配 + 50ms 交叉淡化）。"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, soundfile as sf
from assets import audio
FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"
ap = argparse.ArgumentParser()
ap.add_argument("--source", required=True); ap.add_argument("--converted", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--windows", required=True, help='JSON: [{"a":198.0,"b":199.0}]')
ap.add_argument("--fade", type=float, default=0.05)
a = ap.parse_args()
s = audio.decode(FF, a.source, sr=44100, channels=1).astype(np.float32)
c = audio.decode(FF, a.converted, sr=44100, channels=1).astype(np.float32)
n = min(len(s), len(c)); s, c = s[:n], c[:n]; sr = 44100
out = c.copy()
for w in json.load(open(a.windows, encoding="utf-8-sig")):
    a0 = max(0, int((w["a"]-a.fade)*sr)); b0 = min(n, int((w["b"]+a.fade)*sr))
    seg_s = s[a0:b0].astype(np.float64); seg_c = c[a0:b0].astype(np.float64)
    rs = np.sqrt((seg_s**2).mean()); rc = np.sqrt((seg_c**2).mean())
    g = float(np.clip(rc/max(rs, 1e-9), 0.2, 4.0))
    ramp = np.linspace(0, 1, b0-a0, dtype=np.float32)
    wgt = 1.0 - np.abs(2.0*ramp - 1.0)  # 中心 1.0，两端 0
    out[a0:b0] = c[a0:b0]*(1-wgt) + s[a0:b0]*g*wgt
sf.write(a.out, out, sr, subtype="PCM_16")
print({"patched": len(json.load(open(a.windows, encoding="utf-8-sig")))})
