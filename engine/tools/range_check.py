# -*- coding: utf-8 -*-
"""音域覆盖检查：素材库人声 f0 分布 vs 目标曲，判断能否覆盖。
CLI: python range_check.py --target T.wav --materials DIR_OR_WAV [--margin 30] [--json]
输出：每首 p5/p50/p95；合并覆盖；结论 OK/WARN/FAIL
"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from assets import audio, analysis

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"

def stats(p):
    x = audio.decode(FF, p, sr=22050, channels=1).astype(np.float32)
    f = np.array(analysis.f0_track(x, 22050))
    v = f[np.isfinite(f) & (f > 60)]
    if len(v) < 50:
        return None
    return {"p5": round(float(np.percentile(v, 5))), "p50": round(float(np.percentile(v, 50))),
            "p95": round(float(np.percentile(v, 95))), "p99": round(float(np.percentile(v, 99))),
            "n": int(len(v))}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--materials", required=True, help="目录或单个 wav")
    ap.add_argument("--margin", type=float, default=20.0, help="要求素材 p95 ≥ 目标 p95 + margin Hz")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    mats = []
    if os.path.isdir(a.materials):
        for p in glob.glob(a.materials + "/**/*.wav", recursive=True):
            if os.path.basename(p).lower().startswith(("vocals", "vocal", "dry", "noreverb")):
                mats.append(p)
        if not mats:
            mats = glob.glob(a.materials + "/**/*.wav", recursive=True)
    else:
        mats = [a.materials]
    target = stats(a.target)
    rows = []
    for p in mats:
        s = stats(p)
        if s: rows.append({"name": os.path.basename(os.path.dirname(p)) or os.path.basename(p), **s})
    if not rows or not target:
        print("素材或目标读取失败"); sys.exit(2)
    need_hi = target["p95"] + a.margin
    need_lo = target["p5"] - a.margin
    cover_hi = max(r["p95"] for r in rows)
    cover_lo = min(r["p5"] for r in rows)
    verdict = "OK" if (cover_hi >= need_hi and cover_lo <= need_lo) else ("WARN" if cover_hi >= target["p95"] else "FAIL")
    out = {"target": target, "materials": rows, "coverage": {"p5": cover_lo, "p95": cover_hi},
           "required": {"p5": round(need_lo), "p95": round(need_hi)}, "verdict": verdict}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("目标 ", target)
        for r in rows:
            print("  素材", r["name"], "p5", r["p5"], "p50", r["p50"], "p95", r["p95"])
        print("合并覆盖 p5", cover_lo, "p95", cover_hi, "| 需要 p5<=", round(need_lo), "p95>=", round(need_hi), "| 结论", verdict)
    sys.exit(0 if verdict == "OK" else 1)

if __name__ == "__main__":
    main()
