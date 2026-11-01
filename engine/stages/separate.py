# -*- coding: utf-8 -*-
"""stage 1 separate

options.separateModel:
  kim   （默认）mel_band_roformer + MelBandRoformer_kim.ckpt
  bs317 bs_roformer + model_bs_roformer_ep_317_sdr_12.9755.yaml/.ckpt
产出 work/stems/<song>/{vocals.wav,instrumental.wav}。

分离后立刻做 stem 自检（librosa HPSS 打击/谐波比 + RMS）：
  vocals.perc < 0.28 且 instrumental.perc > vocals.perc + 0.08 -> 正常
  vocals.perc > 0.30 -> warning VOCAL_STEM_HAS_DRUMS
  instrumental.perc + 0.03 < vocals.perc -> STEMS_SWAPPED（fail）
  vocals 比 instrumental 还响 -> warning VOCAL_STEM_LOUD
结果写 work/sep/stem_check.json 与 job.json。
"""

import math
import os

from assets import audio, commands
from stages import base

STAGE = "separate"


def _plan(job):
    plan = []
    for raw in base.raw_files(job):
        stem = os.path.splitext(os.path.basename(raw))[0]
        d = base.work(job, "stems", stem)
        plan.append({
            "raw": raw,
            "name": stem,
            "vocals": os.path.join(d, "vocals.wav"),
            "instrumental": os.path.join(d, "instrumental.wav"),
        })
    return plan


def _model_id(job, cfg):
    opts = job.get("options") or {}
    scfg = cfg.get("separate") or {}
    mid = str(opts.get("separateModel") or scfg.get("default_model") or "kim").strip().lower()
    if mid not in (scfg.get("models") or {}):
        base.log("separate: 未知 separateModel=%s，回退 kim" % mid)
        mid = "kim"
    return mid


def _missing_deps(cfg, spec):
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
    if not spec.get("config") or not os.path.isfile(spec["config"]):
        missing.append("分离 config 不存在: " + str(spec.get("config")))
    if not spec.get("checkpoint") or not os.path.isfile(spec["checkpoint"]):
        missing.append("分离权重不存在: " + str(spec.get("checkpoint")))
    return missing


def _normalize_stems(plan):
    """MSST 不同版本输出 Vocals.wav/Instrumental.wav；统一成小写文件名（Windows 大小写不敏感也要改）。"""
    for item in plan:
        for key, expected in (("vocals", "vocals.wav"), ("instrumental", "instrumental.wav")):
            target = item[key]
            d = os.path.dirname(target)
            if not os.path.isdir(d):
                continue
            names = os.listdir(d)
            if expected in names:
                continue
            for name in names:
                if name.lower() == expected:
                    try:
                        os.replace(os.path.join(d, name), target)
                    except OSError:
                        pass
                    break


def _hpss_stats(path, clip_sec):
    import librosa
    import numpy as np

    duration = float(librosa.get_duration(path=path))
    offset = max(0.0, (duration - float(clip_sec)) / 2.0) if duration > clip_sec else 0.0
    y, _sr = librosa.load(path, sr=22050, mono=True, offset=offset, duration=float(clip_sec))
    if len(y) == 0:
        return {"perc": 0.0, "rms": -120.0}
    spec = np.abs(librosa.stft(y)) ** 2
    harmonic, percussive = librosa.decompose.hpss(spec)
    total = float(np.sum(harmonic) + np.sum(percussive))
    perc = float(np.sum(percussive) / total) if total > 0 else 0.0
    rms = float(np.sqrt(np.mean(y * y)))
    db = 20.0 * math.log10(max(rms, 1e-9))
    return {"perc": round(perc, 4), "rms": round(db, 2)}


def _target_name(job):
    tgt = base.target_entry(job)
    if tgt:
        return os.path.splitext(os.path.basename(tgt.get("raw") or ""))[0]
    return ""


