# -*- coding: utf-8 -*-
"""stage 3 clean：入选参考曲人声清洗（去直流/高通 45Hz + 掐头去尾静音）。

阈值沿用 tuning 里 -40 ~ -45dB 的处理习惯（phrase_plan.py 的 voiced 阈值 -40dB）。
"""

import os

from assets import audio, commands
from stages import base

STAGE = "clean"


def _kept_songs(job):
    rep = base.read_json(base.work(job, "select", "report.json"), None)
    if not rep:
        return None, None
    kept = [s for s in (rep.get("songs") or []) if s.get("role") == "ref" and s.get("kept")]
    return rep, kept


def run(job, cfg):
    rep, kept = _kept_songs(job)
    if rep is None:
        return base.result("failed", [], {}, error="没有 select/report.json：先跑 select")
    if not kept:
        return base.result("failed", [], {}, error="没有入选参考曲：先看 select 报告")

    clean_dir = base.work(job, "clean")
    base.ensure_dir(clean_dir)
    params = {"filter": commands.clean_filter(cfg)}
    inputs = [base.resolve(job, s["vocals"]) for s in kept]
    outputs = [os.path.join(clean_dir, (s.get("id") or "ref") + ".wav") for s in kept]

    if base.recon(job, STAGE, inputs, params, outputs):
        base.log("clean: 产物已存在且输入未变，跳过")
        return base.result("skipped", [base.rel(job, p) for p in outputs], dict(base.prev_metrics(job, STAGE)), reason="产物已存在且输入未变")

    ff = audio.find_ffmpeg(cfg.get("paths"))
    timeout = float((cfg.get("clean") or {}).get("timeout_sec", 900))
    durations = {}
    for s, src, dst in zip(kept, inputs, outputs):
        base.log("clean: %s -> %s" % (os.path.basename(src), os.path.basename(dst)))
        res = base.run_cmd(commands.clean_cmd(ff, cfg, src, dst), timeout=timeout)
        if res["returncode"] != 0 or not base.file_ok(dst):
            return base.result("failed", [], {}, error="清洗失败（%s）：%s" % (s.get("name"), res["tail"][-800:]))
        info = audio.probe(ff, dst)
        dur = info.get("duration_sec") or 0.0
        if dur < 1.0:
            return base.result(
                "failed", [], {},
                error="清洗后几乎没声音了（%s: %.2fs）：人声 stem 可能为空 / 静音门限过严（clean.silence_db）" % (
                    s.get("name"), dur),
            )
        durations[s.get("id")] = dur

    total = round(sum(v for v in durations.values() if v), 2)
    return base.result(
        "done", [base.rel(job, p) for p in outputs],
        {"files": len(outputs), "total_duration_sec": total, "durations": durations},
        input_hash=base.sig(job, inputs, params),
    )
