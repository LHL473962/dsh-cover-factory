# -*- coding: utf-8 -*-
"""stage 8 qa：质检。

指标与合格线（DESIGN.md）：
- preflight（stereo / 时长 / 峰值）：全过
- 响度：-13 +- 1.5 LUFS
- 断音率：< 12 次/分（移植 artifacts.gaps）
- 音域覆盖：目标曲 >= 70% 帧落在训练音域 +- 6 半音内
不合格 -> 报告标红 + 建议（加步数 / 换素材 / 整首移调）。
"""

import os

from stages import base

STAGE = "qa"


def _checks(ok_map):
    return dict(ok_map)


def run(job, cfg):
    from assets import analysis, audio

    qcfg = cfg.get("qa") or {}
    mix = base.work(job, "mix", "out.wav")
    if not base.file_ok(mix):
        return base.result("skipped", [], {"reason": "no-mix"}, reason="没有 work/mix/out.wav：先跑 mix")

    opts = job.get("options") or {}
    shift = base.clamp_int(opts.get("shift", 0), -12, 12)
    tgt = base.target_entry(job)
    target_vocals = ""
    if tgt:
        stem = os.path.splitext(os.path.basename(tgt["raw"]))[0]
        target_vocals = base.work(job, "stems", stem, "vocals.wav")

    ds_dir = base.work(job, "dataset", str(job.get("id") or "job"))
    manifest_path = os.path.join(ds_dir, "manifest.json")
    manifest = base.read_json(manifest_path, {}) or {}

    # 期望声部：options.voice > select 的 expectedVoice > dataset 中位；音域用宽区间
    voice_opt = str(opts.get("voice") or "auto").strip().lower()
    sel_rep = base.read_json(base.work(job, "select", "report.json"), {}) or {}
    if voice_opt in ("male", "female"):
        expected_voice, expected_source = voice_opt, "options.voice"
    elif sel_rep.get("expectedVoice") in ("male", "female"):
        expected_voice, expected_source = sel_rep.get("expectedVoice"), "select"
    else:
        median = (manifest.get("pitch_range") or {}).get("median")
        if median:
            expected_voice = "male" if float(median) < 200.0 else "female"
            expected_source = "dataset"
        else:
            expected_voice, expected_source = "female", "default"
    voice_ranges = qcfg.get("voice_ranges") or {}
    voice_range = voice_ranges.get(expected_voice) or ([80, 400] if expected_voice == "male" else [150, 600])

    report_path = base.work(job, "qa", "report.json")
    md_path = base.work(job, "qa", "report.md")
    params = {
        "thresholds": qcfg,
        "shift": shift,
        "pitchRange": (manifest.get("pitch_range") or {}),
        "expectedVoice": expected_voice,
        "voiceRange": list(voice_range),
    }
    inputs = [mix]
    if base.file_ok(target_vocals):
        inputs.append(target_vocals)
    if base.file_ok(manifest_path):
        inputs.append(manifest_path)

    if base.recon(job, STAGE, inputs, params, [report_path]):
        rep = base.read_json(report_path, {}) or {}
        base.log("qa: 报告已存在且输入未变，跳过")
        return base.result(
            "skipped", [base.rel(job, report_path)] + ([base.rel(job, md_path)] if base.file_ok(md_path) else []),
            rep.get("metrics") or {}, reason="产物已存在且输入未变",
        )

    ff = audio.find_ffmpeg(cfg.get("paths"))
    timeout = float(qcfg.get("timeout_sec", 900))

    target_lufs = float(qcfg.get("loudness_target", -13.0))
    tol = float(qcfg.get("loudness_tol", 1.5))
    peak_max = float(qcfg.get("peak_max_db", -0.5))
    gap_limit = float(qcfg.get("gap_per_min", 12.0))
    min_dur = float(qcfg.get("min_duration_sec", 30.0))
    semis = float(qcfg.get("range_semitones", 6.0))
    cov_min = float(qcfg.get("range_coverage", 0.7))

    info = audio.probe(ff, mix)
    loud = audio.loudness(ff, mix, timeout=timeout)
    mono = audio.decode(ff, mix, 16000, 1, timeout=timeout)
    gaps = analysis.gap_rate(mono, 16000)

    duration = info.get("duration_sec") or gaps.get("duration_sec") or 0.0
    peak = loud.get("peak_db")
    lufs = loud.get("lufs")

    checks = {}
    problems = []
    suggestions = []

    stereo_ok = info.get("channels") == 2 or "stereo" in str(info.get("layout") or "")
    checks["stereo"] = bool(stereo_ok)
    if not stereo_ok:
        problems.append("输出不是立体声（layout=%s）" % info.get("layout"))
        suggestions.append("检查混音 aformat=channel_layouts=stereo / -ac 2")

    checks["duration"] = bool(duration >= min_dur)
    if not checks["duration"]:
        problems.append("时长 %.0fs < %.0fs" % (duration, min_dur))
        suggestions.append("检查目标曲/伴奏是否截断")

    checks["peak"] = bool(peak is None or float(peak) <= peak_max)
    if not checks["peak"]:
        problems.append("峰值 %.1f dBFS > %.1f" % (float(peak), peak_max))
        suggestions.append("降低 limiter 阈值（mix.limiter_db）或减小等响增益")

    if lufs is None:
        checks["loudness"] = None
        problems.append("读不到整体响度（ebur128 无 Summary）")
    else:
        checks["loudness"] = bool(target_lufs - tol <= float(lufs) <= target_lufs + tol)
        if not checks["loudness"]:
            problems.append("响度 %.1f LUFS 不在 %.1f +- %.1f" % (float(lufs), target_lufs, tol))
            suggestions.append("调整 mix.target_lufs 或检查 limiter/等响链")

    checks["gapRate"] = bool(gaps.get("per_min", 0.0) < gap_limit)
    if not checks["gapRate"]:
        problems.append("断音率 %.1f 次/分 >= %.1f" % (gaps.get("per_min", 0.0), gap_limit))
        suggestions.append("加训练步数（当前 steps=%s）/ 换更干净的素材" % opts.get("steps"))

    pitch_range = manifest.get("pitch_range") or {}
    coverage = None
    target_f0_summary = {}
    if base.file_ok(target_vocals) and len(voice_range) >= 2:
        tf0 = analysis.f0_track(audio.decode(ff, target_vocals, 16000, 1, timeout=timeout),
                                16000, fmin=60.0)
        coverage = analysis.range_coverage(tf0, voice_range[0], voice_range[1], 0.0)
        target_f0_summary = analysis.f0_summary(tf0, male_lo=60.0, male_hi=165.0)
        checks["rangeCoverage"] = bool(coverage is not None and coverage >= cov_min)
        if not checks["rangeCoverage"]:
            problems.append("音域覆盖 %.0f%% < %.0f%%（期望声部 %s 区间 %g-%gHz）" % (
                (coverage or 0) * 100, cov_min * 100, expected_voice, voice_range[0], voice_range[1]))
            suggestions.append("目标音域偏离期望声部区间：整首移调（人声+伴奏同比例）或换素材/重训")
    else:
        checks["rangeCoverage"] = False if base.file_ok(target_vocals) else None
        problems.append("音域覆盖未测：缺少目标人声 stem 或期望音域配置")
        suggestions.append("确认目标曲人声 stem 存在、qa.voice_ranges 配置有效")

    ok = all(v is not False for v in checks.values())
    metrics = {
        "ok": ok,
        "lufs": lufs,
        "peakDb": peak,
        "durationSec": duration,
        "gapCount": gaps.get("count"),
        "gapPerMin": gaps.get("per_min"),
        "rangeCoverage": coverage,
        "shift": shift,
        "expectedVoice": expected_voice,
        "expectedSource": expected_source,
        "expectedRange": [voice_range[0], voice_range[1]],
        "pitchRange": pitch_range,
        "targetMedianF0": target_f0_summary.get("median"),
        "problems": problems,
        "suggestions": suggestions,
    }
    report = {
        "version": 1,
        "generatedAt": base.now_iso(),
        "jobId": job.get("id"),
        "file": base.rel(job, mix),
        "ok": bool(ok),
        "checks": checks,
        "metrics": metrics,
        "thresholds": {
            "loudnessTarget": target_lufs, "loudnessTol": tol, "peakMaxDb": peak_max,
            "gapPerMin": gap_limit, "minDurationSec": min_dur,
            "rangeCoverage": cov_min, "rangeSemitones": semis,
            "expectedVoice": expected_voice, "expectedRange": [voice_range[0], voice_range[1]],
        },
        "problems": problems,
        "suggestions": suggestions,
    }
    base.write_json(report_path, report)
    _write_md(md_path, job, report)

    if ok:
        base.log("qa: 合格（LUFS=%s, 断音率=%s/min, 音域覆盖=%s）" % (
            lufs, gaps.get("per_min"), coverage))
    else:
        base.log("qa: 不合格：" + "；".join(problems))

    return base.result(
        "done", [base.rel(job, report_path), base.rel(job, md_path)],
        metrics, input_hash=base.sig(job, inputs, params),
    )


