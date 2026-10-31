# -*- coding: utf-8 -*-
"""训练前清洗：去混响 + 去噪（A1）。调用本地 MSST 模型。
CLI: python preclean.py --in DIR_OR_FILE --out DIR [--dereverb] [--denoise] [--strength 1.0]
"""
import argparse, json, os, shutil, subprocess, sys, tempfile

MSST = os.environ.get("SUIS_MSST", r"D:/Apps/MSST/01_人声分离_MSST-GUI")
PY = os.path.join(MSST, "env", "python.exe")
FF = os.environ.get("SUIS_FFMPEG", r"D:/VoiceData/tools/bin/ffmpeg.exe")
DER = {"model_type": "mel_band_roformer", "config": "configs/dereverb_mel_band_roformer_anvuew.yaml",
       "ckpt": "pretrain/dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt"}
DEN = {"model_type": "mel_band_roformer", "config": "configs/model_mel_band_roformer_denoise.yaml",
       "ckpt": "pretrain/denoise_mel_band_roformer_aufr33_aggr_sdr_27.9768.ckpt"}

def _run(step, in_dir, out_dir):
    cmd = [PY, "inference.py", "--model_type", step["model_type"], "--config_path", step["config"],
           "--start_check_point", step["ckpt"], "--input_folder", in_dir, "--store_dir", out_dir,
           "--disable_detailed_pbar"]
    r = subprocess.run(cmd, cwd=MSST, capture_output=True, text=True, errors="ignore")
    if r.returncode != 0:
        raise RuntimeError(step["model_type"] + " failed: " + (r.stderr or r.stdout)[-400:])

def _find_vocals(out_dir):
    hits = []
    for root, _, files in os.walk(out_dir):
        for f in files:
            if f.lower().startswith("vocals") and f.lower().endswith(".wav"):
                hits.append(os.path.join(root, f))
    return hits

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--dereverb", action="store_true", default=True)
    ap.add_argument("--denoise", action="store_true", default=True)
    ap.add_argument("--no-dereverb", dest="dereverb", action="store_false")
    ap.add_argument("--no-denoise", dest="denoise", action="store_false")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    src_dir = a.in
    tmp = tempfile.mkdtemp(prefix="preclean_")
    cur = src_dir
    steps = []
    if a.dereverb: steps.append(("dereverb", DER))
    if a.denoise: steps.append(("denoise", DEN))
    for name, st in steps:
        out_dir = os.path.join(tmp, name)
        _run(st, cur, out_dir)
        voc = _find_vocals(out_dir)
        if not voc:
            raise RuntimeError(name + " 没有输出 vocals")
        cur = os.path.dirname(voc[0])
    files = [f for f in os.listdir(cur) if f.lower().endswith(".wav")]
    for f in files:
        shutil.copyfile(os.path.join(cur, f), os.path.join(a.out, f))
    print(json.dumps({"ok": True, "steps": [s[0] for s in steps], "files": files}, ensure_ascii=False))

if __name__ == "__main__":
    main()
