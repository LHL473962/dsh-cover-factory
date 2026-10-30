# -*- coding: utf-8 -*-
"""stage 2 select：智选素材（对唱排除 / 现场降权 / 音色聚类加权）。

规则照 DESIGN.md：
1. 逐帧 F0，统计中位音高与男声区（60-165Hz）帧占比；
2. 男声帧 > 15% -> 对唱/合唱，默认排除（allowDuet 覆盖）；
3. 立体声相关 < 0.3 -> 现场/伴唱，降权；
4. 时长 < 60s -> 排除；
5. 参考曲按音色聚类（中位音高 + 频谱质心）加权 x6，其余 x1。
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
        "cluster": rep.get("cluster"),
    }


def run(job, cfg):
    from assets import analysis, audio

    scfg = cfg.get("select") or {}
    opts = job.get("options") or {}
    allow_duet = bool(opts.get("allowDuet") or opts.get("allow_duet") or scfg.get("allow_duet"))
    analysis_sr = int((cfg.get("audio") or {}).get("analysis_sr", 16000))

    entries = base.songs(job)
    if not entries:
        return base.result("failed", [], {}, error="job 里没有素材：先跑 fetch")

    for e in entries:
        e["vocals"], e["instrumental"] = _stems(job, e["raw"])
    missing = [e["name"] for e in entries if not (base.file_ok(e["vocals"]) and base.file_ok(e["instrumental"]))]
    if missing:
        return base.result("failed", [], {}, error="缺少分离出的 stem（先跑 separate）：" + "、".join(missing[:8]))

    report_path = base.work(job, "select", "report.json")
    params = {"rules": scfg, "allow_duet": allow_duet, "analysis_sr": analysis_sr}
    inputs = [e["vocals"] for e in entries] + [e["raw"] for e in entries]

    if base.recon(job, STAGE, inputs, params, [report_path]):
        rep = base.read_json(report_path, {}) or {}
        base.log("select: report.json 已存在且输入未变，跳过")
        return base.result("skipped", [base.rel(job, report_path)], _report_metrics(rep), reason="产物已存在且输入未变")

    ff = audio.find_ffmpeg(cfg.get("paths"))

    male_lo = float(scfg.get("male_lo", 60.0))
    male_hi = float(scfg.get("male_hi", 165.0))
    duet_ratio = float(scfg.get("duet_male_ratio", 0.15))
    corr_min = float(scfg.get("stereo_corr_min", 0.3))
    min_dur = float(scfg.get("min_duration_sec", 60.0))

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
        f0 = analysis.f0_track(mono, analysis_sr)
        fs = analysis.f0_summary(f0, male_lo=male_lo, male_hi=male_hi)
        cen = analysis.spectral_centroid(mono, analysis_sr)
        songs.append({
            "id": e["id"],
            "name": e["name"],
            "role": e["role"],
            "file": base.rel(job, e["raw"]),
            "vocals": base.rel(job, e["vocals"]),
            "instrumental": base.rel(job, e["instrumental"]),
            "duration_sec": round(duration, 2),
            "median_f0": fs.get("median"),
            "f0_p5": fs.get("p5"),
            "f0_p95": fs.get("p95"),
            "male_ratio": fs.get("male_ratio"),
            "voiced_frames": fs.get("voiced_frames"),
            "frames": fs.get("frames"),
            "stereo_corr": corr,
            "centroid_hz": (cen or {}).get("median_hz"),
            "weight": 1.0,
            "kept": True,
            "reasons": [],
        })

    for s in songs:
        if s["role"] == "target":
            continue
        reasons = []
        if s["duration_sec"] and s["duration_sec"] < min_dur:
            reasons.append("时长 %.0fs < %.0fs" % (s["duration_sec"], min_dur))
        if s["male_ratio"] is not None and s["male_ratio"] > duet_ratio:
            reasons.append("男声区帧 %.0f%% > %.0f%%（疑似对唱/合唱）" % (s["male_ratio"] * 100, duet_ratio * 100))
        if s["stereo_corr"] is not None and s["stereo_corr"] < corr_min:
            reasons.append("立体声相关 %.2f < %.2f（疑似现场/伴唱，降权）" % (s["stereo_corr"], corr_min))
        hard = False
        if s["duration_sec"] and s["duration_sec"] < min_dur:
            hard = True
        if s["male_ratio"] is not None and s["male_ratio"] > duet_ratio and not allow_duet:
            hard = True
        s["kept"] = not hard
        s["status"] = "kept" if s["kept"] else "excluded"
        s["reasons"] = reasons

    kept_refs = [s for s in songs if s["role"] == "ref" and s["kept"]]
    tol_semi = float(scfg.get("timbre_semitone_tol", 2.0))
    tol_cent = float(scfg.get("centroid_tol", 0.35))
    cluster_info = {"median_f0": None, "median_centroid_hz": None, "members": [], "size": 0}
    if kept_refs:
        f0s = [s["median_f0"] for s in kept_refs if s["median_f0"]]
        cents = [s["centroid_hz"] for s in kept_refs if s["centroid_hz"]]
        med_f0 = sorted(f0s)[len(f0s) // 2] if f0s else None
        med_cent = sorted(cents)[len(cents) // 2] if cents else None
        cluster_info["median_f0"] = round(float(med_f0), 2) if med_f0 else None
        cluster_info["median_centroid_hz"] = round(float(med_cent), 1) if med_cent else None
        for s in kept_refs:
            in_cluster = True
            if med_f0 and s["median_f0"]:
                if abs(12.0 * math.log(s["median_f0"] / float(med_f0), 2)) > tol_semi:
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
    for s in songs:
        if s["role"] == "ref" and not s["kept"]:
            s["weight"] = 0.0
            s["cluster"] = False

    counts = {
        "songs": len(songs),
        "refs": len([s for s in songs if s["role"] == "ref"]),
        "kept": len(kept_refs),
        "excluded": len([s for s in songs if s["role"] == "ref" and not s["kept"]]),
    }
    report = {
        "version": 1,
        "generatedAt": base.now_iso(),
        "jobId": job.get("id"),
        "allowDuet": allow_duet,
        "rules": {
            "male_lo": male_lo, "male_hi": male_hi, "duet_male_ratio": duet_ratio,
            "stereo_corr_min": corr_min, "min_duration_sec": min_dur,
            "ref_weight": scfg.get("ref_weight", 6.0),
            "outlier_weight": scfg.get("outlier_weight", 3.0),
            "target_weight": scfg.get("target_weight", 1.0),
        },
        "songs": songs,
        "selected": [s["id"] for s in kept_refs],
        "excluded": [{"id": s["id"], "name": s["name"], "reason": "；".join(s["reasons"])} for s in songs if s["role"] == "ref" and not s["kept"]],
        "target": next((s["id"] for s in songs if s["role"] == "target"), None),
        "cluster": cluster_info,
        "counts": counts,
    }
    base.write_json(report_path, report)

    reason_text = ""
    if not kept_refs:
        reason_text = "全部参考曲被排除（" + "；".join(
            "%s: %s" % (s["name"], " / ".join(s["reasons"])) for s in songs if s["role"] == "ref"
        ) + "）。可换素材或加 --allow-duet 放开对唱限制。"

    metrics = _report_metrics(report)
    if not kept_refs:
        return base.result(
            "failed", [base.rel(job, report_path)], metrics,
            error=reason_text, input_hash=base.sig(job, inputs, params),
        )

    base.log("select: 保留 %d 首参考曲，排除 %d 首；目标曲 %s" % (
        counts["kept"], counts["excluded"], report["target"] or "无",
    ))
    return base.result(
        "done", [base.rel(job, report_path)], metrics,
        input_hash=base.sig(job, inputs, params),
    )
