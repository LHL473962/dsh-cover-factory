#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依赖安装引导：只装能自动装的小东西，大模型/权重只给指引。

    python engine/setup/bootstrap.py --check
    python engine/setup/bootstrap.py --install
    python engine/setup/bootstrap.py --install --config 覆盖.json
    python engine/setup/bootstrap.py --install-torch [--python <exe>] [--dry-run] [--yes]

步骤：yt-dlp(pip) -> ffmpeg(pip imageio-ffmpeg/手动) -> 旧版 RVC(git clone + 拷本机权重) -> MSST(只提示)。
--install-torch 单独走：按显卡 capability 选 cu128/cu121/cu118，默认只打印命令（真装要 --yes 或 --install）。
每步写 engine/setup/setup.log；单步失败不中断后续；最后输出 JSON 汇总（stdout 只有 JSON，进度走 stderr）。
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_DIR = os.path.dirname(HERE)
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from setup import checks  # noqa: E402

LOG = os.path.join(HERE, "setup.log")
OLD_RVC_REPO = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
OLD_RVC_TAG = "2.2.231006"


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def log_raw(text):
    if not text:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


def run_cmd(cmd, timeout=600, cwd=None):
    log("$ " + " ".join(str(c) for c in cmd))
    try:
        proc = subprocess.run(
            [str(c) for c in cmd], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or b"").decode("utf-8", "replace")
        rc = proc.returncode
    except Exception as exc:  # noqa: BLE001
        out = "%s: %s" % (type(exc).__name__, exc)
        rc = -1
    log_raw(out)
    tail = "\n".join(out.strip().splitlines()[-6:])
    if tail:
        log("  -> exit=%s %s" % (rc, tail.replace("\n", " | ")[:400]))
    return rc, out


def action(name, status, detail, path="", hint=""):
    return {"name": name, "status": status, "detail": detail, "path": path, "hint": hint}


def _find_in_tree(root, filename, max_depth=5):
    target = filename.lower()
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return ""
    base = root.rstrip("\\/").count(os.sep)
    for cur, dirs, files in os.walk(root):
        if cur.rstrip("\\/").count(os.sep) - base >= max_depth:
            dirs[:] = []
        for name in files:
            if name.lower() == target:
                return os.path.join(cur, name)
    return ""


def _copy_rvc_weights(cfg, rvc_root):
    paths = cfg.get("paths") or {}
    src_root = paths.get("rvc_models_src") or "D:/Apps/rvc-models"
    wanted = [
        ("hubert_base.pt", paths.get("rvc_hubert") or os.path.join(rvc_root, "assets", "hubert", "hubert_base.pt")),
        ("rmvpe.pt", paths.get("rvc_rmvpe") or os.path.join(rvc_root, "assets", "rmvpe", "rmvpe.pt")),
    ]
    copied, missing = [], []
    for name, dst in wanted:
        if os.path.isfile(dst):
            continue
        src = _find_in_tree(src_root, name)
        if not src:
            missing.append(name)
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(dst)
            log("copied %s -> %s" % (src, dst))
        except OSError as exc:
            missing.append("%s (%s)" % (name, exc))
    return copied, missing


def install_ytdlp(cfg):
    before = checks.check_ytdlp(cfg)
    if before.get("ok"):
        return action("yt-dlp", "already", before.get("found"), before.get("path"))
    candidates = checks.python_candidates(cfg)
    py = candidates[0] if candidates else sys.executable
    if not os.path.isfile(py):
        return action("yt-dlp", "manual", "找不到 python: " + str(py), str(py),
                      before.get("hint") or "python -m pip install -U yt-dlp")
    rc, out = run_cmd([py, "-m", "pip", "install", "--upgrade", "yt-dlp", "--disable-pip-version-check"], timeout=900)
    after = checks.check_ytdlp(cfg)
    if after.get("ok"):
        return action("yt-dlp", "installed", after.get("found"), after.get("path"))
    detail = "pip install 失败（exit=%s）" % rc
    return action("yt-dlp", "failed", detail, py, after.get("hint") or "")


