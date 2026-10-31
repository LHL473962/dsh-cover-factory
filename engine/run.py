#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻唱工厂 · 本地引擎 CLI。

用法（插件只通过它调用）：
  python engine/run.py init   --config job_config.json --workdir <dir> [--force] [--reset]
  python engine/run.py run    --job <job.json> [--from stage] [--to stage] [--resume] [--allow-duet] [--dry-run]
  python engine/run.py status --job <job.json> [--text]
  python engine/run.py cancel --job <job.json>
  python engine/run.py delete --job <job.json> [--purge]
  python engine/run.py doctor [--config 覆盖.json]
  python engine/run.py setup --check|--install [--config 覆盖.json]

退出码：0 成功；2 需要用户决策（硬件/版权确认）；1 失败。
job.json 原子写（tmp + os.replace）；每个 stage 日志写 <workdir>/logs/<stage>.log。
"""

import argparse
import atexit
import copy
import datetime
import importlib
import json
import os
import shutil
import subprocess
import sys
import traceback

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from stages import STAGES  # noqa: E402
from stages import base  # noqa: E402

ENGINE_VERSION = "0.1.0"
DEFAULT_CONFIG = os.path.join(ENGINE_DIR, "config_default.json")
LOCAL_CONFIG = os.path.join(ENGINE_DIR, "config.local.json")


# ------------------------------------------------------------------ 基础工具

def now_iso():
    return base.now_iso()


def say(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


def fail_json(error, message, **extra):
    payload = {"ok": False, "error": error, "message": message}
    payload.update(extra)
    say(json.dumps(payload, ensure_ascii=False))
    return 1


def deep_merge(dst, src):
    for key, val in (src or {}).items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], val)
        else:
            dst[key] = val
    return dst


def load_engine_cfg(override_path=None, job=None):
    cfg = base.read_json(DEFAULT_CONFIG, None)
    if not cfg:
        raise RuntimeError("引擎默认配置读不出来: " + DEFAULT_CONFIG)
    local = base.read_json(LOCAL_CONFIG, None)
    if isinstance(local, dict):
        deep_merge(cfg, local)
    if override_path:
        over = base.read_json(override_path, None)
        if not isinstance(over, dict):
            raise RuntimeError("--config 指定的引擎配置读不出来: " + override_path)
        deep_merge(cfg, over)
    if job and isinstance(job.get("config"), dict):
        deep_merge(cfg, job["config"])
    if job:
        apply_job_qa(cfg, job)
    return cfg


def apply_job_qa(cfg, job):
    qa = job.get("qa")
    if not isinstance(qa, dict):
        return cfg
    q = cfg.setdefault("qa", {})
    lo = qa.get("minLoudness")
    hi = qa.get("maxLoudness")
    if lo is not None and hi is not None:
        q["loudness_target"] = (float(lo) + float(hi)) / 2.0
        q["loudness_tol"] = (float(hi) - float(lo)) / 2.0
    elif lo is not None:
        q["loudness_target"] = float(lo) + float(q.get("loudness_tol", 1.5))
    elif hi is not None:
        q["loudness_target"] = float(hi) - float(q.get("loudness_tol", 1.5))
    if qa.get("minGapRate") is not None:
        q["gap_per_min"] = float(qa["minGapRate"])
    for key in ("loudness_target", "loudness_tol", "peak_max_db", "gap_per_min",
                "range_coverage", "range_semitones", "min_duration_sec"):
        if qa.get(key) is not None:
            q[key] = qa[key]
    return cfg


def blank_stage(name):
    return {
        "status": "pending",
        "startedAt": None,
        "finishedAt": None,
        "artifacts": [],
        "log": "logs/%s.log" % name,
        "metrics": {},
        "error": None,
        "reason": None,
        "inputHash": None,
    }


def read_job(path):
    data = base.read_json(path, None)
    if not isinstance(data, dict):
        return None
    stages = data.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    for name in STAGES:
        if name not in stages or not isinstance(stages[name], dict):
            stages[name] = blank_stage(name)
    data["stages"] = stages
    return data


def write_job(job, path):
    job["updatedAt"] = now_iso()
    data = {k: v for k, v in job.items() if not str(k).startswith("_")}
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def sanitize_id(text):
    import re
    s = re.sub(r"[^0-9A-Za-z_\-]+", "-", str(text or "").strip())
    s = s.strip("-_")
    return s[:60] or "job"


def default_job_id(config, workdir):
    import datetime
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    singer = sanitize_id(config.get("singer") or "cover")[:24]
    suffix = os.path.basename(os.path.abspath(workdir)).strip() or ""
    if suffix and suffix.lower() not in ("work", "job", "jobs", "tmp", "temp", "."):
        return sanitize_id(suffix)
    return singer + "-" + stamp


def normalize_target(target_in):
    """target 可以是字符串路径，也可以是 {file,title}。"""
    path = ""
    meta = {}
    if isinstance(target_in, str):
        path = target_in.strip()
    elif isinstance(target_in, dict):
        path = str(target_in.get("file") or target_in.get("path") or target_in.get("audio") or "").strip()
        title = target_in.get("title") or target_in.get("name")
        if title:
            meta["title"] = str(title)
            meta["name"] = str(title)
    return path, meta


def resolve_workdir(args, job_path=""):
    if getattr(args, "workdir", None):
        return os.path.abspath(args.workdir)
    if job_path:
        return os.path.dirname(os.path.abspath(job_path))
    return os.getcwd()


# ------------------------------------------------------------------ init

def cmd_init(args):
    config_path = os.path.abspath(args.config)
    config = base.read_json(config_path, None)
    if not isinstance(config, dict):
        return fail_json("CONFIG_UNREADABLE", "job 配置读不出来或不是 JSON 对象: " + config_path)

    workdir = args.workdir or os.path.dirname(config_path) or os.getcwd()
    workdir = os.path.abspath(workdir)
    job_path = os.path.join(workdir, "job.json")
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.join(workdir, "logs"), exist_ok=True)

    singer = str(config.get("singer") or "").strip()
    if not singer:
        return fail_json("MISSING_SINGER", "job 配置缺 singer（歌手名）", config=config_path)
    mode = str(config.get("mode") or "import").strip().lower()
    if mode not in ("import", "auto", "fetch", "download"):
        return fail_json("BAD_MODE", "mode 只能是 import 或 auto（抓取模式）：" + mode)
    sources = config.get("sources") if isinstance(config.get("sources"), dict) else {}
    refs_raw = config.get("refs")
    if isinstance(refs_raw, str):
        refs_raw = [refs_raw]
    refs_in = refs_raw if isinstance(refs_raw, list) else []
    refs_norm = []
    for item in refs_in:
        if isinstance(item, str):
            if item.strip():
                refs_norm.append(item.strip())
        elif isinstance(item, dict):
            p = item.get("path") or item.get("source") or item.get("file")
            if p:
                refs_norm.append(str(p))
    folder = str(sources.get("folder") or sources.get("dir") or "").strip()
    if mode == "import" and not folder and not refs_in and not sources.get("local_clients"):
        return fail_json("MISSING_SOURCES", "import 模式需要 sources.folder / refs / sources.local_clients", config=config_path)

    target_path, target_meta = normalize_target(config.get("target"))
    options = config.get("options") if isinstance(config.get("options"), dict) else {}
    if isinstance(config.get("output"), dict) and config["output"].get("dir"):
        options.setdefault("outputDir", config["output"]["dir"])
    voice = str(options.get("voice") or "auto").strip().lower()
    if voice not in ("auto", "female", "male"):
        return fail_json("BAD_VOICE", "options.voice 只能是 auto / female / male，收到：" + str(options.get("voice")))
    options["voice"] = voice
    sep_model = str(options.get("separateModel") or "kim").strip().lower()
    if sep_model not in ("kim", "bs317"):
        return fail_json("BAD_SEPARATE_MODEL", "options.separateModel 只能是 kim / bs317，收到：" + str(options.get("separateModel")))
    options["separateModel"] = sep_model
    if "mixPreset" in options and str(options.get("mixPreset") or "").strip().lower() not in ("old", "new"):
        return fail_json("BAD_MIX_PRESET", "options.mixPreset 只能是 old / new，收到：" + str(options.get("mixPreset")))
    if "mixPreset" in options:
        options["mixPreset"] = str(options["mixPreset"]).strip().lower()
    hum = str(options.get("humming") or "auto").strip().lower()
    if hum not in ("auto", "keep", "mute", "off", "manual"):
        return fail_json("BAD_HUMMING", "options.humming 只能是 auto / keep / mute / off / manual，收到：" + str(options.get("humming")))
    options["humming"] = hum
    if "vocalDb" in options:
        try:
            options["vocalDb"] = float(options["vocalDb"])
        except (TypeError, ValueError):
            return fail_json("BAD_VOCAL_DB", "options.vocalDb 必须是数字（dB），收到：" + str(options.get("vocalDb")))

    job_id = sanitize_id(config.get("id") or default_job_id(config, workdir))
    prev = None if args.reset else base.read_json(job_path, None)

    job = {
        "version": 1,
        "id": job_id,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "singer": singer,
        "mode": mode,
        "sources": sources,
        "refs": refs_norm,
        "songs": [],
        "warnings": [],
        "target": target_path,
        "target_meta": (dict(target_meta) if target_meta else None),
        "options": options,
        "stages": {name: blank_stage(name) for name in STAGES},
        "result": {"mp3": None, "report": None, "qaOk": None},
        "cancelled": False,
    }
    if target_meta and not target_meta.get("name"):
        job["target_meta"]["name"] = os.path.splitext(os.path.basename(target_path))[0]
    if isinstance(config.get("config"), dict):
        job["config"] = config["config"]
    if isinstance(config.get("qa"), dict):
        job["qa"] = config["qa"]

    if isinstance(prev, dict) and prev.get("id") == job_id:
        # 重复 init 同一 job：素材没变就保留已有阶段进度，不把进度清空
        same_sources = prev.get("sources") == sources
        same_target = (prev.get("target") or "") == (target_path or "")
        if same_sources and same_target:
            prev_stages = prev.get("stages") if isinstance(prev.get("stages"), dict) else {}
            for name in STAGES:
                rec = prev_stages.get(name)
                if isinstance(rec, dict) and rec.get("status") in ("done", "skipped", "failed", "cancelled", "running"):
                    job["stages"][name] = rec
            job["refs"] = prev.get("refs") or job["refs"]
            job["songs"] = prev.get("songs") or job.get("songs") or []
            job["warnings"] = prev.get("warnings") or []
            job["target_meta"] = prev.get("target_meta") or job["target_meta"]
            job["createdAt"] = prev.get("createdAt") or job["createdAt"]
            job["cancelled"] = bool(prev.get("cancelled"))
            if isinstance(prev.get("result"), dict):
                job["result"] = prev["result"]

    write_job(job, job_path)
    warnings = []
    if not target_path:
        warnings.append("没有目标曲（target）：这个 job 只能跑到 train，出不了成品。")
    if mode != "import":
        warnings.append("抓取模式会联网下载音频，需 --copyright-ack 确认版权风险；不想联网请用 import 模式。")
    say(json.dumps({
        "ok": True,
        "job": job_path,
        "workdir": workdir,
        "id": job_id,
        "singer": singer,
        "mode": mode,
        "target": target_path or None,
        "stages": [{"stage": n, "status": job["stages"][n]["status"]} for n in STAGES],
        "warnings": warnings,
    }, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ run

def load_job_for_run(args):
    job_path = os.path.abspath(args.job)
    job = read_job(job_path)
    if job is None:
        return None, None, job_path
    job["_workdir"] = os.path.dirname(job_path)
    return job, None, job_path


def stage_range(from_stage, to_stage):
    lo = STAGES.index(from_stage) if from_stage else 0
    hi = STAGES.index(to_stage) if to_stage else len(STAGES) - 1
    if lo > hi:
        raise ValueError("--from 不能排在 --to 后面")
    return STAGES[lo:hi + 1]


def copyright_decision(job, cfg):
    sources = job.get("sources") or {}
    is_search = bool(str(sources.get("search") or sources.get("query") or "").strip()) and not sources.get("folder")
    mode = str(job.get("mode") or "import").lower()
    if mode == "import" and not is_search:
        return None
    fetch_cfg = cfg.get("fetch") or {}
    if not fetch_cfg.get("require_copyright_ack", True):
        return None
    opts = job.get("options") or {}
    if opts.get("copyrightAck"):
        return None
    return {
        "code": "COPYRIGHT_ACK_REQUIRED",
        "reason": "抓取模式需用户确认版权风险：加 --copyright-ack（或 options.copyrightAck=true）后重跑。"
                  "下载的音频版权风险由使用者自行承担；不想联网请改用 import 模式提供本地素材文件夹。",
    }


def apply_stage_result(job, name, res):
    rec = job["stages"].setdefault(name, blank_stage(name))
    status = res.get("status") or "failed"
    if status == "needs_decision":
        job["decision"] = {
            "stage": name,
            "code": res.get("code") or "DECISION_REQUIRED",
            "reason": res.get("reason") or "需要用户决策",
            "metrics": res.get("metrics") or {},
            "at": now_iso(),
        }
        return "needs_decision"
    job.pop("decision", None)
    rec["status"] = status
    rec["finishedAt"] = now_iso()
    rec["artifacts"] = res.get("artifacts") or []
    rec["metrics"] = res.get("metrics") or {}
    rec["reason"] = res.get("reason")
    rec["error"] = res.get("error")
    # skipped 时保留旧 hash，避免下次又把整个 stage 重算一遍
    if res.get("inputHash"):
        rec["inputHash"] = res["inputHash"]
    if status == "failed":
        rec["startedAt"] = rec.get("startedAt") or now_iso()
    return status


def update_result(job, name, res):
    metrics = res.get("metrics") or {}
    result = job.setdefault("result", {"mp3": None, "report": None, "qaOk": None})
    if name == "mix":
        result["lufs"] = metrics.get("lufs")
        result["peakDb"] = metrics.get("peakDb")
        result["shift"] = metrics.get("shift")
        result["finalPeak"] = metrics.get("finalPeak")
        result["finalPeakOk"] = metrics.get("finalPeakOk")
    elif name == "qa":
        result["qaOk"] = metrics.get("ok")
        result["qaReport"] = (res.get("artifacts") or [None])[0]
        result["qaProblems"] = metrics.get("problems") if isinstance(metrics, dict) else None
    elif name == "export":
        result["mp3"] = metrics.get("mp3")
        result["report"] = metrics.get("report")
        result["outputDir"] = os.path.dirname(metrics.get("mp3") or "")
        result["qaOk"] = metrics.get("qaOk", result.get("qaOk"))
        result["finalPeak"] = metrics.get("finalPeak", result.get("finalPeak"))
        result["finalPeakOk"] = metrics.get("finalPeakOk", result.get("finalPeakOk"))
        result["mp3PeakMaxDb"] = metrics.get("mp3PeakMaxDb")
        result["warnings"] = metrics.get("warnings") or []
    return result


# ------------------------------------------------------------------ hardware

def _to_gb(mib_text):
    try:
        return round(float(str(mib_text).strip()) / 1024.0, 2)
    except (TypeError, ValueError):
        return None


def probe_hardware(cfg):
    """一次硬件体检：GPU 型号 / 显存总量 / 当前空闲显存；拿不到就标 cpu-fallback。"""
    import shutil as _shutil

    checked = now_iso()
    paths = cfg.get("paths") or {}
    candidates = [str(paths.get("nvidia_smi") or ""), "nvidia-smi"]
    candidates.append(os.path.join(os.environ.get("WINDIR") or "C:/Windows", "System32", "nvidia-smi.exe"))
    exe = ""
    for cand in candidates:
        if not cand:
            continue
        found = _shutil.which(cand) or (cand if os.path.isfile(cand) else "")
        if found:
            exe = found
            break
    if exe:
        try:
            p = subprocess.run(
                [exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = (p.stdout or b"").decode("utf-8", "replace")
            row = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
            if p.returncode == 0 and row:
                parts = [x.strip() for x in row.split(",")]
                return {
                    "ok": True,
                    "source": "nvidia-smi",
                    "checkedAt": checked,
                    "gpu": parts[0] if parts else "",
                    "cuda": True,
                    "cpuFallback": False,
                    "vramTotalGb": _to_gb(parts[1]) if len(parts) > 1 else None,
                    "vramFreeGb": _to_gb(parts[2]) if len(parts) > 2 else None,
                }
        except Exception:  # noqa: BLE001
            pass

    # 退路：用训练环境里的 torch 探一次（机器没有 nvidia-smi 时）
    py = paths.get("applio_python") or paths.get("python") or "python"
    code = (
        "import json,torch;"
        "d={'cuda':bool(torch.cuda.is_available()),'gpu':'','totalGb':0.0,'freeGb':0.0};"
        "d.update({'gpu':torch.cuda.get_device_name(0),"
        "'totalGb':round(torch.cuda.get_device_properties(0).total_memory/1073741824.0,2),"
        "'freeGb':round(torch.cuda.mem_get_info()[0]/1073741824.0,2)}) if d['cuda'] else None;"
        "print('CF_HW='+json.dumps(d))"
    )
    try:
        p = subprocess.run([py, "-c", code], capture_output=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (p.stdout or b"").decode("utf-8", "replace")
        payload = None
        for line in out.splitlines():
            if line.strip().startswith("CF_HW="):
                payload = json.loads(line.strip()[len("CF_HW="):])
        if payload is not None:
            cuda = bool(payload.get("cuda"))
            return {
                "ok": True,
                "source": "torch",
                "checkedAt": checked,
                "gpu": payload.get("gpu") or "",
                "cuda": cuda,
                "cpuFallback": not cuda,
                "vramTotalGb": payload.get("totalGb") if cuda else 0.0,
                "vramFreeGb": payload.get("freeGb") if cuda else 0.0,
            }
    except Exception:  # noqa: BLE001
        pass

    return {
        "ok": False,
        "source": "none",
        "checkedAt": checked,
        "gpu": "",
        "cuda": False,
        "cpuFallback": True,
        "vramTotalGb": 0.0,
        "vramFreeGb": 0.0,
        "error": "未检测到 NVIDIA GPU，也读不到 torch CUDA 信息",
    }


def hardware_warnings(hw, allow_lowmem=False):
    """返回 (warnings, decision)。AMD/Intel 或无 CUDA 的核显 -> GPU_UNSUPPORTED（exit 2）。"""
    warnings = []
    if not hw.get("cuda") or hw.get("cpuFallback"):
        vendor = str(hw.get("vendor") or "").lower()
        names = "、".join(hw.get("gpuNames") or []) or hw.get("gpu") or "未识别显卡"
        if vendor in ("amd", "intel"):
            label = "AMD 显卡" if vendor == "amd" else "Intel 核显"
            warnings.append("%s 不支持：检测到 %s，且 CUDA 不可用" % (label, names))
            return warnings, {
                "code": "GPU_UNSUPPORTED",
                "reason": "不支持 AMD 显卡/核显，需要 NVIDIA CUDA：本管线依赖 PyTorch cu128、MSST、RVC。"
                          "检测到 %s，但 torch CUDA 不可用。请换 NVIDIA 显卡或租用云 GPU；"
                          "AMD Radeon / Intel 核显都不支持。" % names,
            }
        warnings.append("未检测到可用 NVIDIA GPU（cpu-fallback）：CPU 训练约慢 10 倍，管线继续跑")
        return warnings, None
    free = hw.get("vramFreeGb")
    if free is None:
        free = hw.get("vramTotalGb")
    try:
        free = float(free)
    except (TypeError, ValueError):
        free = None
    if free is None:
        return warnings, None
    if free < 8.0:
        warnings.append("当前空闲显存 %.1fG < 8G：训练可能 OOM；建议先关掉其它 GPU 程序" % free)
    if free < 5.0 and not allow_lowmem:
        decision = {
            "code": "LOW_VRAM",
            "reason": "显存不足（当前空闲 %.1fG < 5G）：继续训练有 OOM/失败风险。两种选择："
                      "1) 减少训练步数（options.steps 改小）后重试；2) 加 --allow-lowmem 显式放行。"
                      "也可以先关掉其它占显存的程序再跑。" % free,
        }
        return warnings, decision
    return warnings, None


def _write_hw_log(workdir, hw, warnings):
    path = os.path.join(workdir, "logs", "hw.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (now_iso(), json.dumps(hw, ensure_ascii=False)))
            for w in warnings:
                f.write("[%s] WARNING %s\n" % (now_iso(), w))
    except OSError:
        pass


def cmd_run(args):
    job, _, job_path = load_job_for_run(args)
    if job is None:
        return fail_json("JOB_NOT_FOUND", "找不到或读不出 job.json: " + job_path)

    workdir = job["_workdir"]
    if not getattr(args, "dry_run", False):
        _write_pidfile(workdir, job_path)
    try:
        cfg = load_engine_cfg(args.config, job)
    except Exception as exc:  # noqa: BLE001
        return fail_json("CONFIG_ERROR", str(exc))

    # 硬件体检：一次，结果写 job.hw + logs/hw.log
    if getattr(args, "allow_lowmem", False):
        job.setdefault("options", {})["allowLowmem"] = True
    hw = probe_hardware(cfg)
    if not hw.get("cuda"):
        try:
            from setup import checks as _checks
            _vinfo = _checks.detect_gpu_vendor(cfg)
            hw["vendor"] = _vinfo.get("vendor") or ""
            hw["gpuNames"] = _vinfo.get("names") or []
        except Exception:  # noqa: BLE001
            hw.setdefault("vendor", "")
    else:
        hw.setdefault("vendor", "nvidia")
    job["hw"] = hw
    allow_lowmem = bool((job.get("options") or {}).get("allowLowmem"))
    hw_warnings, hw_decision = hardware_warnings(hw, allow_lowmem)
    if not getattr(args, "dry_run", False):
        write_job(job, job_path)
        _write_hw_log(workdir, hw, hw_warnings)
    for warning in hw_warnings:
        say("[hw] WARNING: " + warning)
    if not hw_decision and isinstance(job.get("decision"), dict) and job["decision"].get("code") == "LOW_VRAM":
        job.pop("decision", None)
    if hw_decision:
        job["decision"] = dict(hw_decision, stage="run", at=now_iso())
        if not getattr(args, "dry_run", False):
            write_job(job, job_path)
        say(json.dumps({"ok": False, "hw": hw, "decision": job["decision"]}, ensure_ascii=False))
        return 2

    # --allow-duet 落到 options，select 会读
    if getattr(args, "allow_duet", False):
        job.setdefault("options", {})["allowDuet"] = True

    # 抓取模式的版权确认（fetch search 必需；只确认一次，写进 job）
    if getattr(args, "copyright_ack", False):
        job.setdefault("options", {})["copyrightAck"] = True

    # 显式复用声库缓存
    if getattr(args, "reuse_bank", ""):
        singer = str(args.reuse_bank or "").strip()
        from stages import train as train_stage
        found = train_stage.find_bank_model(cfg, singer)
        if not found:
            return fail_json(
                "BANK_MODEL_NOT_FOUND",
                "声库缓存里没有歌手 %s 的模型：%s" % (singer, train_stage.bank_singer_dir(cfg, singer)),
            )
        job.setdefault("options", {})["reuseModel"] = found.get("pth") or ""
        job["options"]["reuseIndex"] = found.get("index") or ""
        job["options"]["reuseBankSinger"] = singer
        if not args.dry_run:
            write_job(job, job_path)
        say("[run] 显式复用声库模型：%s（fingerprint=%s）" % (found.get("pth"), found.get("fingerprint")))

    # 重新开始跑就不再是“已完成”；清掉旧 completedAt，报告里的总耗时才会按本次算
    if not args.dry_run and isinstance(job.get("result"), dict):
        job["result"].pop("completedAt", None)

    if job.get("cancelled"):
        if not args.resume:
            return fail_json("JOB_CANCELLED", "job 已取消：用 run --resume 继续（已完成的阶段不会重做）")
        job["cancelled"] = False
        for name in STAGES:
            rec = job["stages"].get(name) or {}
            if rec.get("status") == "cancelled":
                rec["status"] = "pending"
        say("[run] 已清除 cancelled，按 --resume 继续")

    try:
        names = stage_range(args.from_stage, args.to_stage)
    except ValueError as exc:
        return fail_json("BAD_RANGE", str(exc))

    decision = copyright_decision(job, cfg)
    if decision and "fetch" in names:
        job["decision"] = dict(decision, stage="fetch", at=now_iso())
        write_job(job, job_path)
        say(json.dumps({"ok": False, "decision": job["decision"]}, ensure_ascii=False))
        return 2

    say("[run] job=%s stages=%s -> %s%s" % (
        job.get("id"), names[0], names[-1], " (dry-run)" if args.dry_run else ""))

    exit_code = 0
    for name in names:
        rec = job["stages"].setdefault(name, blank_stage(name))
        status = rec.get("status") or "pending"
        if status == "failed" and not args.resume:
            return fail_json(
                "STAGE_FAILED", "阶段 %s 上次失败了：用 run --resume 重试（错误：%s）" % (
                    name, (rec.get("error") or "")[:400]),
                stage=name, job=job_path,
            )
        if status == "cancelled" and not args.resume:
            return fail_json("STAGE_CANCELLED", "阶段 %s 已取消：用 run --resume 继续" % name)
        if status == "running":
            say("[run] 阶段 %s 上次没跑完，按 pending 处理" % name)

        prev_status = status
        rec["status"] = "running"
        rec["startedAt"] = now_iso()
        rec["finishedAt"] = None
        rec["error"] = None
        rec["reason"] = None
        if not args.dry_run:
            write_job(job, job_path)
        # stage 的幂等判断要看历史状态（done/skipped），不是刚写盘的 running
        rec["status"] = prev_status
        if getattr(args, "force", False):
            rec["status"] = "pending"
            rec["inputHash"] = None
            rec["reason"] = "force 重跑"

        base.configure(workdir, name)
        base.log("stage %s 开始（job=%s）" % (name, job.get("id")))
        try:
            module = importlib.import_module("stages." + name)
            res = module.run(job, cfg)
        except KeyboardInterrupt:
            rec["status"] = "cancelled"
            rec["finishedAt"] = now_iso()
            rec["error"] = "用户中断"
            write_job(job, job_path)
            say("[run] 被中断，阶段 %s 标记 cancelled；可用 run --resume 续跑" % name)
            return 1
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            base.log_raw(tb)
            res = {"status": "failed", "artifacts": [], "metrics": {}, "error": str(exc)}
            base.log("stage %s 抛异常：%s" % (name, exc))

        if not isinstance(res, dict):
            res = {"status": "failed", "artifacts": [], "metrics": {}, "error": "stage 返回值不是 dict"}

        outcome = apply_stage_result(job, name, res)
        if outcome == "needs_decision":
            base.log("stage %s 需要用户决策：%s" % (name, job["decision"].get("reason")))
            if not args.dry_run:
                write_job(job, job_path)
            say(json.dumps({"ok": False, "decision": job["decision"]}, ensure_ascii=False))
            return 2

        update_result(job, name, res)
        if not args.dry_run:
            write_job(job, job_path)

        say("[run] %-9s -> %-13s %s" % (name, outcome, (res.get("reason") or res.get("error") or "")[:160]))
        if outcome == "failed":
            if not args.dry_run:
                write_job(job, job_path)
            return fail_json(
                "STAGE_FAILED", "阶段 %s 失败：%s" % (name, (res.get("error") or "")[:600]),
                stage=name, job=job_path,
            )

    if not args.dry_run:
        if names[-1] == STAGES[-1]:
            job.setdefault("result", {})["completedAt"] = now_iso()
        write_job(job, job_path)

    summary = {
        "ok": True,
        "job": job_path,
        "id": job.get("id"),
        "dryRun": bool(args.dry_run),
        "stages": [{"stage": n, "status": (job["stages"].get(n) or {}).get("status")} for n in names],
        "result": job.get("result"),
        "exit": exit_code,
    }
    say(json.dumps(summary, ensure_ascii=False))
    return exit_code


# ------------------------------------------------------------------ status

def cmd_status(args):
    job_path = os.path.abspath(args.job)
    job = read_job(job_path)
    if job is None:
        return fail_json("JOB_NOT_FOUND", "找不到或读不出 job.json: " + job_path)
    workdir = os.path.dirname(job_path)

    stages = []
    for name in STAGES:
        rec = job["stages"].get(name) or {}
        stages.append({
            "stage": name,
            "status": rec.get("status") or "pending",
            "startedAt": rec.get("startedAt"),
            "finishedAt": rec.get("finishedAt"),
            "artifacts": rec.get("artifacts") or [],
            "metrics": rec.get("metrics") or {},
            "error": rec.get("error"),
            "reason": rec.get("reason"),
            "log": rec.get("log") or ("logs/%s.log" % name),
        })
    done = len([s for s in stages if s["status"] in ("done", "skipped")])
    running = next((s for s in stages if s["status"] == "running"), None)
    failed = next((s for s in stages if s["status"] == "failed"), None)

    if args.text:
        say("%s  %s  %s" % (job.get("id"), job.get("singer"), "cancelled" if job.get("cancelled") else ""))
        for s in stages:
            extra = s.get("reason") or s.get("error") or ""
            say("  %-9s %-10s %s" % (s["stage"], s["status"], str(extra)[:100]))
        return 0

    view = dict(job)
    view.pop("_workdir", None)
    view["workdir"] = workdir
    view["done"] = done
    view["total"] = len(stages)
    view["currentStage"] = (running or failed or {}).get("stage")
    view["stages"] = {s["stage"]: {k: v for k, v in s.items() if k != "stage"} for s in stages}
    try:
        cfg = load_engine_cfg(None, job)
        steps = (job.get("options") or {}).get("steps")
        if steps:
            eta = base.estimate_train_seconds(steps, cfg)
            view["trainEta"] = {
                "steps": int(steps),
                "seconds": eta,
                "text": base.format_eta(eta),
                "secPerStepEst": base.sec_per_step(cfg),
            }
    except Exception:  # noqa: BLE001
        pass
    say(json.dumps(view, ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------ cancel

def cmd_cancel(args):
    job_path = os.path.abspath(args.job)
    job = read_job(job_path)
    if job is None:
        return fail_json("JOB_NOT_FOUND", "找不到或读不出 job.json: " + job_path)
    job["cancelled"] = True
    cancelled = []
    for name in STAGES:
        rec = job["stages"][name]
        if rec.get("status") in ("running", "pending"):
            rec["status"] = "cancelled"
            rec["finishedAt"] = now_iso()
            cancelled.append(name)
    write_job(job, job_path)
    say(json.dumps({
        "ok": True,
        "job": job_path,
        "id": job.get("id"),
        "cancelled": cancelled,
        "note": "已标记取消；正在跑的外部进程由调用方结束，之后可用 run --resume 续跑",
    }, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ delete

def _pidfile_path(workdir):
    return os.path.join(workdir, "engine.pid")


def _write_pidfile(workdir, job_path):
    path = _pidfile_path(workdir)
    try:
        os.makedirs(workdir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "job": job_path, "startedAt": now_iso()}, f)
    except OSError:
        return

    def _cleanup():
        try:
            data = base.read_json(path, {}) or {}
            if int(data.get("pid") or 0) == os.getpid():
                os.remove(path)
        except Exception:  # noqa: BLE001
            pass

    atexit.register(_cleanup)


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _norm_cmd(text):
    return str(text or "").replace("/", "\\").lower()


def _job_in_cmdline(cmdline, job_path):
    target = _norm_cmd(os.path.abspath(job_path))
    return bool(target) and target in _norm_cmd(cmdline)


def _scan_python_cmdlines():
    """返回 [(pid, cmdline)]；None 表示两种扫描方式都不可用。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name != "nt":
        try:
            p = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            return None
        pairs = []
        for line in (p.stdout or b"").decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                try:
                    pairs.append((int(parts[0]), parts[1]))
                except ValueError:
                    pass
        return pairs
    try:
        p = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:list"],
            capture_output=True, timeout=15, creationflags=flags,
        )
        text = (p.stdout or b"").decode("utf-8", "replace").replace("\r", "")
        pairs = []
        cur = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                if cur.get("ProcessId") and cur.get("CommandLine"):
                    try:
                        pairs.append((int(cur["ProcessId"]), cur["CommandLine"]))
                    except ValueError:
                        pass
                cur = {}
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                cur[key.strip()] = val.strip()
        if cur.get("ProcessId") and cur.get("CommandLine"):
            try:
                pairs.append((int(cur["ProcessId"]), cur["CommandLine"]))
            except ValueError:
                pass
        if p.returncode == 0:
            return pairs
    except Exception:  # noqa: BLE001
        pass
    try:
        script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                  "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=20, creationflags=flags)
        pairs = []
        for line in (p.stdout or b"").decode("utf-8", "replace").splitlines():
            if "|" in line:
                pid_s, cmd = line.split("|", 1)
                try:
                    pairs.append((int(pid_s.strip()), cmd.strip()))
                except ValueError:
                    pass
        if p.returncode == 0:
            return pairs
    except Exception:  # noqa: BLE001
        pass
    return None


