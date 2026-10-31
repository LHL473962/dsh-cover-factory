# -*- coding: utf-8 -*-
"""stage 9 export：成品 mp3 + report.md 落盘（引擎侧先写 work/export/，再复制到桌面）。

mp3 是交付格式，峰值必须按 mp3 实测验收：encode_mp3_checked 编码后测 peak，
超过 -1.0 dBFS 就整体回退 1dB 重编（最多 2 次），finalPeak 写进 job.result。
报告里的“阶段”表要显示 export=done：写报告前先把内存里的 export 状态置 done。
"""

import datetime
import os
import re
import shutil

from assets import audio
from stages import base

STAGE = "export"


def _safe(name):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" ._")
    return s or "cover"


def _output_dir(job, cfg):
    opts = job.get("options") or {}
    ecfg = cfg.get("export") or {}
    folder = str(ecfg.get("folder_name") or "翻唱工厂")
    explicit = str(opts.get("outputDir") or (cfg.get("paths") or {}).get("export_dir") or ecfg.get("desktop_dir") or "").strip()
    if explicit:
        base_dir = explicit
        if not opts.get("outputDir") and os.path.basename(os.path.normpath(explicit)) != folder:
            base_dir = os.path.join(explicit, folder)
        return base_dir
    return os.path.join(base.desktop_dir(), folder)


def _elapsed_text(job):
    created = job.get("createdAt")
    end = (job.get("result") or {}).get("completedAt") or base.now_iso()
    try:
        t0 = datetime.datetime.fromisoformat(str(created))
        t1 = datetime.datetime.fromisoformat(str(end))
        sec = max(0, int((t1 - t0).total_seconds()))
    except Exception:
        return "未测"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d 小时 %d 分" % (h, m)
    if m:
        return "%d 分 %d 秒" % (m, s)
    return "%d 秒" % s


def _select_summary(job):
    sel = base.read_json(base.work(job, "select", "report.json"), {}) or {}
    counts = sel.get("counts") or {}
    kept = counts.get("kept")
    if kept is None:
        return "未测"
    excluded = counts.get("excluded")
    names = [str(e.get("name") or "") for e in (sel.get("excluded") or []) if e.get("name")]
    text = "保留 %s 首 / 排除 %s 首" % (kept, excluded if excluded is not None else 0)
    if names:
        text += "（排除：%s）" % "、".join(names[:6])
    return text


def _train_summary(job):
    metrics = ((job.get("stages") or {}).get("train") or {}).get("metrics") or {}
    steps = metrics.get("steps") or (job.get("options") or {}).get("steps")
    epochs = metrics.get("epochs") or (job.get("options") or {}).get("epochs")
    if steps is None:
        return "未测"
    text = ("%s 步（%s epoch）" % (steps, epochs)) if epochs else ("%s 步" % steps)
    elapsed = metrics.get("trainElapsed")
    if elapsed:
        text += "，训练耗时 %s" % base.format_duration(elapsed)
        if metrics.get("secPerStepActual"):
            text += "（%.2f 秒/步）" % float(metrics["secPerStepActual"])
    return text


def _pct(v):
    if v is None:
        return "未测"
    try:
        return "%.1f%%" % (float(v) * 100.0)
    except (TypeError, ValueError):
        return "未测"