def _stem_check(job, cfg, plan, model_id):
    scfg = (cfg.get("separate") or {}).get("stem_check") or {}
    clip = float(scfg.get("clip_sec", 60))
    vocal_max = float(scfg.get("vocal_perc_max", 0.28))
    drums_perc = float(scfg.get("vocal_drums_perc", 0.30))
    inst_gap = float(scfg.get("inst_gap", 0.08))
    swap_margin = float(scfg.get("swap_margin", 0.03))
    loud_db = float(scfg.get("vocal_loud_db", 0.0))

    songs = []
    warnings = []
    any_swap = False
    for item in plan:
        v = None
        i = None
        err = ""
        try:
            v = _hpss_stats(item["vocals"], clip)
            i = _hpss_stats(item["instrumental"], clip)
        except Exception as exc:  # noqa: BLE001
            err = "%s: %s" % (type(exc).__name__, exc)

        item_warn = []
        verdict = "ok"
        if err:
            item_warn.append({"code": "STEM_CHECK_UNAVAILABLE", "message": err})
            verdict = "warn"
        elif (i["perc"] + swap_margin < v["perc"]) and (v["perc"] > drums_perc) and (v["rms"] > i["rms"] - 2.0):
            any_swap = True
            verdict = "swap"
            item_warn.append({
                "code": "STEMS_SWAPPED",
                "message": "伴奏的打击比例 %.3f 比人声 %.3f 低 %.3f：两条 stem 很可能接反" % (
                    i["perc"], v["perc"], v["perc"] - i["perc"]),
            })
        elif i["perc"] + swap_margin < v["perc"]:
            verdict = "warn"
            item_warn.append({
                "code": "STEM_CHECK_UNCERTAIN",
                "message": "伴奏打击比例 %.3f 低于人声 %.3f，但人声打击比例仅 %.3f（可能是安静伴奏），建议试听确认是否接反" % (
                    i["perc"], v["perc"], v["perc"]),
            })
        else:
            if v["perc"] > drums_perc:
                item_warn.append({
                    "code": "VOCAL_STEM_HAS_DRUMS",
                    "message": "人声轨打击比例 %.3f > %.2f：人声里混进了鼓/伴奏，换声会把乐器唱成人声" % (
                        v["perc"], drums_perc),
                })
            if v["rms"] > i["rms"] + loud_db:
                item_warn.append({
                    "code": "VOCAL_STEM_LOUD",
                    "message": "人声 RMS %.1f dBFS 比伴奏 %.1f dBFS 还响（通常人声低 5–15dB），建议检查" % (
                        v["rms"], i["rms"]),
                })
            if not (v["perc"] < vocal_max and i["perc"] > v["perc"] + inst_gap):
                if not item_warn:
                    item_warn.append({
                        "code": "STEM_CHECK_UNCERTAIN",
                        "message": "打击比例不够典型（人声 %.3f，伴奏 %.3f），建议试听确认" % (
                            v["perc"], i["perc"]),
                    })
            if item_warn:
                verdict = "warn"

        sounds = {
            "name": item["name"],
            "raw": base.rel(job, item["raw"]),
            "vocals": v or {"perc": None, "rms": None},
            "instrumental": i or {"perc": None, "rms": None},
            "verdict": verdict,
            "warnings": item_warn,
        }
        songs.append(sounds)
        for w in item_warn:
            warnings.append("%s: %s" % (w["code"], w["message"]))

    overall = "swap" if any_swap else ("warn" if warnings else "ok")
    target = _target_name(job)
    chosen = next((s for s in songs if s["name"] == target), songs[0] if songs else None)
    report = {
        "version": 1,
        "generatedAt": base.now_iso(),
        "model": model_id,
        "clipSec": clip,
        "thresholds": {
            "vocalPercMax": vocal_max,
            "vocalDrumsPerc": drums_perc,
            "instGap": inst_gap,
            "swapMargin": swap_margin,
            "vocalLoudDb": loud_db,
        },
        "vocals": (chosen or {}).get("vocals"),
        "instrumental": (chosen or {}).get("instrumental"),
        "verdict": overall,
        "swapped": any_swap,
        "warnings": warnings,
        "songs": songs,
    }
    path = base.work(job, "sep", "stem_check.json")
    base.ensure_dir(os.path.dirname(path))
    base.write_json(path, report)
    job["stem_check"] = {
        "report": base.rel(job, path),
        "model": model_id,
        "verdict": overall,
        "swapped": any_swap,
        "warnings": warnings,
        "songs": [{k: s.get(k) for k in ("name", "vocals", "instrumental", "verdict")} for s in songs],
    }
    return report, path, overall, warnings


def _range_gate(job, cfg, plan):
    """分离完成后立刻做音域覆盖检查：素材人声 p95 必须覆盖目标曲。"""
    opts = job.get("options") or {}
    if opts.get("allowRangeFail") is True:
        return None
    scfg = cfg.get("separate") or {}
    if scfg.get("range_gate") is False:
        return None
    target_name = _target_name(job)
    tgt = next((x for x in plan if x.get("name") == target_name), None)
    if not tgt or not base.file_ok(tgt.get("vocals", "")):
        return None
    mats = [x["vocals"] for x in plan if x is not tgt and base.file_ok(x.get("vocals", ""))]
    if not mats:
        return None
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "range_check.py")
    margin = float(scfg.get("range_margin_hz", 20))
    try:
        r = subprocess.run([sys.executable, tool, "--target", tgt["vocals"], "--materials"] + mats +
                            ["--margin", str(margin), "--json"], capture_output=True, text=True, timeout=900)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return None
    job["range_check"] = {
        "target": data.get("target"), "coverage": data.get("coverage"),
        "required": data.get("required"), "verdict": data.get("verdict"),
    }
    if data.get("verdict") == "FAIL":
        cov = data.get("coverage") or {}
        need = data.get("required") or {}
        raise RuntimeError(
            "RANGE_COVERAGE_FAIL: 素材人声 p95=%s Hz，目标需要 >=%s Hz（%s）；"
            "请补高音区素材（可用 engine/tools/material_finder.py 自动搜索分析），"
            "或设 options.allowRangeFail=true" % (cov.get("p95"), need.get("p95"), tgt.get("name")))
    return data


