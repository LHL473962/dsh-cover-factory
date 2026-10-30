# -*- coding: utf-8 -*-
"""stage 4 dataset：把清洗后的参考曲整理成训练集目录。

产物：work/dataset/<jobId>/ 下一批 wav + manifest.json（含训练音域 p5/p95，供 qa 用）。
权重来自 select 的音色聚类（x6 / x3），按 dataset.weight_repeat 复制以加重。
"""

import os
import shutil

from stages import base

STAGE = "dataset"


def run(job, cfg):
    from assets import analysis, audio

    rep = base.read_json(base.work(job, "select", "report.json"), None)
    if not rep:
        return base.result("failed", [], {}, error="没有 select/report.json：先跑 select")
    kept = [s for s in (rep.get("songs") or []) if s.get("role") == "ref" and s.get("kept")]
    matched_raw = base.matched_ref_raw_paths(job)
    if matched_raw:
        subset = []
        for s in kept:
            raw = base.resolve(job, s.get("file") or "")
            if os.path.normcase(os.path.abspath(raw)) in matched_raw:
                subset.append(s)
        if subset:
            kept = subset
            ref_w = float((cfg.get("select") or {}).get("ref_weight", 6.0))
            for s in kept:
                s["weight"] = ref_w
            base.log("dataset: 只使用命中的好球曲 %d 首（权重 %s）" % (len(kept), ref_w))
        else:
            base.log("dataset: 命中的好球曲都被 select 排除，回退到全部入选素材")
    if not kept:
        return base.result("failed", [], {}, error="没有入选参考曲：先看 select 报告")

    ds_cfg = cfg.get("dataset") or {}
    repeat = max(1, int(ds_cfg.get("weight_repeat", 1)))
    out_dir = base.work(job, "dataset", str(job.get("id") or "job"))
    manifest_path = os.path.join(out_dir, "manifest.json")

    inputs = []
    for s in kept:
        p = base.work(job, "clean", (s.get("id") or "ref") + ".wav")
        if not base.file_ok(p):
            return base.result("failed", [], {}, error="缺少 clean 产物：先跑 clean（" + p + "）")
        inputs.append(p)

    params = {"repeat": repeat, "pitch_percentiles": ds_cfg.get("pitch_percentiles") or [5, 95]}
    if base.recon(job, STAGE, inputs, params, [manifest_path]):
        man = base.read_json(manifest_path, {}) or {}
        base.log("dataset: manifest 已存在且输入未变，跳过")
        return base.result("skipped", [base.rel(job, manifest_path)], dict(base.prev_metrics(job, STAGE) or {}), reason="产物已存在且输入未变")

    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            p = os.path.join(out_dir, name)
            if os.path.isfile(p) and (name.endswith(".wav") or name == "manifest.json"):
                try:
                    os.remove(p)
                except OSError:
                    pass
    base.ensure_dir(out_dir)

    ff = audio.find_ffmpeg(cfg.get("paths"))
    analysis_sr = int((cfg.get("audio") or {}).get("analysis_sr", 16000))
    files = []
    voiced = []
    total_duration = 0.0
    for s, src in zip(kept, inputs):
        sid = s.get("id") or "ref"
        for i in range(repeat):
            dst = os.path.join(out_dir, "%s_w%d.wav" % (sid, i + 1) if repeat > 1 else sid + ".wav")
            shutil.copy2(src, dst)
            files.append({"name": os.path.basename(dst), "source": base.rel(job, src), "weight": s.get("weight", 1.0)})
        info = audio.probe(ff, src)
        if info.get("duration_sec"):
            total_duration += float(info["duration_sec"])
        mono = audio.decode(ff, src, analysis_sr, 1)
        f0 = analysis.f0_track(mono, analysis_sr)
        if len(f0):
            import numpy as np
            v = f0[np.isfinite(f0)]
            if len(v):
                voiced.append(v)

    pitch_range = {"lo": None, "hi": None, "median": None, "voiced_frames": 0}
    if not voiced:
        return base.result(
            "failed", [], {"files": len(files)},
            error="数据集里没有可检测音高的帧：素材可能不是人声（检查 separate 出来的 vocals 是否为空）",
        )
    if voiced:
        import numpy as np
        allv = np.concatenate(voiced)
        pcts = params["pitch_percentiles"]
        pitch_range = {
            "lo": round(float(np.percentile(allv, float(pcts[0]))), 2),
            "hi": round(float(np.percentile(allv, float(pcts[1]))), 2),
            "median": round(float(np.median(allv)), 2),
            "voiced_frames": int(allv.size),
        }

    manifest = {
        "version": 1,
        "generatedAt": base.now_iso(),
        "jobId": job.get("id"),
        "datasetDir": out_dir,
        "repeat": repeat,
        "files": files,
        "songs": [{"id": s.get("id"), "name": s.get("name"), "weight": s.get("weight", 1.0)} for s in kept],
        "total_duration_sec": round(total_duration, 2),
        "pitch_range": pitch_range,
    }
    base.write_json(manifest_path, manifest)

    metrics = {
        "files": len(files),
        "songs": len(kept),
        "total_duration_sec": round(total_duration, 2),
        "pitch_range": pitch_range,
    }
    return base.result(
        "done", [base.rel(job, manifest_path)] + [base.rel(job, os.path.join(out_dir, f["name"])) for f in files],
        metrics, input_hash=base.sig(job, inputs, params),
    )