def _report_md(job, cfg, qa, mix_metrics, export_metrics):
    tgt = job.get("target_meta") or {}
    qmetrics = (qa or {}).get("metrics") or {}
    lines = [
        "# 翻唱工厂 · " + str(job.get("singer") or "未命名") + " - " + str(tgt.get("title") or tgt.get("name") or ""),
        "",
        "- jobId：" + str(job.get("id") or ""),
        "- 模式：" + str(job.get("mode") or "import"),
        "- 歌手：" + str(job.get("singer") or ""),
        "- 目标曲：" + str(tgt.get("title") or tgt.get("name") or tgt.get("source") or ""),
        "- 生成时间：" + base.now_iso(),
        "",
        "## 阶段",
        "",
        "| 阶段 | 状态 |",
        "| --- | --- |",
    ]
    for name in base.STAGES:
        rec = (job.get("stages") or {}).get(name) or {}
        lines.append("| %s | %s |" % (name, rec.get("status") or "pending"))
    lines.extend([
        "",
        "- 总耗时：" + _elapsed_text(job),
        "",
        "## 关键指标",
        "",
        "- 素材筛选：" + _select_summary(job),
        "- 分离模型：" + str((job.get("separate_model") or {}).get("id") or "未记录"),
        "- 训练步数：" + _train_summary(job),
        "- Stem 自检：" + str(((job.get("stem_check") or {}).get("verdict") or "未测")) + (
            "（" + "；".join((job.get("stem_check") or {}).get("warnings") or [])[:2] + "）"
            if (job.get("stem_check") or {}).get("warnings") else ""),
        "- EVAL_LEAK：" + (
            (lambda s: s[len("EVAL_LEAK："):] if s.startswith("EVAL_LEAK：") else s)(
                str((qa.get("evalLeak") or {}).get("warning") or "目标曲命中训练集（见 qa/report.json）"))
            if (qa.get("evalLeak") or {}).get("hit") else "未命中训练集"),
        "- 人声/伴奏比例：" + (
            "new ratioActive=%+.2f dB" % float(qa.get("ratioActive") or 0)
            if qa.get("mixPreset") == "new" else
            ("src %+.2f / mix %+.2f dB（Δ %+.2f）" % (
                float((qa.get("vocalBackingRatio") or {}).get("src") or 0),
                float((qa.get("vocalBackingRatio") or {}).get("mix") or 0),
                float((qa.get("vocalBackingRatio") or {}).get("delta") or 0))
             if (qa.get("vocalBackingRatio") or {}).get("src") is not None else "未测")),
        "- 混音预设：" + str(qa.get("mixPreset") or "old") + (
            "（不归一化，只峰值保护）" if qa.get("mixPreset") == "new" else "（归一化 -14 LUFS）"),
        "- 断音率：%s 次/分（合格线 < 12）" % (qmetrics.get("gapPerMin") if qmetrics.get("gapPerMin") is not None else "未测"),
        "- 音域覆盖：%s（合格线 >= 70%%）" % _pct(qmetrics.get("rangeCoverage")),
        "",
        "## 混音",
        "",
    ])
    for k in ("lufs", "peakDb", "shift"):
        if mix_metrics.get(k) is not None:
            lines.append("- %s: %s" % (k, mix_metrics.get(k)))
    if mix_metrics.get("polarityFixed") is not None:
        lines.append("- 极性自检：%s（lag=%s，0延迟相关=%s，对齐相关=%s）" % (
            "已反相修正 polarityFixed=true" if mix_metrics.get("polarityFixed") else "正常",
            mix_metrics.get("polarityLag"), mix_metrics.get("polarityCorr0"),
            mix_metrics.get("polarityCorr")))
    final_peak = export_metrics.get("finalPeak")
    if final_peak is not None:
        lines.append("- 编码后峰值：%s dBFS（要求 <= -1.0）" % final_peak)
    if qa:
        lines.extend(["", "## 质检：" + ("合格" if qa.get("ok") else "不合格"), ""])
        lines.append("- 响度：%s LUFS" % qmetrics.get("lufs"))
        lines.append("- 峰值（wav）：%s dBFS" % qmetrics.get("peakDb"))
        for p in qa.get("problems") or []:
            lines.append("- 问题：" + str(p))
        for s in qa.get("suggestions") or []:
            lines.append("- 建议：" + str(s))
    lines.extend([
        "",
        "---",
        "",
        "混音链：人声/伴奏 stem 原始电平（0dB/0dB）、260Hz -1.2、9k +1.6、2.8k 让位 -1、",
        "IR 1.2s 湿 30%、返回加宽 1.8x、伴奏加宽 1.15x、1.3:1 glue、等响 -14 LUFS、limiter -1.5dB（level=false）。",
        "",
    ])
    return "\n".join(lines)