def install_ffmpeg(cfg):
    before = checks.check_ffmpeg(cfg)
    if before.get("ok"):
        return action("ffmpeg", "already", before.get("found"), before.get("path"))
    paths = cfg.get("paths") or {}
    py = paths.get("python") or paths.get("applio_python") or sys.executable
    manual = ("手动下载：https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip，"
              "解压后把 bin/ffmpeg.exe 路径写进 paths.ffmpeg")
    if not os.path.isfile(py):
        return action("ffmpeg", "manual", "找不到可用的 python 来 pip 安装", "", manual)
    rc, out = run_cmd([py, "-m", "pip", "install", "imageio-ffmpeg", "--disable-pip-version-check"], timeout=900)
    if rc != 0:
        return action("ffmpeg", "manual", "pip install imageio-ffmpeg 失败（exit=%s）" % rc, "", manual)
    rc2, out2 = run_cmd([py, "-c", "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"], timeout=180)
    exe = ""
    if rc2 == 0:
        for line in reversed(out2.strip().splitlines()):
            cand = line.strip()
            if cand and os.path.isfile(cand):
                exe = cand
                break
    if exe:
        after = checks.check_ffmpeg({"paths": dict(paths, ffmpeg=exe)})
        return action("ffmpeg", "installed", "imageio-ffmpeg 自带的 ffmpeg" + ("（%s）" % after.get("found") if after.get("ok") else ""),
                      exe, "把 config_default.json 的 paths.ffmpeg 设为该路径（或加进 ffmpeg_fallbacks）")
    return action("ffmpeg", "manual", "装上了 imageio-ffmpeg 但没找到 ffmpeg 可执行文件", "", manual)


def install_old_rvc(cfg):
    before = checks.check_old_rvc(cfg)
    if before.get("ok"):
        return action("old_rvc", "already", before.get("found"), before.get("path"))
    paths = cfg.get("paths") or {}
    root = paths.get("rvc_root") or ""
    if not root:
        return action("old_rvc", "manual", "paths.rvc_root 为空", "", before.get("hint") or "")
    if os.path.isdir(root) and os.listdir(root):
        copied, missing = _copy_rvc_weights(cfg, root)
        after = checks.check_old_rvc(cfg)
        detail = "目录已存在，跳过 clone；复制本机权重 %d 个" % len(copied)
        if missing:
            detail += "；仍缺: " + "、".join(missing)
        status = "installed" if after.get("ok") else "manual"
        return action("old_rvc", status, detail, root, "" if after.get("ok") else (after.get("hint") or ""))
    if not shutil.which("git"):
        return action("old_rvc", "manual", "找不到 git", root, before.get("hint") or "")
    parent = os.path.dirname(root)
    if parent:
        os.makedirs(parent, exist_ok=True)
    rc, out = run_cmd(["git", "clone", "--depth", "1", "--branch", OLD_RVC_TAG, OLD_RVC_REPO, root], timeout=1800)
    if rc != 0:
        return action("old_rvc", "failed", "git clone %s 失败（网络/代理？）" % OLD_RVC_TAG, root,
                      before.get("hint") or "")
    run_cmd(["git", "-C", root, "checkout", OLD_RVC_TAG], timeout=300)
    copied, missing = _copy_rvc_weights(cfg, root)
    after = checks.check_old_rvc(cfg)
    detail = "git clone %s 完成；复制本机权重 %d 个" % (OLD_RVC_TAG, len(copied))
    if missing:
        detail += "；仍缺: " + "、".join(missing)
    status = "installed" if after.get("ok") else "manual"
    return action("old_rvc", status, detail, root, "" if after.get("ok") else (after.get("hint") or ""))


def install_msst(cfg):
    before = checks.check_msst(cfg)
    if before.get("ok"):
        return action("msst", "already", before.get("found"), before.get("path"))
    guidance = before.get("hint") or (
        "代码：git clone https://github.com/ZFTurbo/Music-Source-Separation-Training；"
        "MelBandRoformer_kim.ckpt 需按模型页/许可自行下载，放到 pretrain/ 后配置 paths.msst_*"
    )
    log("[msst] 不自动下载大模型，指引：" + guidance)
    return action("msst", "manual", "已打印仓库与权重下载指引（不自动下载大模型）", before.get("path") or "", guidance)


def _gpu_name_from_smi():
    exe = shutil.which("nvidia-smi") or ""
    if not exe:
        sys32 = os.path.join(os.environ.get("WINDIR") or "C:/Windows", "System32", "nvidia-smi.exe")
        exe = sys32 if os.path.isfile(sys32) else ""
    if not exe:
        return ""
    rc, out = run_cmd([exe, "--query-gpu=name", "--format=csv,noheader"], timeout=30)
    if rc != 0:
        return ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return lines[0] if lines else ""