def run(job, cfg):
    raw_dir = base.work(job, "raw")
    stems_dir = base.work(job, "stems")
    plan = _plan(job)
    if not plan:
        return base.result("failed", [], {}, error="work/raw 里没有素材：先跑 fetch")

    model_id = _model_id(job, cfg)
    spec = commands.separate_model(cfg, model_id)
    job["separate_model"] = {
        "id": spec["id"], "label": spec["label"], "modelType": spec["model_type"],
        "config": spec["config"], "checkpoint": spec["checkpoint"],
    }

    outs = []
    for item in plan:
        outs.extend([item["vocals"], item["instrumental"]])
    params = {
        "model": spec["id"],
        "model_type": spec["model_type"],
        "config": spec["config"],
        "checkpoint": spec["checkpoint"],
        "extract_instrumental": True,
    }
    inputs = [item["raw"] for item in plan]

    check_path = base.work(job, "sep", "stem_check.json")
    if base.recon(job, STAGE, inputs, params, outs):
        _normalize_stems(plan)
        report = base.read_json(check_path, {}) or {}
        if not report:
            report, check_path, verdict, warnings = _stem_check(job, cfg, plan, model_id)
        else:
            verdict = str(report.get("verdict") or "ok")
            warnings = list(report.get("warnings") or [])
            job["stem_check"] = {
                "report": base.rel(job, check_path), "model": model_id,
                "verdict": verdict, "swapped": bool(report.get("swapped")),
                "warnings": warnings,
                "songs": [{k: s.get(k) for k in ("name", "vocals", "instrumental", "verdict")}
                          for s in (report.get("songs") or [])],
            }
        if verdict == "swap":
            msg = "STEMS_SWAPPED：人声/伴奏 stem 疑似接反；" + "；".join(warnings[:3])
            return base.result("failed", [base.rel(job, check_path)], {"model": model_id, "verdict": verdict}, error=msg)
        base.log("separate: 产物已存在且输入未变，跳过（stem_check=%s）" % verdict)
        metrics = dict(base.prev_metrics(job, STAGE) or {"songs": len(plan)})
        metrics.update({"model": model_id, "stemCheck": verdict})
        return base.result(
            "skipped", [base.rel(job, p) for p in outs] + [base.rel(job, check_path)],
            metrics, reason="产物已存在且输入未变",
        )

    missing = _missing_deps(cfg, spec)
    if missing:
        return base.result("failed", [], {}, error="MSST 分离依赖缺失：" + "；".join(missing))

    base.ensure_dir(stems_dir)
    base.log("separate: 模型=%s（%s）" % (spec["id"], spec["label"]))
    cmd = commands.separate_cmd(cfg, raw_dir, stems_dir, model_id=model_id)
    timeout = float((cfg.get("separate") or {}).get("timeout_sec", 7200))
    res = base.run_cmd(cmd, cwd=(cfg.get("paths") or {}).get("msst_root"),
                       env=base.child_env(), timeout=timeout)
    if res["returncode"] != 0:
        return base.result(
            "failed", [], {"songs": len(plan), "elapsed_sec": res["elapsed_sec"], "model": model_id},
            error="MSST 分离失败（exit=%s%s）：%s" % (
                res["returncode"], "，超时" if res["timed_out"] else "", res["tail"][-1200:],
            ),
        )

    _normalize_stems(plan)
    absent = [p for p in outs if not base.file_ok(p)]
    if absent:
        return base.result(
            "failed", [], {"songs": len(plan), "model": model_id},
            error="分离命令成功但缺少产物（检查 MUSDB 输出结构）：" + "；".join(absent[:6]),
        )

    report, check_path, verdict, warnings = _stem_check(job, cfg, plan, model_id)
    job["warnings"] = list(dict.fromkeys((job.get("warnings") or []) + warnings))
    if verdict == "swap":
        base.log("separate: STEMS_SWAPPED，停止后续阶段")
        return base.result(
            "failed", [base.rel(job, p) for p in outs] + [base.rel(job, check_path)],
            {"songs": len(plan), "stems": len(outs), "model": model_id,
             "elapsed_sec": res["elapsed_sec"], "stemCheck": verdict},
            error="STEMS_SWAPPED：人声/伴奏 stem 疑似接反；" + "；".join(warnings[:3]),
        )

    try:
        _range_gate(job, cfg, plan)
    except RuntimeError as exc:
        return base.result("failed", [base.rel(job, p) for p in outs] + [base.rel(job, check_path)],
                           {"songs": len(plan), "model": model_id, "stemCheck": verdict}, error=str(exc))
    base.log("separate: 完成 %d 首（%d 个 stem，模型=%s，stem_check=%s）" % (
        len(plan), len(outs), model_id, verdict))
    return base.result(
        "done", [base.rel(job, p) for p in outs] + [base.rel(job, check_path)],
        {"songs": len(plan), "stems": len(outs), "elapsed_sec": res["elapsed_sec"],
         "model": model_id, "stemCheck": verdict, "stemWarnings": len(warnings)},
        input_hash=base.sig(job, inputs, params),
    )
