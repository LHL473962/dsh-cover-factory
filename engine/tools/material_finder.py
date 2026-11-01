# -*- coding: utf-8 -*-
"""素材搜索 + 自动音域分析：本地库扫描（含 ncm 解密）+ 可选 B 站 BV 下载，
逐个分离、算音高范围，输出候选表与是否补足目标音域。用户不需要自己筛选。

CLI:
  python material_finder.py --target <目标人声.wav> --roots "D:/CloudMusic" [--bvid BV1xx BV2yy] \
      [--top 6] [--out candidates.json] [--json]
"""
import argparse, glob, json, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from assets import audio, analysis

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"
MSST = r"D:/Apps/MSST/01_人声分离_MSST-GUI"
PY_MSST = MSST + "/env/python.exe"
YTDLP = r"D:/Apps/dsh-soundcheck-src/engine/venv/Scripts/yt-dlp.exe"
NCDUMP_PY = r"D:/Apps/Python311/python.exe"
BS317 = ("bs_roformer", "configs/model_bs_roformer_ep_317_sdr_12.9755.yaml",
         "pretrain/model_bs_roformer_ep_317_sdr_12.9755.ckpt")
AUDIO_EXT = (".mp3", ".flac", ".wav", ".m4a", ".ncm")

def stats_vocal(p):
    x = audio.decode(FF, p, sr=22050, channels=1).astype(np.float32)
    f = np.array(analysis.f0_track(x, 22050)); v = f[np.isfinite(f) & (f > 60)]
    if len(v) < 50: return None
    return {"p5": round(float(np.percentile(v, 5))), "p50": round(float(np.percentile(v, 50))),
            "p95": round(float(np.percentile(v, 95))), "p99": round(float(np.percentile(v, 99))), "n": int(len(v))}

def decode_ncm(src, out_wav):
    tmp = tempfile.mktemp(suffix="")
    subprocess.run([NCDUMP_PY, "-c", "import ncmdump,sys; ncmdump.dump(sys.argv[1], sys.argv[2], skip=False)", src, tmp],
                   capture_output=True)
    if not os.path.isfile(tmp): return False
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", tmp, "-ar", "44100", "-ac", "2", out_wav],
                   capture_output=True)
    os.remove(tmp)
    return os.path.isfile(out_wav)

def separate(vocal_out_dir, wav_path):
    inp = os.path.join(vocal_out_dir, "in"); os.makedirs(inp, exist_ok=True)
    base = os.path.basename(wav_path)
    shutil.copyfile(wav_path, os.path.join(inp, base))
    mt, cfg, ck = BS317
    subprocess.run([PY_MSST, "inference.py", "--model_type", mt, "--config_path", cfg,
                    "--start_check_point", ck, "--input_folder", inp, "--store_dir", os.path.join(vocal_out_dir, "stems"),
                    "--disable_detailed_pbar", "--extract_instrumental"], cwd=MSST, capture_output=True)
    for root, _, files in os.walk(os.path.join(vocal_out_dir, "stems")):
        for f in files:
            if f.lower().startswith("vocals") and f.endswith(".wav"):
                return os.path.join(root, f)
    return None

def download_bv(bvid, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    r = subprocess.run([YTDLP, "-f", "bestaudio", "-o", out_dir + "/%(title)s.%(ext)s", "--no-playlist",
                        "https://www.bilibili.com/video/" + bvid], capture_output=True, text=True)
    for f in os.listdir(out_dir):
        if f.lower().endswith((".m4a", ".webm", ".mp3", ".m4a")):
            return os.path.join(out_dir, f)
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--roots", nargs="*", default=[])
    ap.add_argument("--bvid", nargs="*", default=[])
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "material_finder"))
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    t = stats_vocal(a.target)
    if not t: print("目标读取失败"); sys.exit(2)
    need_hi = t["p95"] + 20; need_lo = t["p5"] - 20
    cands = []
    for r in a.roots:
        for p in glob.glob(r + "/**/*", recursive=True):
            if p.lower().endswith(AUDIO_EXT) and os.path.isfile(p):
                cands.append(("local", p))
    for b in a.bvid:
        cands.append(("bili", b))
    results = []
    for kind, item in cands:
        name = os.path.basename(item) if kind == "local" else item
        wav = os.path.join(a.work, name + ".wav")
        os.makedirs(a.work, exist_ok=True)
        try:
            if kind == "local" and item.lower().endswith(".ncm"):
                ok = decode_ncm(item, wav)
            elif kind == "local":
                subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", item, "-ar", "44100", "-ac", "2", wav], capture_output=True)
                ok = os.path.isfile(wav)
            else:
                d = os.path.join(a.work, item); got = download_bv(item, d)
                if got:
                    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", got, "-ar", "44100", "-ac", "2", wav], capture_output=True)
                ok = os.path.isfile(wav)
            if not ok:
                results.append({"name": name, "error": "decode"}); continue
            voc = separate(os.path.join(a.work, "sep_" + name.replace("/", "_")), wav)
            st = stats_vocal(voc) if voc else None
            if st:
                st.update({"name": name, "kind": kind, "hi_ok": st["p95"] >= need_hi, "lo_ok": st["p5"] <= need_lo})
                results.append(st)
            else:
                results.append({"name": name, "error": "no_vocal"})
        except Exception as e:  # noqa: BLE001
            results.append({"name": name, "error": str(e)[:120]})
    results.sort(key=lambda r: r.get("p95", 0), reverse=True)
    out = {"target": t, "required": {"p5": round(need_lo), "p95": round(need_hi)},
           "candidates": results[:a.top]}
    if a.out: open(a.out, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
    if a.json: print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("目标 p95", t["p95"], "需要 >=", round(need_hi))
        for r in out["candidates"]:
            print(" ", r.get("name"), r.get("p95", "-"), "OK" if r.get("hi_ok") else "")

if __name__ == "__main__":
    main()
