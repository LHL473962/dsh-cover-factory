#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""匿名 A/B 试听：两个声库 + 同一目标 stems + 同一混音链。

强制约束：
- 同伴奏、同混音链；
- 每版人声按 ratio_src 对齐（±0.2 dB），先自检，不通过拒绝输出；
- 同响度（-14 LUFS 目标）；
- 输出匿名 A.wav / B.wav，bank 映射只写 mapping.json，不打印。
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys

import numpy as np

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)

from assets import analysis, audio, commands  # noqa: E402
from setup import checks  # noqa: E402
from stages import base, mix as mix_stage  # noqa: E402


def _rms_db(ff, path):
    x = audio.decode(ff, path, 44100, 1)
    if x is None or len(x) == 0:
        raise RuntimeError("空音频")
    return 20.0 * float(np.log10(float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2))) + 1e-12))


def _run(cmd, cwd=None, env=None, timeout=3600):
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        tail = ((p.stderr or b"") + (p.stdout or b"")).decode("utf-8", "replace")[-800:]
        raise RuntimeError(tail)
    return p


def _convert(cfg, vocal_in, pth, index, out, pitch=0, device=None):
    paths = cfg.get("paths") or {}
    root = paths.get("rvc_root") or ""
    py = paths.get("rvc_python") or ""
    if not (root and os.path.isdir(root) and os.path.isfile(py)):
        raise RuntimeError("RVC 路径/解释器未配置")
    base.ensure_dir(os.path.dirname(out))
    cmd = commands.rvc_convert_cmd(cfg, os.path.basename(pth), index or "", vocal_in, out,
                                   pitch=int(pitch), device=device)
    _run(cmd, cwd=root, env=commands.rvc_env(cfg, os.path.dirname(pth)))


def _apply_gain(ff, src, dst, gain_db):
    base.ensure_dir(os.path.dirname(dst))
    _run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
          "-af", "volume=%.3fdB" % float(gain_db), "-c:a", "pcm_s16le", dst])


def _align_vocal(ff, conv, inst_rms, ratio_src, dst):
    conv_rms = _rms_db(ff, conv)
    gain = (float(inst_rms) + float(ratio_src)) - conv_rms
    cur = conv
    for i in range(3):
        target = dst if i == 0 else dst + ".fix%d.wav" % i
        _apply_gain(ff, cur, target, gain)
        actual = _rms_db(ff, target) - float(inst_rms)
        delta = actual - float(ratio_src)
        if abs(delta) <= 0.2:
            if target != dst:
                os.replace(target, dst)
            return float(actual), float(delta), float(gain)
        gain += float(ratio_src) - actual
        cur = target
    raise RuntimeError("A/B 人声比例对齐失败（>0.2 dB），拒绝输出")


def _master(ff, cfg, vocal, inst, dst, ir, shift):
    m = cfg.get("mix") or {}
    premix = dst + ".premix.wav"
    cmd, _graph, _ir = commands.mix_render_cmd(ff, cfg, vocal, inst, premix, ir_path=ir, shift=int(shift or 0))
    _run(cmd)
    target = float(m.get("target_lufs", -14.0))
    headroom = float(m.get("headroom_db", 1.0))
    loud = audio.loudness(ff, premix)
    if loud.get("lufs") is None:
        raise RuntimeError("读不到混音响度")
    gain = target - float(loud["lufs"]) - headroom
    lufs = None
    try:
        for _ in range(3):
            _run(commands.mix_master_cmd(ff, cfg, premix, dst, gain))
            loud2 = audio.loudness(ff, dst)
            lufs = loud2.get("lufs")
            if lufs is None or abs(float(lufs) - target) <= 0.25:
                break
            gain += target - float(lufs)
    finally:
        try:
            os.remove(premix)
        except OSError:
            pass
    return lufs


def _resolve_inputs(args, cfg):
    job = None
    if args.job:
        job = json.load(open(args.job, "r", encoding="utf-8"))
        job["_workdir"] = os.path.dirname(os.path.abspath(args.job))
        tgt = base.target_entry(job)
        if not tgt:
            raise RuntimeError("job 没有目标曲")
        stem = os.path.splitext(os.path.basename(tgt["raw"]))[0]
        vocals = base.work(job, "stems", stem, "vocals.wav")
        inst = base.work(job, "stems", stem, "instrumental.wav")
    else:
        vocals = args.vocals or ""
        inst = args.instrumental or ""
    if not (vocals and os.path.isfile(vocals)):
        raise RuntimeError("目标曲人声 stem 不存在")
    if not (inst and os.path.isfile(inst)):
        raise RuntimeError("目标曲伴奏 stem 不存在")
    ir = (cfg.get("paths") or {}).get("room_ir") or ""
    if ir:
        ir = os.path.expanduser(ir)
        if not os.path.isabs(ir):
            ir = os.path.normpath(os.path.join(ENGINE, ir))
    if not (ir and os.path.isfile(ir)):
        ir = ""
    return vocals, inst, ir, job