def detect_gpu(py):
    """读目标 python 的 torch；torch 不可用就用 nvidia-smi 型号猜架构。"""
    code = (
        "import json,torch;c=torch.cuda.is_available();"
        "d={'torch':torch.__version__,'cuda':bool(c),"
        "'capability':(list(torch.cuda.get_device_capability(0)) if c else None),"
        "'gpu':(torch.cuda.get_device_name(0) if c else '')};"
        "print('CF='+json.dumps(d))"
    )
    rc, out = run_cmd([py, "-c", code], timeout=180)
    payload = None
    for line in out.splitlines():
        if line.strip().startswith("CF="):
            try:
                payload = json.loads(line.strip()[3:])
            except ValueError:
                payload = None
    if payload is not None:
        payload["source"] = "torch"
        return payload
    name = _gpu_name_from_smi()
    low = (name or "").lower()
    cap = None
    for pat, value in (("rtx 50", [12, 0]), ("rtx 40", [8, 9]), ("rtx 30", [8, 6]), ("rtx 20", [7, 5])):
        if pat in low:
            cap = value
            break
    return {"source": "nvidia-smi", "cuda": bool(name), "capability": cap, "gpu": name, "torch": ""}


def install_torch_mode(cfg, args):
    py = checks.resolve_python_path(cfg, getattr(args, "python", ""))
    detected = detect_gpu(py)
    plan = checks.torch_plan_for(detected.get("capability"), py, detected.get("gpu") or "")
    need_install = bool(args.yes or args.install) and not args.dry_run
    log("=" * 68)
    log("torch 安装：python=%s" % py)
    log("  检测=" + json.dumps(detected, ensure_ascii=False))
    log("  建议=" + str(plan.get("pip") or ""))
    log("  警告：torch 轮子约 2.5–3G；装错 cu 版本会 no kernel image，GPU 训练直接跑不了")
    cmd = [py, "-m", "pip", "install", "torch"]
    if plan.get("index"):
        cmd += ["--index-url", plan["index"]]
    payload = {
        "ok": True,
        "action": "install-torch",
        "python": py,
        "detected": detected,
        "suggestedTorch": plan,
        "command": cmd,
        "dryRun": not need_install,
        "executed": False,
        "log": LOG,
    }
    if not need_install:
        log("  DRY-RUN：只打印，不执行。真装加 --yes（或 --install）")
        return payload, 0
    log("  执行：" + " ".join(cmd))
    rc, out = run_cmd(cmd, timeout=3600)
    payload["executed"] = True
    payload["exitCode"] = rc
    payload["ok"] = rc == 0
    log("  torch 安装%s（exit=%s）" % ("完成" if rc == 0 else "失败", rc))
    return payload, 0 if rc == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bootstrap.py", description="翻唱工厂依赖检查/安装")
    parser.add_argument("--check", action="store_true", help="只检测（默认）")
    parser.add_argument("--install", action="store_true", help="尝试自动安装能自动装的部分（或作为 --install-torch 的确认）")
    parser.add_argument("--install-torch", action="store_true", help="按显卡 capability 安装对应 torch（默认 dry-run）")
    parser.add_argument("--python", default="", help="目标 Python 解释器（默认 paths.python）")
    parser.add_argument("--dry-run", action="store_true", help="只打印 pip 命令，不执行")
    parser.add_argument("--yes", action="store_true", help="确认真执行安装")
    parser.add_argument("--config", default="", help="引擎配置覆盖 JSON（paths 覆盖）")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    cfg = checks.load_config(args.config or None)

    if args.install_torch:
        payload, code = install_torch_mode(cfg, args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return code

    actions = []
    if args.install:
        log("==== bootstrap --install start ====")
        for step in (install_ytdlp, install_ffmpeg, install_old_rvc, install_msst):
            try:
                act = step(cfg)
            except Exception as exc:  # noqa: BLE001
                act = action(step.__name__, "failed", "%s: %s" % (type(exc).__name__, exc))
            log("step %-9s -> %-9s %s" % (act.get("name"), act.get("status"), str(act.get("detail"))[:200]))
            actions.append(act)
        log("==== bootstrap --install done ====")

    payload = checks.doctor_payload(cfg)
    payload["actions"] = actions
    payload["log"] = LOG
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
