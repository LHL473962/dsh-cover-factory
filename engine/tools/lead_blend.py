# -*- coding: utf-8 -*-
"""主唱优先混合：用 karaoke 模型分离目标曲主唱，与普通分离人声按比例混合。

用法：python lead_blend.py --raw <目标曲原音频> --vocals <普通分离人声.wav> --out <输出.wav> [--ratio 0.75]
产物：混合人声（0.75 主唱 + 0.25 普通人声，按峰值归一）
"""
import argparse, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, soundfile as sf
from assets import audio

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"
MSST = r"D:/Apps/MSST/01_人声分离_MSST-GUI"
PY_MSST = MSST + "/env/python.exe"
KARA = ("mel_band_roformer", "configs/config_karaoke_becruily.yaml",
        "pretrain/mel_band_roformer_karaoke_becruily.ckpt")

def run_karaoke(raw):
    tmp = tempfile.mkdtemp(prefix="leadblend_")
    inp = os.path.join(tmp, "in"); os.makedirs(inp)
    dst = os.path.join(inp, "song.wav")
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", raw, "-ar", "44100", "-ac", "2", dst], check=True)
    out = os.path.join(tmp, "sep")
    env = dict(os.environ)  # MSST_ENV_MARK
    env.setdefault("HOME", "C:/Users/a1599")
    env.setdefault("USERPROFILE", "C:/Users/a1599")
    env.setdefault("APPDATA", "C:/Users/a1599/AppData/Roaming")
    mt, cfg, ck = KARA
    subprocess.run([PY_MSST, "inference.py", "--model_type", mt, "--config_path", cfg,
                    "--start_check_point", ck, "--input_folder", inp, "--store_dir", out,
                    "--disable_detailed_pbar", "--extract_instrumental"],
                   cwd=MSST, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    lead = None
    for root, _, files in os.walk(out):
        for f in files:
            if f.lower().startswith("vocals") and f.lower().endswith(".wav"):
                lead = os.path.join(root, f)
    if not lead:
        raise RuntimeError("karaoke 分离没有输出 Vocals")
    return tmp, lead

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--vocals", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ratio", type=float, default=0.75)
    a = ap.parse_args()
    tmp, lead = run_karaoke(a.raw)
    try:
        ka = audio.decode(FF, lead, sr=44100, channels=1).astype(np.float32)
        bs = audio.decode(FF, a.vocals, sr=44100, channels=1).astype(np.float32)
        n = min(len(ka), len(bs)); ka, bs = ka[:n], bs[:n]
        mix = a.ratio * ka + (1.0 - a.ratio) * bs
        pk = float(np.abs(mix).max())
        if pk > 0.98:
            mix = mix * (0.98 / pk)
        sf.write(a.out, mix, 44100, subtype="PCM_16")
        print({"ok": True, "out": a.out, "ratio": a.ratio, "lead": os.path.basename(lead)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()