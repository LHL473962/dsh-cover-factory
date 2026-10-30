# -*- coding: utf-8 -*-
"""依赖体检：doctor 与 bootstrap --check 共用。

每项返回 {name, ok, found, path, hint}：
- found 是实际探测到的版本/文件描述
- path 是命中的路径（没有则空串）
- hint 是缺失时的安装提示（命令级；ok 时为空串）
"""

import json
import os
import shutil
import subprocess
import sys

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ENGINE_DIR, "config_default.json")


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def deep_merge(dst, src):
    for key, val in (src or {}).items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], val)
        else:
            dst[key] = val
    return dst


def load_config(override_path=None):
    cfg = read_json(DEFAULT_CONFIG, {}) or {}
    if override_path:
        over = read_json(override_path, None)
        if isinstance(over, dict):
            deep_merge(cfg, over)
    return cfg


def _paths(cfg):
    return cfg.get("paths") or {}


def _is_file(path):
    return bool(path) and os.path.isfile(path)


def _which_or_file(cand):
    if not cand:
        return ""
    found = shutil.which(str(cand))
    if found:
        return found
    p = os.path.abspath(str(cand))
    return p if os.path.isfile(p) else ""


def _run(cmd, timeout=120, cwd=None):
    try:
        proc = subprocess.run(
            [str(c) for c in cmd], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, "%s: %s" % (type(exc).__name__, exc)


def check_python(cfg):
    ver = "%d.%d.%d" % tuple(sys.version_info[:3])
    ok = sys.version_info >= (3, 10)
    return {
        "name": "python",
        "ok": ok,
        "found": ver,
        "path": sys.executable,
        "hint": "" if ok else "安装 Python 3.10+（https://www.python.org/downloads/），或用 DSH 内置运行时",
    }


_TORCH_CACHE = {"data": None}


def resolve_python_path(cfg, explicit=None):
    cand = explicit or (cfg.get("paths") or {}).get("python") or sys.executable
    resolved = _which_or_file(cand)
    return resolved or str(cand)


CU128 = "https://download.pytorch.org/whl/cu128"
CU121 = "https://download.pytorch.org/whl/cu121"
CU118 = "https://download.pytorch.org/whl/cu118"


def torch_plan_for(capability, python_path="", gpu_name=""):
    """按 compute capability 给 torch 安装建议（index / 版本 / 完整 pip 命令）。"""
    py = python_path or "python"

    def make(index, req, alternatives=None, note=""):
        cmd = py + " -m pip install torch"
        if index:
            cmd += " --index-url " + index
        plan = {"index": index, "torch": req, "pip": cmd}
        if alternatives:
            plan["alternatives"] = alternatives
        if note:
            plan["note"] = note
        return plan

    if not capability or len(capability) < 2:
        return make("", "cpu（无 CUDA，训练约慢 10 倍）", None,
                    "没有可用 CUDA：装 CPU 版；GPU 训练请换有 NVIDIA 显卡的机器")
    major, minor = int(capability[0]), int(capability[1])
    if (major, minor) == (12, 0):
        plan = make(CU128, "torch>=2.7（50 系必须 cu128）", None, "sm_120 只有 cu128 轮子带 kernel")
    elif major == 8 and minor >= 9:
        plan = make(CU121, "torch 2.1–2.5", [{"index": CU118, "torch": "torch 2.1–2.5"}],
                    "40 系官方旧版 cu121 / cu118 都可用")
    elif major == 8:
        plan = make(CU118, "torch 2.0–2.1", [{"index": CU121, "torch": "torch 2.1–2.5"}],
                    "30 系 cu118 最稳")
    elif major >= 7:
        plan = make(CU118, "torch 2.0–2.1", None, "20 系及更老可用但慢，建议降步数")
    else:
        plan = make(CU118, "torch 1.13–2.0", None, "更老架构建议 CPU 或云 GPU")
    plan["gpu"] = gpu_name or ""
    plan["capability"] = "sm_%d%d" % (major, minor)
    return plan


def _torch_probe(cfg):
    """跑一次 torch，缓存结果（torch_cuda / gpu_torch_match 共用，避免重复 import）。"""
    if _TORCH_CACHE.get("data") is not None:
        return _TORCH_CACHE["data"]
    paths = _paths(cfg)
    py = paths.get("python") or paths.get("applio_python") or sys.executable
    data = {"ok": False, "python": py, "error": ""}
    if not _is_file(py):
        data["error"] = "找不到 python: " + str(py)
        _TORCH_CACHE["data"] = data
        return data
    code = (
        "import json,torch;"
        "c=torch.cuda.is_available();"
        "d={'torch':torch.__version__,'cuda':bool(c),"
        "'gpu':(torch.cuda.get_device_name(0) if c else ''),"
        "'capability':(list(torch.cuda.get_device_capability(0)) if c else None),"
        "'arch_list':(list(torch.cuda.get_arch_list()) if c else [])};"
        "print('CF='+json.dumps(d))"
    )
    rc, out = _run([py, "-c", code], timeout=180)
    payload = None
    for line in out.splitlines():
        if line.strip().startswith("CF="):
            try:
                payload = json.loads(line.strip()[3:])
            except ValueError:
                payload = None
    if payload is None:
        data["error"] = (out.strip().splitlines() or ["torch 导入失败"])[-1][:200]
    else:
        data.update(payload)
        data["ok"] = True
    _TORCH_CACHE["data"] = data
    return data


def check_torch(cfg):
    hint = ("把 paths.python 指向装好 PyTorch 的 Python 3.10 环境（如 Applio env / rvc-env）；"
            "按显卡选 CUDA 版本安装：python -m pip install torch --index-url https://download.pytorch.org/whl/cu128")
    info = _torch_probe(cfg)
    py = info.get("python") or str(_paths(cfg).get("python") or "")
    if not info.get("ok"):
        return {"name": "torch_cuda", "ok": False, "found": info.get("error") or "torch 导入失败", "path": py, "hint": hint}
    cuda = bool(info.get("cuda"))
    found = "torch %s / CUDA %s%s" % (
        info.get("torch"), "True" if cuda else "False",
        (" / " + info.get("gpu")) if info.get("gpu") else "",
    )
    if not cuda:
        hint = ("torch 能用但 CUDA 不可用（CPU 回退约慢 10 倍）；要 GPU 训练请按显卡重装 CUDA 版 torch："
                "python -m pip install torch --index-url https://download.pytorch.org/whl/cu128，并确认 NVIDIA 驱动正常")
    return {"name": "torch_cuda", "ok": cuda, "found": found, "path": py, "hint": "" if cuda else hint}


def check_gpu_torch_match(cfg, probe=None):
    """显卡 compute capability 是否在当前 torch 的 arch_list 里（不匹配 = 跑不了 GPU kernel）。"""
    info = probe if probe is not None else _torch_probe(cfg)
    py = info.get("python") or str(_paths(cfg).get("python") or "")
    matrix = "50系→cu128，40系→cu121/cu118，30系→cu118"
    if not info.get("ok"):
        return {
            "name": "gpu_torch_match", "ok": False,
            "found": "读不到 torch/CUDA 信息", "path": py,
            "hint": "先修好 torch 检查（" + str(info.get("error") or "torch 不可用") + "）；" + matrix,
            "optional": True,
            "suggestedTorch": torch_plan_for(None, py),
        }
    if not info.get("cuda"):
        return {
            "name": "gpu_torch_match", "ok": False,
            "found": "无 CUDA（cpu-fallback）", "path": py,
            "hint": "没有可用 CUDA：CPU 训练约慢 10 倍（此检查标为可选）；要 GPU 训练需装对应 CUDA 版 torch：" + matrix,
            "optional": True,
            "suggestedTorch": torch_plan_for(None, py),
        }
    cap = info.get("capability") or []
    arches = [str(a).lower() for a in (info.get("arch_list") or [])]
    if len(cap) < 2:
        return {
            "name": "gpu_torch_match", "ok": False,
            "found": "无法读取 compute capability（%s）" % (info.get("gpu") or "GPU"),
            "path": py, "hint": "重装 torch 后重试；" + matrix,
            "suggestedTorch": torch_plan_for(None, py, info.get("gpu") or ""),
        }
    sm = "sm_%d%d" % (int(cap[0]), int(cap[1]))
    matched = sm in arches or (sm + "a") in arches
    gpu = info.get("gpu") or "GPU"
    arches_text = ",".join(info.get("arch_list") or [])
    found = "%s (%s) %s arch_list [%s]" % (gpu, sm, "in" if matched else "NOT in", arches_text)
    plan = torch_plan_for(cap, py, gpu)
    if matched:
        return {"name": "gpu_torch_match", "ok": True, "found": found, "path": py, "hint": "",
                "suggestedTorch": plan}
    command = "%s -m pip install torch --index-url %s" % (py, plan.get("index") or "")
    return {
        "name": "gpu_torch_match", "ok": False, "found": found, "path": py,
        "hint": "当前 torch 不含该架构，需换 cu 版本（%s）。torch=%s，arch_list=%s；建议：python engine/run.py setup --install-torch --python %s（或 %s）" % (
            matrix, info.get("torch") or "?", arches_text, py, command),
        "suggestedTorch": plan,
    }


def check_ffmpeg(cfg):
    paths = _paths(cfg)
    cands = [paths.get("ffmpeg")] + list(paths.get("ffmpeg_fallbacks") or []) + ["ffmpeg", "ffmpeg.exe"]
    exe = ""
    for cand in cands:
        exe = _which_or_file(cand)
        if exe:
            break
    hint = ("python -m pip install imageio-ffmpeg，然后把 paths.ffmpeg 指向 imageio_ffmpeg.get_ffmpeg_exe()；"
            "或从 https://www.gyan.dev/ffmpeg/builds/ 下载解压，把 bin/ffmpeg.exe 写进 paths.ffmpeg")
    if not exe:
        return {"name": "ffmpeg", "ok": False, "found": "未找到 ffmpeg", "path": "", "hint": hint}
    rc, out = _run([exe, "-version"], timeout=30)
    ver = ""
    if out:
        first = out.strip().splitlines()[0] if out.strip() else ""
        parts = first.split()
        if len(parts) >= 3 and parts[0].lower().startswith("ffmpeg"):
            ver = parts[2]
    ok = rc == 0
    return {"name": "ffmpeg", "ok": ok, "found": ("ffmpeg " + ver) if ok else "ffmpeg 无法执行", "path": exe, "hint": "" if ok else hint}


def check_msst(cfg):
    paths = _paths(cfg)
    root = paths.get("msst_root") or ""
    inference = paths.get("msst_inference") or (os.path.join(root, "inference.py") if root else "")
    config = paths.get("msst_config") or ""
    ckpt = paths.get("msst_checkpoint") or ""
    python = paths.get("msst_python") or ""
    missing = []
    if not (root and os.path.isdir(root)):
        missing.append("目录")
    if not _is_file(inference):
        missing.append("inference.py")
    if not _is_file(config):
        missing.append("config yaml")
    if not _is_file(ckpt):
        missing.append("MelBandRoformer_kim.ckpt")
    ok = not missing
    hint = ("git clone https://github.com/ZFTurbo/Music-Source-Separation-Training；"
            "MelBandRoformer_kim.ckpt 需按模型页/许可自行下载（约几百 MB），放到 pretrain/；"
            "然后把 paths.msst_root / msst_config / msst_checkpoint / msst_python 指到实际位置")
    found = "MSST + kim 权重" if ok else ("缺: " + "、".join(missing))
    return {"name": "msst", "ok": ok, "found": found, "path": root, "hint": "" if ok else hint}


def check_applio(cfg):
    paths = _paths(cfg)
    root = paths.get("applio_root") or ""
    script = os.path.join(root, "dsh_rvc2.py") if root else ""
    python = paths.get("applio_python") or ""
    missing = []
    if not (root and os.path.isdir(root)):
        missing.append("目录")
    if not _is_file(script):
        missing.append("dsh_rvc2.py")
    if not _is_file(python):
        missing.append("applio python")
    ok = not missing
    hint = ("git clone https://github.com/IAHispano/Applio；建独立 Python 3.10 env 并装 requirements；"
            "paths.applio_root / paths.applio_python 指过去（dsh_rvc2.py 是本引擎的训练驱动）")
    found = "Applio + dsh_rvc2.py" if ok else ("缺: " + "、".join(missing))
    return {"name": "applio", "ok": ok, "found": found, "path": root, "hint": "" if ok else hint}


def check_old_rvc(cfg):
    paths = _paths(cfg)
    root = paths.get("rvc_root") or ""
    infer = os.path.join(root, "tools", "infer_cli.py") if root else ""
    hubert = paths.get("rvc_hubert") or (os.path.join(root, "assets", "hubert", "hubert_base.pt") if root else "")
    rmvpe = paths.get("rvc_rmvpe") or (os.path.join(root, "assets", "rmvpe", "rmvpe.pt") if root else "")
    python = paths.get("rvc_python") or ""
    missing = []
    if not (root and os.path.isdir(root)):
        missing.append("目录")
    if not _is_file(infer):
        missing.append("tools/infer_cli.py")
    if not _is_file(hubert):
        missing.append("hubert_base.pt")
    if not _is_file(rmvpe):
        missing.append("rmvpe.pt")
    if not _is_file(python):
        missing.append("rvc python")
    ok = not missing
    hint = ("git clone https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI && "
            "git checkout 2.2.231006；把 hubert_base.pt / rmvpe.pt 放到 assets/hubert、assets/rmvpe"
            "（可从本机 rvc-models 复制，见 paths.rvc_models_src）；paths.rvc_root / rvc_python 指过去")
    found = "旧版 RVC 2.2.231006 + hubert/rmvpe" if ok else ("缺: " + "、".join(missing))
    return {"name": "old_rvc", "ok": ok, "found": found, "path": root, "hint": "" if ok else hint}


def python_candidates(cfg, key="ytdlp_python"):
    """把 paths.<key> / <key>_fallbacks / paths.python 解析成去重后的真实 python 路径。"""
    paths = _paths(cfg)
    raw = [paths.get(key), paths.get("python"), sys.executable]
    raw.extend(paths.get(key + "_fallbacks") or [])
    out = []
    for item in raw:
        if not item:
            continue
        resolved = _which_or_file(item)
        if resolved and resolved not in out:
            out.append(resolved)
    return out


def check_ytdlp(cfg):
    paths = _paths(cfg)
    hint = ("python -m pip install -U yt-dlp（在 paths.ytdlp_python 或 ytdlp_python_fallbacks 指向的环境里）；"
            "只有 fetch search 抓取模式需要，import 模式不联网可不用")
    candidates = python_candidates(cfg)
    if not candidates:
        return {"name": "ytdlp", "ok": False, "found": "找不到可用 python", "path": str(paths.get("ytdlp_python") or ""), "hint": hint}
    last = ""
    for py in candidates:
        rc, out = _run([py, "-m", "yt_dlp", "--version"], timeout=90)
        ver = out.strip().splitlines()[-1].strip() if (rc == 0 and out.strip()) else ""
        if rc == 0 and ver:
            return {"name": "ytdlp", "ok": True, "found": "yt-dlp " + ver, "path": py, "hint": ""}
        last = "%s: %s" % (py, (out.strip().splitlines() or ["no yt_dlp"])[-1][:120])
    return {"name": "ytdlp", "ok": False, "found": "yt-dlp 不可用（试过 %d 个 python）" % len(candidates), "path": candidates[0], "hint": hint + "；最后错误：" + last}


def run_checks(cfg):
    return [
        check_python(cfg),
        check_torch(cfg),
        check_gpu_torch_match(cfg),
        check_ffmpeg(cfg),
        check_msst(cfg),
        check_applio(cfg),
        check_old_rvc(cfg),
        check_ytdlp(cfg),
    ]


def doctor_payload(cfg):
    checks = run_checks(cfg)
    # optional=True 的项目（如无 CUDA 时的 gpu_torch_match）不计入整体失败
    ok = all(c.get("ok") or c.get("optional") for c in checks)
    return {"ok": ok, "checks": checks}
