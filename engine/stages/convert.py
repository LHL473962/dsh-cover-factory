# -*- coding: utf-8 -*-
"""stage 6 convert：旧版 RVC 换声（tools/infer_cli.py，rmvpe + model.pth + index）。

cwd 必须是 rvc 根目录；模型目录通过 weight_root 环境变量喂给 get_vc，
所以不用往 old_rvc/assets 里拷任何东西。
"""

import os

import numpy as np

from assets import audio, commands
from stages import base

STAGE = "convert"


def _rms_db(ff, path):
    x = audio.decode(ff, path, 44100, 1)
    if x is None or len(x) == 0:
        return None
    return 20.0 * float(np.log10(float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2))) + 1e-12))


def run(job, cfg):
    tgt = base.target_entry(job)
    if not tgt:
        return base.result("skipped", [], {"reason": "no-target"}, reason="没有目标曲（job.target 为空）：只能跑到 train")

    stem = os.path.splitext(os.path.basename(tgt["raw"]))[0]
    vocals = base.work(job, "stems", stem, "vocals.wav")
    if not base.file_ok(vocals):
        return base.result("failed", [], {}, error="目标曲人声 stem 缺失：先跑 separate（" + vocals + "）")

    model_id = base.model_id(job, cfg)
    model_dir = base.work(job, "model", model_id)
    opts = job.get("options") or {}
    model_pth = os.path.join(model_dir, "model.pth")
    model_idx = os.path.join(model_dir, "model.index")
    # 模型解析顺序：显式 reuseModel（--reuse-bank）> train 写的 pointer（model.json）> job 内 model.pth
    p_meta = base.read_json(os.path.join(model_dir, "model.json"), {}) or {}
    reuse_pth = opts.get("reuseModel") if isinstance(opts.get("reuseModel"), str) else ""
    if reuse_pth and base.file_ok(reuse_pth):
        model_pth = reuse_pth
        reuse_idx = str(opts.get("reuseIndex") or "")
        model_idx = reuse_idx if base.file_ok(reuse_idx) else os.path.splitext(reuse_pth)[0] + ".index"
    elif base.file_ok(p_meta.get("modelPth") or ""):
        model_pth = str(p_meta["modelPth"])
        p_idx = str(p_meta.get("indexPath") or "")
        model_idx = p_idx if base.file_ok(p_idx) else os.path.splitext(model_pth)[0] + ".index"
    if not base.file_ok(model_pth):
        return base.result("failed", [], {}, error="没有训练好的模型：先跑 train（" + model_pth + "）")

    ccfg = cfg.get("convert") or {}
    pitch = base.clamp_int(opts.get("shift", 0), -12, 12)
    out = base.work(job, "convert", "vocal.wav")
    params = {
        "pitch": pitch,
        "f0_method": ccfg.get("f0_method", "rmvpe"),
        "index_rate": ccfg.get("index_rate", 0.75),
        "protect": ccfg.get("protect", 0.33),
        "model": model_pth,
        "modelMtime": os.path.getmtime(model_pth),
    }
    target_vocals = base.work(job, "stems", stem, "vocals.wav")
    inputs = [vocals, model_pth] + ([target_vocals] if base.file_ok(target_vocals) else []) + (
        [model_idx] if base.file_ok(model_idx) else [])

    if base.recon(job, STAGE, inputs, params, [out]):
        base.log("convert: 产物已存在且输入未变，跳过")
        return base.result("skipped", [base.rel(job, out)], dict(base.prev_metrics(job, STAGE)), reason="产物已存在且输入未变")

    paths = cfg.get("paths") or {}
    rvc_root = paths.get("rvc_root") or ""
    missing = []
    if not rvc_root or not os.path.isdir(rvc_root):
        missing.append("paths.rvc_root=" + str(rvc_root))
    elif not os.path.isfile(os.path.join(rvc_root, "tools", "infer_cli.py")):
        missing.append("infer_cli.py 不存在: " + os.path.join(rvc_root, "tools", "infer_cli.py"))
    rvc_py = paths.get("rvc_python") or ""
    if not rvc_py or not os.path.isfile(rvc_py):
        missing.append("paths.rvc_python=" + str(rvc_py))
    if missing:
        return base.result("failed", [], {}, error="换声依赖缺失：" + "；".join(missing))

    base.ensure_dir(os.path.dirname(out))
    device = opts.get("device") or ccfg.get("device")
    cmd = commands.rvc_convert_cmd(
        cfg, os.path.basename(model_pth), model_idx if base.file_ok(model_idx) else "",
        vocals, out, pitch=pitch, device=device,
    )
    res = base.run_cmd(
        cmd, cwd=rvc_root,
        env=commands.rvc_env(cfg, os.path.dirname(model_pth)),
        timeout=float(ccfg.get("timeout_sec", 3600)),
    )
    if res["returncode"] != 0:
        return base.result(
            "failed", [], {"pitch": pitch, "elapsed_sec": res["elapsed_sec"]},
            error="换声失败（exit=%s%s）：%s" % (
                res["returncode"], "，超时" if res["timed_out"] else "", res["tail"][-1500:],
            ),
        )
    if not base.file_ok(out):
        return base.result("failed", [], {"pitch": pitch}, error="换声命令成功但没有产物: " + out)

    ff = audio.find_ffmpeg(paths)
    align_db = None
    if base.file_ok(target_vocals):
        try:
            r_t = _rms_db(ff, target_vocals)
            r_c = _rms_db(ff, out)
            if r_t is not None and r_c is not None:
                align_db = float(r_t) - float(r_c)
                if abs(align_db) > 0.05:
                    tmp = out + ".align.wav"
                    res_a = base.run_cmd(
                        [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", out,
                         "-af", "volume=%.3fdB" % align_db, "-c:a", "pcm_s16le", tmp],
                        timeout=float(ccfg.get("timeout_sec", 3600)),
                    )
                    if res_a["returncode"] == 0 and base.file_ok(tmp):
                        os.replace(tmp, out)
                        base.log("convert: 人声电平对齐 %+.2f dB（与目标 vocals 等 RMS）" % align_db)
                    else:
                        base.log("convert: 警告：人声电平对齐失败（%s）" % res_a.get("tail", "")[-200:])
        except Exception as exc:  # noqa: BLE001
            base.log("convert: 警告：人声电平对齐异常：%s" % exc)

    info = audio.probe(ff, out)
    metrics = {
        "pitch": pitch,
        "durationSec": info.get("duration_sec"),
        "indexRate": ccfg.get("index_rate", 0.75),
        "vocalAlignDb": round(float(align_db), 3) if align_db is not None else None,
        "elapsed_sec": res["elapsed_sec"],
    }
    base.log("convert: 完成（%.1fs）" % (info.get("duration_sec") or 0))
    return base.result("done", [base.rel(job, out)], metrics, input_hash=base.sig(job, inputs, params))