def _write_md(path, job, report):
    m = report.get("metrics") or {}
    checks = report.get("checks") or {}
    lines = [
        "# 质检报告 · " + str(job.get("singer") or "未命名") + " - " + str((job.get("target_meta") or {}).get("title") or ""),
        "",
        "- jobId：" + str(job.get("id") or ""),
        "- 结论：" + ("**合格**" if report.get("ok") else "**不合格**"),
        "- 生成时间：" + str(report.get("generatedAt") or ""),
        "",
        "| 指标 | 值 | 判定 |",
        "| --- | --- | --- |",
    ]
    names = {
        "stereo": "立体声", "duration": "时长", "peak": "峰值", "loudness": "响度",
        "gapRate": "断音率", "rangeCoverage": "音域覆盖",
    }
    values = {
        "stereo": str(m.get("durationSec") or ""),
        "duration": "%.1fs" % float(m.get("durationSec") or 0),
        "peak": "%s dBFS" % m.get("peakDb"),
        "loudness": "%s LUFS" % m.get("lufs"),
        "gapRate": "%s 次/分" % m.get("gapPerMin"),
        "rangeCoverage": "%s" % (("%.0f%%" % (float(m["rangeCoverage"]) * 100)) if m.get("rangeCoverage") is not None else "未测"),
    }
    for k in ("stereo", "duration", "peak", "loudness", "gapRate", "rangeCoverage"):
        v = checks.get(k)
        verdict = "通过" if v is True else ("不合格" if v is False else "未测")
        lines.append("| %s | %s | %s |" % (names.get(k, k), values.get(k, ""), verdict))
    lines.extend(["", "合格线：响度 -13 +- 1.5 LUFS；断音率 < 12 次/分；音域覆盖 >= 70%；峰值 <= -0.5 dBFS；stereo 且时长 >= 30s。", ""])
    if report.get("problems"):
        lines.append("## 问题")
        lines.extend(["- " + p for p in report["problems"]])
        lines.append("")
    if report.get("suggestions"):
        lines.append("## 建议")
        lines.extend(["- " + s for s in report["suggestions"]])
        lines.append("")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
