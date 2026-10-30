# -*- coding: utf-8 -*-
"""stage 1 separate

调用 MSST 的 inference.py（mel_band_roformer + config_vocals_mel_band_roformer_kim.yaml
+ pretrain/MelBandRoformer_kim.ckpt），产出
work/stems/<song>/{vocals.wav,instrumental.wav}。
"""

import os

from assets import commands
from stages import base

STAGE = "separate"


def _plan(job):
    plan = []
    for raw in base.raw_files(job):
        stem = os.path.splitext(os.path.basename(raw))[0]
        d = base.work(job, "stems", stem)
        plan.append({
            "raw": raw,
            "vocals": os.path.join(d, "vocals.wav"),
            "instrumental": os.path.join(d, "instrumental.wav"),
        })
    return plan


def _missing_deps(cfg):
    p = cfg.get("paths") or {}
    missing = []
    root = p.get("msst_root") or ""
    if not root or not os.path.isdir(root):
        missing.append("paths.msst_root=" + str(root))
    elif not os.path.isfile(os.path.join(root, "inference.py")):
        missing.append("MSST inference.py 不存在: " + os.path.join(root, "inference.py"))
    py = p.get("msst_python") or p.get("python") or ""
    if not py or not os.path.isfile(py):
        missing.append("paths.msst_python=" + str(py))
    cfgp = p.get("msst_config") or ""
    if not cfgp or not os.path.isfile(cfgp):
        missing.append("分离 config 不存在: " + str(cfgp))
    ckpt = p.get("msst_checkpoint") or ""
    if not ckpt or not os.path.isfile(ckpt):
        missing.append("分离权重不存在: " + str(ckpt))
    return missing


def run(job, cfg):
    raw_dir = base.work(job, "raw")
    stems_dir = base.work(job, "stems")
    plan = _plan(job)
    if not plan:
        return base.result("failed", [], {}, error="work/raw 里没有素材：先跑 fetch")

    outs = []
    for item in plan:
        outs.extend([item["vocals"], item["instrumental"]])
    params = {"model_type": (cfg.get("separate") or {}).get("model_type", "mel_band_roformer"), "extract_instrumental": True}
    inputs = [item["raw"] for item in plan]

    if base.recon(job, STAGE, inputs, params, outs):
        base.log("separate: 产物已存在且输入未变，跳过")
        return base.result(
            "skipped", [base.rel(job, p) for p in outs],
            dict(base.prev_metrics(job, STAGE) or {"songs": len(plan)}),
            reason="产物已存在且输入未变",
        )

    missing = _missing_deps(cfg)
    if missing:
        return base.result("failed", [], {}, error="MSST 分离依赖缺失：" + "；".join(missing))

    base.ensure_dir(stems_dir)
    cmd = commands.separate_cmd(cfg, raw_dir, stems_dir)
    timeout = float((cfg.get("separate") or {}).get("timeout_sec", 7200))
    res = base.run_cmd(cmd, cwd=(cfg.get("paths") or {}).get("msst_root"), env=base.child_env(), timeout=timeout)
    if res["returncode"] != 0:
        return base.result(
            "failed", [], {"songs": len(plan), "elapsed_sec": res["elapsed_sec"]},
            error="MSST 分离失败（exit=%s%s）：%s" % (
                res["returncode"], "，超时" if res["timed_out"] else "", res["tail"][-1200:],
            ),
        )

    absent = [p for p in outs if not base.file_ok(p)]
    if absent:
        return base.result(
            "failed", [], {"songs": len(plan)},
            error="分离命令成功但缺少产物（检查 MUSDB 输出结构）：" + "；".join(absent[:6]),
        )

    base.log("separate: 完成 %d 首（%d 个 stem）" % (len(plan), len(outs)))
    return base.result(
        "done", [base.rel(job, p) for p in outs],
        {"songs": len(plan), "stems": len(outs), "elapsed_sec": res["elapsed_sec"]},
        input_hash=base.sig(job, inputs, params),
    )