def _running_pids(job_path):
    pairs = _scan_python_cmdlines()
    if pairs is not None:
        pids = set()
        for pid, cmd in pairs:
            if pid == os.getpid():
                continue
            if _job_in_cmdline(cmd, job_path):
                pids.add(pid)
        return sorted(pids)
    # 进程扫描不可用：退回 engine.pid（run 阶段写入）
    data = base.read_json(_pidfile_path(os.path.dirname(job_path)), {}) or {}
    pid = data.get("pid") or 0
    if _pid_alive(pid):
        return [int(pid)]
    return []


def cmd_delete(args):
    job_path = os.path.abspath(args.job)
    if not os.path.isfile(job_path):
        return fail_json("JOB_NOT_FOUND", "找不到 job.json：" + job_path)
    workdir = os.path.dirname(job_path)

    pids = _running_pids(job_path)
    if pids:
        return fail_json(
            "JOB_RUNNING",
            "job 正在运行（python pid %s）：先 cancel（或等它结束）再删除" % ", ".join(str(p) for p in pids),
            pids=pids,
        )

    if args.purge:
        try:
            shutil.rmtree(workdir)
        except Exception as exc:  # noqa: BLE001
            return fail_json("DELETE_FAILED", "物理删除失败（文件可能被占用）：" + str(exc))
        say(json.dumps({"ok": True, "job": job_path, "deleted": workdir}, ensure_ascii=False))
        return 0

    work_root = os.path.dirname(workdir)
    trash = os.path.join(work_root, "_deleted")
    try:
        os.makedirs(trash, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return fail_json("MOVE_FAILED", "建不了 _deleted 目录：" + str(exc))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = os.path.basename(workdir.rstrip("\\/")) or "job"
    dest = os.path.join(trash, "%s-%s" % (base_name, stamp))
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(trash, "%s-%s-%d" % (base_name, stamp, n))
        n += 1
    try:
        shutil.move(workdir, dest)
    except Exception as exc:  # noqa: BLE001
        return fail_json("MOVE_FAILED", "移动到 _deleted 失败：" + str(exc))
    say(json.dumps({"ok": True, "job": job_path, "movedTo": dest}, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------ doctor / setup

def cmd_doctor(args):
    from setup import checks
    cfg = checks.load_config(getattr(args, "config", "") or None)
    if getattr(args, "python", ""):
        cfg.setdefault("paths", {})["python"] = args.python
    payload = checks.doctor_payload(cfg)
    say(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


def cmd_setup(args):
    from setup import bootstrap
    argv = []
    if getattr(args, "install_torch", False):
        argv.append("--install-torch")
        if getattr(args, "install", False):
            argv.append("--install")
    elif getattr(args, "install", False):
        argv.append("--install")
    else:
        argv.append("--check")
    if getattr(args, "install_humming_asr", False):
        argv.append("--install-humming-asr")
    if getattr(args, "install_rvc_deps", False):
        argv.append("--install-rvc-deps")
    if getattr(args, "install_fairseq", False):
        argv.append("--install-fairseq")
    if getattr(args, "install_applio", False):
        argv.append("--install-applio")
    if getattr(args, "with_models", False):
        argv.append("--with-models")
    if getattr(args, "python", ""):
        argv.extend(["--python", args.python])
    if getattr(args, "dry_run", False):
        argv.append("--dry-run")
    if getattr(args, "yes", False):
        argv.append("--yes")
    cfg = getattr(args, "config", "") or ""
    if cfg:
        argv.extend(["--config", cfg])
    return bootstrap.main(argv)


# ------------------------------------------------------------------ main

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(prog="run.py", description="翻唱工厂本地引擎")
    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="从 job 配置生成 job.json")
    p_init.add_argument("--config", required=True)
    p_init.add_argument("--workdir", default="")
    p_init.add_argument("--force", action="store_true", help="兼容参数（默认就是覆盖写）")
    p_init.add_argument("--reset", action="store_true", help="连同已有阶段进度一起重置")

    p_run = sub.add_parser("run", help="按阶段跑管线")
    p_run.add_argument("--job", required=True)
    p_run.add_argument("--workdir", default="")
    p_run.add_argument("--config", default="", help="引擎配置覆盖（JSON）")
    p_run.add_argument("--from", dest="from_stage", default="", choices=[""] + STAGES)
    p_run.add_argument("--to", dest="to_stage", default="", choices=[""] + STAGES)
    p_run.add_argument("--resume", action="store_true")
    p_run.add_argument("--allow-duet", dest="allow_duet", action="store_true")
    p_run.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_run.add_argument("--force", action="store_true", help="忽略幂等签名，强制重跑选中的 stage")
    p_run.add_argument("--copyright-ack", dest="copyright_ack", action="store_true",
                       help="确认抓取模式的版权风险（fetch search 必需）")
    p_run.add_argument("--reuse-bank", dest="reuse_bank", default="",
                       help="显式复用声库缓存里的模型，参数是歌手名")
    p_run.add_argument("--allow-lowmem", dest="allow_lowmem", action="store_true",
                       help="显存 <5G 时仍继续（风险自负，可能 OOM）")

    p_status = sub.add_parser("status", help="输出 job 状态 JSON")
    p_status.add_argument("--job", required=True)
    p_status.add_argument("--text", action="store_true")

    p_cancel = sub.add_parser("cancel", help="标记取消")
    p_cancel.add_argument("--job", required=True)

    p_delete = sub.add_parser("delete", help="删除 job（默认移到 _deleted，--purge 物理删除）")
    p_delete.add_argument("--job", required=True)
    p_delete.add_argument("--purge", action="store_true", help="物理删除整个 workdir（不可恢复）")

    p_doctor = sub.add_parser("doctor", help="依赖体检（JSON；缺项退出码 1）")
    p_doctor.add_argument("--config", default="", help="引擎配置覆盖 JSON（paths 覆盖）")
    p_doctor.add_argument("--python", default="", help="指定检查用的 Python（覆盖 paths.python）")

    p_setup = sub.add_parser("setup", help="依赖检查/安装（bootstrap）")
    p_setup.add_argument("--check", action="store_true", help="只检测（默认）")
    p_setup.add_argument("--install", action="store_true", help="尝试自动安装能自动装的部分（或作为 install-torch 的确认）")
    p_setup.add_argument("--with-models", dest="with_models", action="store_true",
                         help="下载 MSST 代码 + MelBand kim + hubert/rmvpe（默认 dry-run，--yes 才下载）")
    p_setup.add_argument("--install-torch", dest="install_torch", action="store_true",
                         help="按显卡 capability 安装对应 torch（默认 dry-run）")
    p_setup.add_argument("--install-humming-asr", dest="install_humming_asr", action="store_true",
                         help="可选：安装 faster-whisper + small 模型（默认 dry-run）")
    p_setup.add_argument("--install-rvc-deps", dest="install_rvc_deps", action="store_true",
                         help="安装旧版 RVC 运行依赖 numpy/librosa/faiss/pyworld/parselmouth 等（默认 dry-run）")
    p_setup.add_argument("--install-fairseq", dest="install_fairseq", action="store_true",
                         help="安装预编译 fairseq 0.12.2 到 paths.rvc_python（默认 dry-run）")
    p_setup.add_argument("--install-applio", dest="install_applio", action="store_true",
                         help="安装 Applio 训练器（clone + venv + torch + requirements；默认 dry-run）")
    p_setup.add_argument("--python", default="", help="目标 Python 解释器（默认 paths.python）")
    p_setup.add_argument("--dry-run", dest="dry_run", action="store_true", help="只打印 pip 命令")
    p_setup.add_argument("--yes", action="store_true", help="确认真执行安装")
    p_setup.add_argument("--config", default="")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1

    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "cancel":
        return cmd_cancel(args)
    if args.cmd == "delete":
        return cmd_delete(args)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "setup":
        return cmd_setup(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