def main(argv=None):
    ap = argparse.ArgumentParser(description="匿名 A/B 对比：两个声库、同伴奏、同链、比例对齐")
    ap.add_argument("--a-pth", required=True)
    ap.add_argument("--a-index", default="")
    ap.add_argument("--b-pth", required=True)
    ap.add_argument("--b-index", default="")
    ap.add_argument("--job", default="", help="从 job.json 取目标曲 stems（与 --vocals/--instrumental 二选一）")
    ap.add_argument("--vocals", default="")
    ap.add_argument("--instrumental", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default="")
    ap.add_argument("--pitch", type=int, default=0)
    ap.add_argument("--shift", type=int, default=0)
    ap.add_argument("--device", default="")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    try:
        cfg = checks.load_config(args.config or None)
        ff = audio.find_ffmpeg(cfg.get("paths"))
        vocals, inst, ir, _job = _resolve_inputs(args, cfg)
        src_ratio = _rms_db(ff, vocals) - _rms_db(ff, inst)
        inst_rms = _rms_db(ff, inst)
        out = os.path.abspath(args.out)
        if os.path.isdir(out) and os.listdir(out) and not args.force:
            raise RuntimeError("输出目录非空，加 --force 或换目录")
        base.ensure_dir(out)
        work = os.path.join(out, ".work")
        if os.path.isdir(work):
            shutil.rmtree(work)
        os.makedirs(work, exist_ok=True)

        banks = [
            {"pth": os.path.abspath(args.a_pth), "index": os.path.abspath(args.a_index) if args.a_index else ""},
            {"pth": os.path.abspath(args.b_pth), "index": os.path.abspath(args.b_index) if args.b_index else ""},
        ]
        for b in banks:
            if not os.path.isfile(b["pth"]):
                raise RuntimeError("声库 pth 不存在")
            if b["index"] and not os.path.isfile(b["index"]):
                raise RuntimeError("声库 index 不存在")
        rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
        rng.shuffle(banks)

        prepared = []
        for letter, bank in zip(("A", "B"), banks):
            conv = os.path.join(work, letter + ".convert.wav")
            aligned = os.path.join(work, letter + ".aligned.wav")
            _convert(cfg, vocals, bank["pth"], bank["index"], conv, pitch=args.pitch, device=args.device or None)
            ratio_mix, delta, gain = _align_vocal(ff, conv, inst_rms, src_ratio, aligned)
            if abs(delta) > 0.2:
                raise RuntimeError("比例自检失败：未输出 A/B")
            prepared.append((letter, bank, aligned, ratio_mix, delta, gain))

        results = {}
        for letter, bank, aligned, ratio_mix, delta, gain in prepared:
            dst = os.path.join(out, letter + ".wav")
            lufs = _master(ff, cfg, aligned, inst, dst, ir, args.shift)
            x = audio.decode(ff, dst, 44100, 1)
            gaps = analysis.gap_rate(x, 44100)
            c0, cbest, lag = mix_stage._best_corr(ff, dst, inst)
            results[letter] = {
                "file": letter + ".wav",
                "ratio": round(float(ratio_mix), 3),
                "ratioDelta": round(float(delta), 3),
                "gainDb": round(float(gain), 3),
                "lufs": lufs,
                "gap": gaps.get("per_min"),
                "corr": round(float(cbest), 4),
                "corr0": round(float(c0), 4),
                "lag": int(lag),
            }
            print("%s: file=%s.wav ratio=%+.2f dB (Δ %+.2f) lufs=%s gap=%s/min corr=%.3f" % (
                letter, letter, ratio_mix, delta, lufs, gaps.get("per_min"), cbest))
        lufs_vals = [float(v["lufs"]) for v in results.values() if v.get("lufs") is not None]
        if len(lufs_vals) == 2 and abs(lufs_vals[0] - lufs_vals[1]) > 0.25:
            raise RuntimeError("A/B 响度未对齐（>0.25dB）")
        mapping = {letter: bank for letter, bank, _a, _r, _d, _g in prepared}
        with open(os.path.join(out, "mapping.json"), "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        shutil.rmtree(work, ignore_errors=True)
        summary = {"ok": True, "ratioSrc": round(float(src_ratio), 3), "outputs": results, "mapping": "mapping.json"}
        print("ratio_src=%+.2f dB；A/B 已输出（映射见 mapping.json，未打印）" % src_ratio)
        return summary
    except Exception as exc:  # noqa: BLE001
        print("AB_FAILED: " + str(exc)[:800], file=sys.stderr)
        return None


if __name__ == "__main__":
    res = main()
    sys.exit(0 if res else 1)
