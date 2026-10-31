# -*- coding: utf-8 -*-
"""自动增益：按“人声活跃帧中位电平”与“伴奏活跃帧中位电平”算增益。
用法: python engine/tools/auto_gain.py --vocal X --inst Y [--target-db -4]
输出 JSON: {gainDb, vocalDb, instDb, targetDb}
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from assets import audio

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"

def active_median_db(path, win=0.05, floor_db=-45.0):
    x = audio.decode(FF, path, sr=22050, channels=1).astype(np.float64)
    n = max(1, int(round(win * 22050)))
    m = len(x) // n
    if m < 5:
        return 20 * np.log10(max(np.sqrt((x * x).mean()), 1e-9))
    env = np.array([np.sqrt((x[i*n:(i+1)*n] ** 2).mean()) for i in range(m)])
    db = 20 * np.log10(np.maximum(env, 1e-9))
    act = db[db > floor_db]
    if act.size < 5:
        return float(db.max())
    return float(np.median(act))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocal", required=True)
    ap.add_argument("--inst", required=True)
    ap.add_argument("--target-db", type=float, default=-4.0,
                    help="人声相对伴奏的目标差（负=人声低）。默认 -4 dB")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    v = active_median_db(a.vocal)
    i = active_median_db(a.inst)
    gain = (i + a.target_db) - v
    out = {"gainDb": round(gain, 2), "vocalDb": round(v, 2), "instDb": round(i, 2),
           "targetDb": a.target_db, "ratioNow": round(v - i, 2)}
    if a.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("vocal=%.2f dB  inst=%.2f dB  ratio=%.2f  target=%.2f  gain=%.2f dB" % (
            v, i, v - i, a.target_db, gain))

if __name__ == "__main__":
    main()