def run(job, cfg):
    mix = base.work(job, "mix", "out.wav")
    if not base.file_ok(mix):
        return base.result("skipped", [], {"reason": "no-mix"}, reason="没有 work/mix/out.wav：先跑 mix")

    ecfg = cfg.get("export") or {}
    tgt = job.get("target_meta") or {}
    singer = _safe(job.get("singer") or "cover")
    tname = _safe(tgt.get("title") or tgt.get("name") or "target")
    template = str(ecfg.get("filename") or "{singer}-{target}")
    try:
        base_name = template.format(singer=singer, target=tname, song=tname)
    except Exception:
        base_name = singer + "-" + tname
    base_name = _safe(base_name)

    out_dir = base.work(job, "export")
    mp3 = os.path.join(out_dir, base_name + ".mp3")
    report_path = os.path.join(out_dir, "report.md")
    qa = base.read_json(base.work(job, "qa", "report.json"), {}) or {}
    dest_dir = _output_dir(job, cfg)
    dest_mp3 = os.path.join(dest_dir, base_name + ".mp3")
    dest_report = os.path.join(dest_dir, base_name + "-report.md")

    params = {"bitrate": ecfg.get("mp3_bitrate", "320k"), "name": base_name, "dest": dest_dir}
    qa_path = base.work(job, "qa", "report.json")
    inputs = [mix] + ([qa_path] if base.file_ok(qa_path) else [])

    if base.recon(job, STAGE, inputs, params, [mp3, report_path]):
        base.log("export: 成品已存在且输入未变，跳过")
        metrics = dict(base.prev_metrics(job, STAGE))
        metrics.update({"mp3": metrics.get("mp3") or dest_mp3, "report": metrics.get("report") or dest_report})
        return base.result("skipped", [base.rel(job, mp3), base.rel(job, report_path)], metrics, reason="产物已存在且输入未变")

    ff = audio.find_ffmpeg(cfg.get("paths"))
    base.ensure_dir(out_dir)
    min_peak = float(ecfg.get("mp3_peak_max_db", -1.0))
    encoded = audio.encode_mp3_checked(
        ff, mix, mp3,
        bitrate=ecfg.get("mp3_bitrate", "320k"),
        meta={"title": tname, "artist": singer, "comment": "dsh-cover-factory job=" + str(job.get("id") or "")},
        min_peak_db=min_peak,
        max_retries=2,
        timeout=float(ecfg.get("timeout_sec", 900)),
    )
    final_peak = encoded.get("peakDb")
    final_ok = bool(encoded.get("ok"))
    warnings = []
    if not final_ok:
        warnings.append("mp3 峰值 %s dBFS 未达到 <= %s dBFS（已回退 %s 次）" % (
            final_peak, min_peak, max(0, int(encoded.get("attempts", 1)) - 1)))
        base.log("export: " + warnings[-1])

    metrics = {
        "mp3": dest_mp3,
        "report": dest_report,
        "localMp3": base.rel(job, mp3),
        "qaOk": qa.get("ok"),
        "finalPeak": final_peak,
        "finalPeakOk": final_ok,
        "mp3PeakMaxDb": min_peak,
        "encodeRetries": max(0, int(encoded.get("attempts", 1)) - 1),
        "encodeGainDb": encoded.get("gainDb"),
        "warnings": warnings,
    }

    # 报告里的阶段表要显示 export=done（run.py 等 stage 返回后才写 job.json，这里先置内存态）
    rec = job.setdefault("stages", {}).setdefault("export", {})
    rec["status"] = "done"
    rec["finishedAt"] = base.now_iso()
    _write(report_path, _report_md(job, cfg, qa, base.prev_metrics(job, "mix"), metrics))

    try:
        base.ensure_dir(dest_dir)
        shutil.copy2(mp3, dest_mp3)
        shutil.copy2(report_path, dest_report)
    except Exception as e:  # noqa: BLE001
        warnings.append("复制到成品目录失败（引擎侧产物仍在）：" + str(e))
        base.log("export: " + warnings[-1])

    base.log("export: 完成 -> %s（finalPeak=%s dBFS，重编 %s 次）" % (
        dest_mp3, final_peak, metrics["encodeRetries"]))
    return base.result(
        "done", [base.rel(job, mp3), base.rel(job, report_path)], metrics,
        input_hash=base.sig(job, inputs, params),
    )


def _write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
