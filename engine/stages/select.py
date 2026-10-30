# -*- coding: utf-8 -*-
"""stage 2 select：性别/对唱判断 + 智选素材。

新规则（2026-10-30 重做，修复“男歌手素材被全排除”）：
1. 逐帧 F0，分低区 60–165Hz / 高区 165–500Hz，统计两区帧占比与中位；
2. 对唱/合唱 = 两区占比都 >=20% 且 高区中位 / 低区中位 > 1.5；
3. 期望声部 expected：options.voice 显式指定；auto 时看目标曲人声中位（<200Hz→male），
   目标曲不可用时取素材多数；
4. 排除规则 = 与 expected 不符（male 时高区主导、female 时低区主导），不再无条件排除男声；
5. 时长 < 60s 排除；立体声相关低降权；参考曲按音色聚类加权 ×6（用户点名的好球曲权重 ×6）。
"""

import math
import os

from stages import base

STAGE = "select"


def _stems(job, raw):
    stem = os.path.splitext(os.path.basename(raw))[0]
    d = base.work(job, "stems", stem)
    return os.path.join(d, "vocals.wav"), os.path.join(d, "instrumental.wav")


def _report_metrics(rep):
    counts = rep.get("counts") or {}
    return {
        "refs": counts.get("refs"),
        "kept": counts.get("kept"),
        "excluded": counts.get("excluded"),
        "target": bool(rep.get("target")),
        "expectedVoice": rep.get("expectedVoice"),
        "expectedSource": rep.get("expectedSource"),
        "cluster": rep.get("cluster"),
    }


def _voice_guess(bimodal, low_share, high_share, single_share):
    if bimodal:
        return "mixed"
    if low_share >= single_share:
        return "male"
    if high_share >= single_share:
        return "female"
    return "male" if low_share >= high_share else "female"


def _expected_voice(job, songs, split_hz):
    opts = job.get("options") or {}
    explicit = str(opts.get("voice") or "auto").strip().lower()
    if explicit in ("male", "female"):
        return explicit, "options.voice"
    target = next((s for s in songs if s["role"] == "target"), None)
    if target and target.get("medianF0"):
        expected = "male" if float(target["medianF0"]) < split_hz else "female"
        return expected, "target"
    vals = [float(s["medianF0"]) for s in songs if s["role"] == "ref" and s.get("medianF0")]
    if vals:
        males = sum(1 for v in vals if v < split_hz)
        return ("male" if males * 2 >= len(vals) else "female"), "materials-majority"
    return "female", "default"


