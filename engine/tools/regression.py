# -*- coding: utf-8 -*-
"""回归集：固定歌曲 + 固定指标基线对比。CLI:
python regression.py --manifest manifest.json [--update] [--json out.json]

manifest 格式:
{"songs":[{"name":"kataomoi","vocal":"...","inst":"...","mix":"...","baseline":"baseline_kataomoi.json"}]}
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS = os.path.join(HERE, "metrics.py")
THRESH = {
    "flatness_rel": 0.10,   # 平坦度允许下降 10%
    "hf_abs_db": 1.0,       # 8-16k 允许下降 1 dB
    "ratio_abs_db": 0.5,
    "peak_max_db": -1.0,
    "voiced_abs": 0.05,
}

def run_metrics(args):
    out = subprocess.run([sys.executable, METRICS] + args, capture_output=True, text=True)
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)

def compare(base, cur):
    fails, warns = [], []
    def get(d, *ks):
        for k in ks:
            if not isinstance(d, dict) or k not in d: return None
            d = d[k]
        return d
    for section in ("vocal", "mix"):
        b, c = base.get(section), cur.get(section)
        if not b or not c: continue
        bf, cf = b.get("flatness"), c.get("flatness")
        if bf and cf and cf < bf*(1-THRESH["flatness_rel"]):
            fails.append(f"{section}.flatness {bf}->{cf}")
        for band in ("b4_8k", "b8_16k"):
            bb, cb = b.get(band), c.get(band)
            if bb is not None and cb is not None and cb < bb - THRESH["hf_abs_db"]:
                fails.append(f"{section}.{band} {bb}->{cb}")
        pk = c.get("peak")
        if pk is not None and pk > THRESH["peak_max_db"]:
            fails.append(f"{section}.peak {pk}")
    bv, cv = get(base,"vocal"), get(cur,"vocal")
    if bv and cv:
        if bv.get("voiced") and cv.get("voiced",0) < bv["voiced"] - THRESH["voiced_abs"]:
            fails.append(f"vocal.voiced {bv['voiced']}->{cv['voiced']}")
        if bv.get("ratioDb") is not None and cv.get("ratioDb") is not None:
            if abs(cv["ratioDb"] - bv["ratioDb"]) > THRESH["ratio_abs_db"]:
                fails.append(f"vocal.ratioDb {bv['ratioDb']}->{cv['ratioDb']}")
    return fails, warns

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--update", action="store_true", help="用当前结果覆盖基线")
    ap.add_argument("--json")
    a = ap.parse_args()
    man = json.load(open(a.manifest, encoding="utf-8-sig"))
    report = {}
    ok = True
    for song in man.get("songs", []):
        name = song.get("name", "?")
        args = []
        if song.get("vocal"): args += ["--vocal", song["vocal"]]
        if song.get("inst"): args += ["--inst", song["inst"]]
        if song.get("mix"): args += ["--mix", song["mix"]]
        cur = run_metrics(args)
        if not cur:
            report[name] = {"status": "ERROR"}
            ok = False
            continue
        bpath = song.get("baseline")
        if a.update or not (bpath and os.path.isfile(bpath)):
            if bpath:
                os.makedirs(os.path.dirname(bpath), exist_ok=True)
                open(bpath, "w", encoding="utf-8").write(json.dumps(cur, ensure_ascii=False, indent=2))
            report[name] = {"status": "BASELINE", "metrics": cur}
            continue
        base = json.load(open(bpath, encoding="utf-8-sig"))
        fails, warns = compare(base, cur)
        status = "PASS" if not fails else "FAIL"
        ok = ok and not fails
        report[name] = {"status": status, "fails": fails, "warns": warns, "metrics": cur}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        open(a.json, "w", encoding="utf-8").write(text)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