def run(job, cfg):
    from assets import analysis, audio

    scfg = cfg.get("select") or {}
    opts = job.get("options") or {}
    voice_opt = str(opts.get("voice") or "auto").strip().lower()
    if voice_opt not in ("auto", "female", "male"):
        voice_opt = "auto"
    allow_duet = bool(opts.get("allowDuet") or opts.get("allow_duet") or scfg.get("allow_duet"))
    analysis_sr = int((cfg.get("audio") or {}).get("analysis_sr", 16000))

    low_band = scfg.get("low_band") or [60.0, 165.0]
    high_band = scfg.get("high_band") or [165.0, 500.0]
    low_lo, low_hi = float(low_band[0]), float(low_band[1])
    high_lo, high_hi = float(high_band[0]), float(high_band[1])
    bim_share = float(scfg.get("bimodal_share", 0.2))
    bim_ratio = float(scfg.get("bimodal_ratio", 1.5))
    single_share = float(scfg.get("single_voice_share", 0.8))
    split_hz = float(scfg.get("voice_median_split", 200.0))
    corr_min = float(scfg.get("stereo_corr_min", 0.3))
    min_dur = float(scfg.get("min_duration_sec", 60.0))

    entries = base.songs(job)
    if not entries:
        return base.result("failed", [], {}, error="job 里没有素材：先跑 fetch")

    for e in entries:
        e["vocals"], e["instrumental"] = _stems(job, e["raw"])
    missing = [e["name"] for e in entries if not (base.file_ok(e["vocals"]) and base.file_ok(e["instrumental"]))]
    if missing:
        return base.result("failed", [], {}, error="缺少分离出的 stem（先跑 separate）：" + "、".join(missing[:8]))

    report_path = base.work(job, "select", "report.json")
    params = {
        "rules": scfg,
        "allow_duet": allow_duet,
        "analysis_sr": analysis_sr,
        "voice": voice_opt,
        "bands": [low_lo, low_hi, high_lo, high_hi],
    }
    inputs = [e["vocals"] for e in entries] + [e["raw"] for e in entries]

    if base.recon(job, STAGE, inputs, params, [report_path]):
        rep = base.read_json(report_path, {}) or {}
        base.log("select: report.json 已存在且输入未变，跳过")
        return base.result(
            "skipped", [base.rel(job, report_path)], _report_metrics(rep),
            reason="产物已存在且输入未变",
        )

    ff = audio.find_ffmpeg(cfg.get("paths"))

    songs = []
    for e in entries:
        info = audio.probe(ff, e["raw"])
        duration = float(info.get("duration_sec") or 0.0)
        corr = None
        try:
            corr = analysis.stereo_corr(audio.decode(ff, e["raw"], 8000, 2))
        except Exception as ex:  # noqa: BLE001
            base.log("select: %s 立体声相关读取失败（忽略）：%s" % (e["name"], ex))
        try:
            mono = audio.decode(ff, e["vocals"], analysis_sr, 1)
        except Exception as ex:  # noqa: BLE001
            return base.result("failed", [], {}, error="读取人声 stem 失败（%s）：%s" % (e["name"], ex))
        f0 = analysis.f0_track(mono, analysis_sr, fmin=low_lo)
        fs = analysis.f0_summary(f0, male_lo=low_lo, male_hi=low_hi)
        low = analysis.band_stats(f0, low_lo, low_hi)
        high = analysis.band_stats(f0, high_lo, high_hi)
        median = fs.get("median")
        ratio = None
        if low.get("median") and high.get("median"):
            ratio = round(float(high["median"]) / float(low["median"]), 2)
        bimodal = bool(
            low.get("share", 0) >= bim_share
            and high.get("share", 0) >= bim_share
            and ratio is not None
            and ratio > bim_ratio
        )
        guess = _voice_guess(bimodal, low.get("share", 0), high.get("share", 0), single_share)
        cen = analysis.spectral_centroid(mono, analysis_sr)
        songs.append({
            "id": e["id"],
            "name": e["name"],
            "role": e["role"],
            "file": base.rel(job, e["raw"]),
            "vocals": base.rel(job, e["vocals"]),
            "instrumental": base.rel(job, e["instrumental"]),
            "duration_sec": round(duration, 2),
            "medianF0": median,
            "median_f0": median,
            "lowShare": low.get("share", 0.0),
            "highShare": high.get("share", 0.0),
            "lowMedian": low.get("median"),
            "highMedian": high.get("median"),
            "bandRatio": ratio,
            "bimodal": bimodal,
            "voiceGuess": guess,
            "singleVoice": bool(low.get("share", 0) >= single_share or high.get("share", 0) >= single_share),
            "male_ratio": fs.get("male_ratio"),
            "f0_p5": fs.get("p5"),
            "f0_p95": fs.get("p95"),
            "voiced_frames": fs.get("voiced_frames"),
            "frames": fs.get("frames"),
            "stereo_corr": corr,
            "centroid_hz": (cen or {}).get("median_hz"),
            "weight": 1.0,
            "kept": True,
            "used": True,
            "status": "kept",
            "reason": "",
            "reasons": [],
        })

    expected, expected_source = _expected_voice(job, songs, split_hz)

    for s in songs:
        if s["role"] == "target":
            s["kept"] = True
            s["used"] = True
            s["status"] = "kept"
            s["reason"] = ""
            continue
        reasons = []
        hard = False
        if s["duration_sec"] and s["duration_sec"] < min_dur:
            hard = True
            reasons.append("时长 %.0fs < %.0fs" % (s["duration_sec"], min_dur))
        if s["bimodal"]:
            detail = "低区 %.0f%% / 高区 %.0f%%（中位比 %s）" % (
                s["lowShare"] * 100, s["highShare"] * 100, s.get("bandRatio"))
            if allow_duet:
                reasons.append("对唱/合唱（allowDuet 放行）：" + detail)
            else:
                hard = True
                reasons.append("对唱/合唱：" + detail)
        med = s.get("medianF0")
        if med is not None and expected == "male" and float(med) > split_hz and s["lowShare"] < 0.20:
            hard = True
            reasons.append("与期望声部 male 不符：高区主导（中位 %.0fHz，低区 %.0f%%）" % (med, s["lowShare"] * 100))
        if med is not None and expected == "female" and float(med) < split_hz and s["highShare"] < 0.20:
            hard = True
            reasons.append("与期望声部 female 不符：低区主导（中位 %.0fHz，高区 %.0f%%）" % (med, s["highShare"] * 100))
        if s["stereo_corr"] is not None and s["stereo_corr"] < corr_min:
            reasons.append("立体声相关 %.2f < %.2f（疑似现场/伴唱，降权）" % (s["stereo_corr"], corr_min))
        s["kept"] = not hard
        s["used"] = not hard
        s["status"] = "kept" if s["kept"] else "excluded"
        s["reasons"] = reasons
        s["reason"] = "；".join(reasons)

    kept_refs = [s for s in songs if s["role"] == "ref" and s["kept"]]
    tol_semi = float(scfg.get("timbre_semitone_tol", 2.0))
    tol_cent = float(scfg.get("centroid_tol", 0.35))
    cluster_info = {"median_f0": None, "median_centroid_hz": None, "members": [], "size": 0}
    if kept_refs:
        f0s = [s["medianF0"] for s in kept_refs if s["medianF0"]]
        cents = [s["centroid_hz"] for s in kept_refs if s["centroid_hz"]]
        med_f0 = sorted(f0s)[len(f0s) // 2] if f0s else None
        med_cent = sorted(cents)[len(cents) // 2] if cents else None
        cluster_info["median_f0"] = round(float(med_f0), 2) if med_f0 else None
        cluster_info["median_centroid_hz"] = round(float(med_cent), 1) if med_cent else None
        for s in kept_refs:
            in_cluster = True
            if med_f0 and s["medianF0"]:
                if abs(12.0 * math.log(s["medianF0"] / float(med_f0), 2)) > tol_semi:
                    in_cluster = False
            if med_cent and s["centroid_hz"] and abs(s["centroid_hz"] - float(med_cent)) > float(med_cent) * tol_cent:
                in_cluster = False
            w = float(scfg.get("ref_weight", 6.0)) if in_cluster else float(scfg.get("outlier_weight", 3.0))
            if not in_cluster:
                s["reasons"].append("音色聚类离群（中位音高/质心偏差），权重 %s" % w)
            if s["stereo_corr"] is not None and s["stereo_corr"] < corr_min:
                w = w * 0.5
                s["reasons"].append("立体声相关低，权重减半")
            s["weight"] = round(w, 2)
            s["cluster"] = in_cluster
            if in_cluster:
                cluster_info["members"].append(s["id"])
        cluster_info["size"] = len(cluster_info["members"])

    matched_raw = base.matched_ref_raw_paths(job)
    if matched_raw:
        user_weight = float(scfg.get("ref_weight", 6.0))
        for s in kept_refs:
            raw = base.resolve(job, s.get("file") or "")
            if os.path.normcase(os.path.abspath(raw)) in matched_raw:
                s["weight"] = round(user_weight, 2)
                s["userRef"] = True
                s["cluster"] = True
            else:
                s["weight"] = 1.0
                s["userRef"] = False
                s["cluster"] = False
        cluster_info["userRefs"] = [s["id"] for s in kept_refs if s.get("userRef")]

    for s in songs:
        if s["role"] == "target":
            s["weight"] = float(scfg.get("target_weight", 1.0))
            s["cluster"] = False
        elif not s["kept"]:
            s["weight"] = 0.0
            s["cluster"] = False

    counts = {
        "songs": len(songs),
        "refs": len([s for s in songs if s["role"] == "ref"]),
        "kept": len(kept_refs),
        "excluded": len([s for s in songs if s["role"] == "ref" and not s["kept"]]),
        "expectedVoice": expected,
    }
    report = {
        "version": 2,
        "generatedAt": base.now_iso(),
        "jobId": job.get("id"),
        "voiceInput": voice_opt,
        "expectedVoice": expected,
        "expectedSource": expected_source,
        "bands": {
            "low": [low_lo, low_hi],
            "high": [high_lo, high_hi],
            "bimodalShare": bim_share,
            "bimodalRatio": bim_ratio,
            "singleVoiceShare": single_share,
            "voiceMedianSplit": split_hz,
        },
        "allowDuet": allow_duet,
        "rules": {
            "bimodal_share": bim_share, "bimodal_ratio": bim_ratio,
            "single_voice_share": single_share, "voice_median_split": split_hz,
            "stereo_corr_min": corr_min, "min_duration_sec": min_dur,
            "ref_weight": scfg.get("ref_weight", 6.0),
            "outlier_weight": scfg.get("outlier_weight", 3.0),
            "target_weight": scfg.get("target_weight", 1.0),
        },
        "songs": songs,
        "selected": [s["id"] for s in kept_refs],
        "excluded": [{"id": s["id"], "name": s["name"], "reason": s["reason"]} for s in songs if s["role"] == "ref" and not s["kept"]],
        "target": next((s["id"] for s in songs if s["role"] == "target"), None),
        "cluster": cluster_info,
        "counts": counts,
    }
    base.write_json(report_path, report)

    job.setdefault("options", {})["voice"] = voice_opt
    job["select_report"] = base.rel(job, report_path)
    job["expectedVoice"] = expected

    metrics = _report_metrics(report)
    if not kept_refs:
        reason_text = "全部参考曲被排除（expected=%s）：%s。可换素材、改 options.voice，或加 --allow-duet 放开对唱限制。" % (
            expected,
            "；".join("%s: %s" % (s["name"], s["reason"] or "无原因") for s in songs if s["role"] == "ref"),
        )
        return base.result(
            "failed", [base.rel(job, report_path)], metrics,
            error=reason_text, input_hash=base.sig(job, inputs, params),
        )

    base.log("select: expected=%s（%s）；保留 %d 首参考曲，排除 %d 首；目标曲 %s" % (
        expected, expected_source, counts["kept"], counts["excluded"], report["target"] or "无",
    ))
    return base.result(
        "done", [base.rel(job, report_path)], metrics,
        input_hash=base.sig(job, inputs, params),
    )
