#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依赖安装引导：只装能自动装的小东西，大模型/权重只给指引。

    python engine/setup/bootstrap.py --check
    python engine/setup/bootstrap.py --install
    python engine/setup/bootstrap.py --install --config 覆盖.json
    python engine/setup/bootstrap.py --install-torch [--python <exe>] [--dry-run] [--yes]
    python engine/setup/bootstrap.py --install-applio [--python <exe>] [--yes]
    python engine/setup/bootstrap.py --install-fairseq [--yes]

--install 链：torch -> Applio -> 旧版 RVC -> fairseq -> MSST/权重 -> yt-dlp/ffmpeg。
--install-applio：固定 Applio 3.7.1（git 失败回退 codeload zip）-> venv -> torch -> requirements -> import core。
--install-fairseq：预编译 fairseq 0.12.2 zip（sha256 校验）解到 paths.rvc_python 的 site-packages。
每步写 engine/setup/setup.log；单步失败不中断后续；最后输出 JSON 汇总（stdout 只有 JSON，进度走 stderr）。
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

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


def _human_size(num):
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return ("%.1f %s" % (num, unit)) if unit != "B" else ("%d B" % num)
        num /= 1024.0
    return "?"


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _valid_weight(path, expected_size, expected_sha):
    """已存在且大小/sha 匹配；返回 (ok, detail)。"""
    if not os.path.isfile(path):
        return False, "不存在"
    size = os.path.getsize(path)
    if expected_size and size != int(expected_size):
        return False, "大小不符（%s，期望 %s）" % (_human_size(size), _human_size(expected_size))
    if expected_sha:
        got = _sha256_file(path)
        if got.lower() != str(expected_sha).lower():
            return False, "sha256 不符（%s）" % got[:16]
    return True, _human_size(size)


def _http_download(url, part, expected_size, timeout=60):
    existing = os.path.getsize(part) if os.path.isfile(part) else 0
    headers = {"User-Agent": "dsh-cover-factory/1.0"}
    if existing:
        headers["Range"] = "bytes=%d-" % existing
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", 200)
        mode = "ab" if existing and status == 206 else "wb"
        if mode == "wb":
            existing = 0
        total = int(expected_size or 0)
        if not total:
            total = existing + int(resp.headers.get("Content-Length") or 0)
        done = existing
        last = 0.0
        with open(part, mode) as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last >= 2.0:
                    if total:
                        log("    下载 %.1f%%（%s / %s）" % (done * 100.0 / total, _human_size(done), _human_size(total)))
                    else:
                        log("    下载 %s" % _human_size(done))
                    last = now
    real = os.path.getsize(part)
    if expected_size and real != int(expected_size):
        raise RuntimeError("大小不符：%s，期望 %s" % (_human_size(real), _human_size(expected_size)))


def _download_weight(name, url, dest, expected_size, expected_sha, execute):
    if not dest:
        return action(name, "failed", "未配置落点路径", "", "检查 paths.msst_checkpoint / rvc_hubert / rvc_rmvpe")
    ok, detail = _valid_weight(dest, expected_size, expected_sha)
    if ok:
        log("[%s] 已存在，跳过：%s（%s）" % (name, dest, detail))
        return action(name, "skipped", "已存在（%s）：%s" % (detail, url), dest, url)
    if not execute:
        log("[%s] DRY-RUN：需下载 %s -> %s（%s）" % (name, url, dest, _human_size(expected_size)))
        return action(name, "skipped", "dry-run：加 --yes 下载 %s（%s）" % (_human_size(expected_size), url), dest, url)
    if not url:
        log("[%s] 没配置下载 URL" % name)
        return action(name, "failed", "没配置下载 URL", dest, "在 paths.model_sources 里补 %s" % name)
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    part = dest + ".part"
    if os.path.isfile(part) and expected_size and os.path.getsize(part) == int(expected_size):
        ok, detail = _valid_weight(part, expected_size, expected_sha)
        if ok:
            os.replace(part, dest)
            log("[%s] 已下载完整（断点续传校验通过）：%s" % (name, dest))
            return action(name, "done", "下载完成（%s）：%s" % (detail, url), dest, url)
    log("[%s] 下载 %s（%s）-> %s" % (name, url, _human_size(expected_size), dest))
    last_error = ""
    for attempt in range(3):
        try:
            _http_download(url, part, expected_size)
            ok, detail = _valid_weight(part, expected_size, expected_sha)
            if not ok:
                raise RuntimeError(detail)
            os.replace(part, dest)
            log("[%s] 完成：%s（%s）" % (name, dest, detail))
            return action(name, "done", "下载完成（%s）：%s" % (detail, url), dest, url)
        except Exception as exc:  # noqa: BLE001
            last_error = "%s: %s" % (type(exc).__name__, exc)
            log("[%s] 第 %d 次下载失败：%s" % (name, attempt + 1, last_error))
            time.sleep(1.5 * (attempt + 1))
    return action(name, "failed", "下载失败：%s（可手动下载：%s -> %s）" % (last_error[:300], url, dest), dest, url)


def install_models(cfg, execute):
    """MSST 仓库 + MelBand kim + hubert/rmvpe；已存在跳过、断点续传、失败不中断。"""
    paths = cfg.get("paths") or {}
    sources = paths.get("model_sources") or {}
    sizes = paths.get("model_sizes") or {}
    shas = paths.get("model_sha256") or {}
    steps = []

    msst_root = paths.get("msst_root") or ""
    repo_url = sources.get("msst_repo") or ""
    if msst_root and os.path.isdir(msst_root):
        log("[msst_repo] 已存在，跳过：%s" % msst_root)
        steps.append(action("msst_repo", "skipped", "已存在：%s" % msst_root, msst_root, repo_url))
    elif not execute:
        steps.append(action("msst_repo", "skipped", "dry-run：加 --yes 执行 git clone %s -> %s" % (repo_url, msst_root), msst_root, repo_url))
    elif not repo_url:
        steps.append(action("msst_repo", "failed", "没配置 paths.model_sources.msst_repo", msst_root, ""))
    else:
        parent = os.path.dirname(os.path.abspath(msst_root))
        if parent:
            os.makedirs(parent, exist_ok=True)
        log("[msst_repo] git clone --depth 1 %s -> %s" % (repo_url, msst_root))
        rc, out = run_cmd(["git", "clone", "--depth", "1", repo_url, msst_root], timeout=1800)
        if rc == 0 and os.path.isdir(msst_root):
            steps.append(action("msst_repo", "done", "已 clone：%s" % repo_url, msst_root, repo_url))
        else:
            steps.append(action("msst_repo", "failed", "git clone 失败：%s" % (out.strip().splitlines() or ["exit=%s" % rc])[-1][:300], msst_root, repo_url))

    rvc_root = paths.get("rvc_root") or ""
    targets = [
        ("melband_kim", paths.get("msst_checkpoint") or (os.path.join(msst_root, "pretrain", "MelBandRoformer_kim.ckpt") if msst_root else "")),
        ("hubert_base", paths.get("rvc_hubert") or (os.path.join(rvc_root, "assets", "hubert", "hubert_base.pt") if rvc_root else "")),
        ("rmvpe", paths.get("rvc_rmvpe") or (os.path.join(rvc_root, "assets", "rmvpe", "rmvpe.pt") if rvc_root else "")),
    ]
    for name, dest in targets:
        steps.append(_download_weight(name, sources.get(name) or "", dest,
                                     sizes.get(name), shas.get(name), execute))
    return steps


def _normalize_step(step):
    status = step.get("status") or "failed"
    if status in ("already", "manual", "skipped"):
        status = "skipped"
    elif status in ("installed", "done", "cloned", "ok"):
        status = "done"
    else:
        status = "failed"
    return {
        "name": step.get("name") or "unknown",
        "status": status,
        "detail": step.get("detail") or "",
        "path": step.get("path") or "",
    }


def _next_steps(steps):
    out = ["运行 python engine/run.py doctor：全绿（ok=true）才算安装成功"]
    for s in steps:
        if s["status"] == "failed":
            out.append("手动补 %s：%s" % (s["name"], s["detail"]))
    out.append("初始化 job：python engine/run.py init --config <job配置> --workdir <workdir>")
    out.append("重启 DSH（如果通过 install.ps1 装进 DSH profile）")
    return out


FAIRSEQ_DEFAULT_URL = ("https://github.com/LHL473962/dsh-cover-factory/releases/download/"
                       "v0.1.11/fairseq-0.12.2-win-py310.zip")
FAIRSEQ_DEFAULT_SHA = "8ba5a4af1354f69aa383ec12067f2b01d5f374bdceb7e0b78a900d033cf5b368"


def _fairseq_check(py):
    rc, out = run_cmd([py, "-c", "import fairseq;print(fairseq.__version__)"], timeout=120)
    if rc == 0:
        ver = (out.strip().splitlines() or ["?"])[-1].strip()
        return True, ver
    return False, (out.strip().splitlines() or ["import fairseq 失败"])[-1][:240]


def _site_packages(py):
    rc, out = run_cmd([py, "-c", "import site;print(site.getsitepackages()[-1])"], timeout=60)
    if rc != 0:
        return ""
    for line in reversed(out.strip().splitlines()):
        p = line.strip()
        if p and os.path.isdir(p):
            return p
    return ""


def install_fairseq(cfg, execute):
    """预编译 fairseq 0.12.2 解压到 paths.rvc_python 的 site-packages（校验 sha256）。"""
    paths = cfg.get("paths") or {}
    py = paths.get("rvc_python") or ""
    url = paths.get("fairseq_zip_url") or FAIRSEQ_DEFAULT_URL
    sha = paths.get("fairseq_zip_sha256") or FAIRSEQ_DEFAULT_SHA
    if not py or not os.path.isfile(py):
        return [action("fairseq", "failed", "paths.rvc_python 不可用: " + str(py), str(py),
                       "先配 paths.rvc_python（旧版 RVC 的 Python 3.10），或手动下载 " + url)]
    ok, detail = _fairseq_check(py)
    if ok:
        return [action("fairseq", "skipped", "已安装 fairseq " + str(detail), py)]
    if not execute:
        return [action("fairseq", "skipped", "dry-run：加 --yes 下载并安装 " + url, py, url)]

    site = _site_packages(py)
    if not site:
        return [action("fairseq", "failed", "拿不到 site-packages（python 不可用）", py,
                       "检查 paths.rvc_python；或手动下载 " + url)]

    tmpdir = tempfile.mkdtemp(prefix="dsh-fairseq-")
    zpath = os.path.join(tmpdir, "fairseq.zip")
    try:
        log("[fairseq] 下载 %s（约 11.4MB）" % url)
        try:
            _http_download(url, zpath, expected_size=None, timeout=120)
        except Exception as exc:  # noqa: BLE001
            return [action("fairseq", "failed", "下载失败：%s" % exc, zpath, url)]
        got = _sha256_file(zpath)
        if sha and got.lower() != str(sha).lower():
            return [action("fairseq", "failed", "sha256 不符（%s，期望 %s）" % (got[:16], str(sha)[:16]), zpath, url)]
        try:
            with zipfile.ZipFile(zpath) as zf:
                names = zf.namelist()
                bad = [n for n in names if n.startswith("/") or ".." in n.replace("\\", "/").split("/")]
                if bad:
                    return [action("fairseq", "failed", "zip 含不安全路径，已拒绝", zpath, url)]
                zf.extractall(site)
        except Exception as exc:  # noqa: BLE001
            return [action("fairseq", "failed", "解压失败：%s" % exc, zpath, url)]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    deps = ["omegaconf<2.1", "hydra-core<1.1", "sacrebleu", "bitarray", "regex", "tqdm", "cffi"]
    log("[fairseq] 安装依赖：" + " ".join(deps))
    rc, out = run_cmd([py, "-m", "pip", "install", "--disable-pip-version-check"] + deps, timeout=1800)
    if rc != 0:
        return [action("fairseq", "failed", "依赖安装失败（exit=%s）" % rc, py, "手动：%s -m pip install %s" % (py, " ".join(deps)))]
    ok2, detail2 = _fairseq_check(py)
    if not ok2:
        return [action("fairseq", "failed", "导入仍失败：%s" % detail2, py, "手动下载 %s 解压到 %s" % (url, site))]
    return [action("fairseq", "done", "已安装 fairseq %s（预编译包）" % detail2, py)]


APPLIO_REPO = "https://github.com/IAHispano/Applio.git"
APPLIO_TAG = "3.7.1"
APPLIO_COMMIT = "6f69726b140c615c8cc6831a7db8b7c5e48ba8af"


def _venv_python(venv_dir):
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _is_venv(venv_dir):
    return os.path.isfile(os.path.join(venv_dir, "pyvenv.cfg"))


def _applio_requirements(src_root):
    """展开 -r 引用并去掉 torch/torchaudio（它们由 cu 选择逻辑单独装）。"""
    skip = {"torch", "torchaudio", "torchvision"}
    lines = []

    def visit(path, seen):
        if not os.path.isfile(path) or path in seen:
            return
        seen.add(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("-r "):
                    visit(os.path.join(os.path.dirname(path), s[3:].strip()), seen)
                    continue
                pkg = re.split(r"[<>=!~ ]+", s, 1)[0].strip().lower()
                if pkg in skip:
                    continue
                lines.append(s)

    visit(os.path.join(src_root, "requirements.txt"), set())
    return lines


def _pip_torch(py, cfg, timeout=3600):
    detected = detect_gpu(py)
    plan = checks.torch_plan_for(detected.get("capability"), py, detected.get("gpu") or "")
    cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "torch", "torchaudio"]
    if plan.get("index"):
        cmd += ["--index-url", plan["index"]]
    log("[applio] torch：%s（%s）" % (" ".join(cmd), plan.get("torch") or "cpu"))
    return run_cmd(cmd, timeout=timeout), plan


def install_applio_steps(cfg, execute, base_python="", python_override=""):
    """clone Applio（固定 tag）+ venv + torch + requirements + import core 验证。"""
    paths = cfg.get("paths") or {}
    root = paths.get("applio_root") or ""
    py = paths.get("applio_python") or ""
    steps = []
    if not root:
        return [action("applio", "failed", "paths.applio_root 为空", "", "在 config.local.json 配 paths.applio_root")]
    if not py:
        return [action("applio", "failed", "paths.applio_python 为空", "", "在 config.local.json 配 paths.applio_python")]
    py_abs = os.path.abspath(py)
    if os.path.basename(os.path.dirname(py_abs)).lower() not in ("scripts", "bin"):
        return [action("applio_venv", "failed",
                       "paths.applio_python 必须指向 venv 里的 python（.../Scripts/python.exe 或 .../bin/python）: " + py,
                       py, "配 paths.applio_python = <venv>/Scripts/python.exe")]
    venv_dir = os.path.dirname(os.path.dirname(py_abs))
    ready = checks.check_applio(cfg)
    if ready.get("ok"):
        return [action("applio", "skipped", "已安装：" + str(ready.get("found")), root)]
    if not execute:
        return [action("applio", "skipped",
                       "dry-run：加 --yes 安装 Applio %s（约 3–5G / 20–40 分钟）到 %s" % (APPLIO_TAG, root),
                       root, APPLIO_REPO)]

    log("[applio] 目标 %s（tag %s）" % (root, APPLIO_TAG))
    log("[applio] 警告：需要 3–5G 磁盘、约 20–40 分钟（torch + CUDA + requirements）")

    # 1) repo（git 优先；github.com:443 被墙时回退 codeload zip，仍是固定 tag）
    parent = os.path.dirname(os.path.abspath(root))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isdir(os.path.join(root, ".git")):
        rc, out = run_cmd(["git", "-C", root, "fetch", "--tags", "--force"], timeout=1800)
        rc2, out2 = run_cmd(["git", "-C", root, "checkout", "-f", APPLIO_TAG], timeout=600)
        if rc == 0 and rc2 == 0:
            steps.append(action("applio_repo", "skipped", "已存在，checkout %s" % APPLIO_TAG, root))
        elif os.path.isfile(os.path.join(root, "core.py")):
            steps.append(action("applio_repo", "skipped", "git 更新失败但 core.py 已存在，继续：%s" % (
                (out2 or out)[-200:]), root))
        else:
            steps.append(action("applio_repo", "failed", "git fetch/checkout %s 失败：%s" % (
                APPLIO_TAG, (out2 or out)[-300:]), root, APPLIO_REPO))
            return steps
    elif os.path.isdir(root) and os.listdir(root):
        steps.append(action("applio_repo", "skipped", "目录已存在（非 git），跳过 clone", root))
    else:
        rc, out = run_cmd(["git", "clone", "--depth", "1", "--branch", APPLIO_TAG,
                           APPLIO_REPO, root], timeout=1800)
        if rc != 0:
            log("[applio] git clone 失败，回退 codeload zip（tag %s）" % APPLIO_TAG)
            shutil.rmtree(root, ignore_errors=True)
            zurl = "https://codeload.github.com/IAHispano/Applio/zip/refs/tags/" + APPLIO_TAG
            tmp = tempfile.mkdtemp(prefix="dsh-applio-zip-")
            zpath = os.path.join(tmp, "applio.zip")
            try:
                _http_download(zurl, zpath, expected_size=None, timeout=120)
                with zipfile.ZipFile(zpath) as zf:
                    names = zf.namelist()
                    bad = [n for n in names if n.startswith("/") or ".." in n.replace("\\", "/").split("/")]
                    if bad:
                        raise RuntimeError("zip 含不安全路径")
                    zf.extractall(tmp)
                tops = [d for d in os.listdir(tmp)
                        if os.path.isdir(os.path.join(tmp, d)) and os.path.isfile(os.path.join(tmp, d, "core.py"))]
                if not tops:
                    raise RuntimeError("zip 里找不到 core.py")
                shutil.copytree(os.path.join(tmp, tops[0]), root)
                rc = 0
                steps.append(action("applio_repo", "done", "git 不可用，已用 codeload zip 安装 %s" % APPLIO_TAG, root))
            except Exception as exc:  # noqa: BLE001
                steps.append(action("applio_repo", "failed", "git/codeload 都失败：%s" % exc, root,
                                    "手动下载 https://github.com/IAHispano/Applio/archive/refs/tags/%s.zip 解压到 %s" % (APPLIO_TAG, root)))
                shutil.rmtree(tmp, ignore_errors=True)
                return steps
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            run_cmd(["git", "-C", root, "checkout", "-f", APPLIO_COMMIT], timeout=600)
            steps.append(action("applio_repo", "done", "clone %s（commit %s）" % (APPLIO_TAG, APPLIO_COMMIT[:10]), root))

    if not os.path.isfile(os.path.join(root, "core.py")):
        steps.append(action("applio_repo", "failed", "仓库缺 core.py", root, APPLIO_REPO))
        return steps
    driver = os.path.join(root, "dsh_rvc2.py")
    vendor = os.path.join(ENGINE_DIR, "vendor", "applio", "dsh_rvc2.py")
    if not os.path.isfile(driver) and os.path.isfile(vendor):
        try:
            shutil.copy2(vendor, driver)
            log("[applio] 复制训练驱动 dsh_rvc2.py")
        except OSError as exc:
            steps.append(action("applio_repo", "failed", "复制 dsh_rvc2.py 失败：%s" % exc, driver))
            return steps

    # 2) venv
    if os.path.isfile(py) and _is_venv(venv_dir):
        steps.append(action("applio_venv", "skipped", "venv 已存在", py))
    else:
        base = python_override or base_python or paths.get("python") or sys.executable
        base = checks.resolve_python_path({"paths": {"python": base}})
        if not os.path.isfile(base):
            steps.append(action("applio_venv", "failed", "找不到基础 Python: " + str(base), "", "传 --python <python.exe>"))
            return steps
        os.makedirs(venv_dir, exist_ok=True)
        rc, out = run_cmd([base, "-m", "venv", "--clear", venv_dir], timeout=900)
        if rc != 0 or not os.path.isfile(_venv_python(venv_dir)):
            steps.append(action("applio_venv", "failed", "venv 创建失败（exit=%s）：%s" % (
                rc, out[-300:]), venv_dir, "确认 Python 3.10+ 可用"))
            return steps
        py = _venv_python(venv_dir)
        steps.append(action("applio_venv", "done", "venv 已创建", py))

    if not os.path.isfile(py):
        steps.append(action("applio_venv", "failed", "venv python 不存在: " + py, py))
        return steps

    rc, out = run_cmd([py, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"], timeout=60)
    ver = (out.strip().splitlines() or ["?"])[-1].strip() if rc == 0 else "?"
    if ver in ("3.10", "3.9", "3.8", "3.7") or ver == "?":
        steps.append(action(
            "applio_python", "failed",
            "Applio %s 需要 Python >=3.11（requirements numpy 2.4.6 只支持 3.11+），当前 venv=%s" % (APPLIO_TAG, ver),
            py, "用 --python <Python 3.11 解释器> 重新跑 setup --install-applio --yes",
        ))
        return steps

    # 3) torch（先装，再用过滤后的 requirements）
    rc, out = run_cmd([py, "-c", "import torch;print(torch.cuda.is_available())"], timeout=180)
    gpu_present = bool(_gpu_name_from_smi())
    torch_ok = rc == 0 and (out.strip().endswith("True") or not gpu_present)
    if torch_ok:
        steps.append(action("applio_torch", "skipped", "torch 已可用", py))
    else:
        res, plan = _pip_torch(py, cfg)
        if res["returncode"] != 0:
            steps.append(action("applio_torch", "failed", "torch 安装失败（exit=%s）：%s" % (
                res["returncode"], res["tail"][-300:]), py, "手动: %s -m pip install torch torchaudio --index-url %s" % (
                    py, plan.get("index") or "https://pypi.org/simple")))
            return steps
        steps.append(action("applio_torch", "done", "torch 安装完成（%s）" % (plan.get("torch") or "cpu"), py))

    # 4) requirements（过滤 torch/torchaudio）
    req_lines = _applio_requirements(root)
    req_tmp = os.path.join(root, ".dsh-requirements.txt")
    try:
        with open(req_tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(req_lines) + "\n")
    except OSError as exc:
        steps.append(action("applio_requirements", "failed", "写 requirements 失败：%s" % exc, root))
        return steps
    if not req_lines:
        steps.append(action("applio_requirements", "failed", "requirements.txt 为空或读不到", root))
        return steps
    log("[applio] pip install -r .dsh-requirements.txt（%d 条，torch 已单独装）" % len(req_lines))
    rc, out = run_cmd([py, "-m", "pip", "install", "--disable-pip-version-check",
                       "-r", req_tmp], timeout=5400)
    if rc != 0:
        steps.append(action("applio_requirements", "failed", "requirements 安装失败（exit=%s）：%s" % (
            rc, out[-500:]), root, "看 %s；可重跑 --install-applio --yes" % LOG))
        return steps
    steps.append(action("applio_requirements", "done", "requirements 安装完成（%d 条）" % len(req_lines), root))

    # 5) verify
    rc, out = run_cmd([py, "-c", "import core;print('ok')"], timeout=300, cwd=root)
    if rc != 0:
        steps.append(action("applio_verify", "failed", "import core 失败：%s" % out[-400:], root,
                            "手动: cd %s && %s -c \"import core\"" % (root, py)))
        return steps
    rc2, out2 = run_cmd([py, "-c", "import transformers;print(transformers.__version__)"], timeout=180, cwd=root)
    rc3, out3 = run_cmd([py, "-c", "import faiss;print(getattr(faiss,'__version__','ok'))"], timeout=180, cwd=root)
    if rc2 != 0:
        steps.append(action("applio_verify", "failed", "import transformers 失败：%s" % out2[-300:], root,
                            "重跑 --install-applio --yes，或手动 pip install transformers"))
        return steps
    detail = "import core ok；transformers %s" % (out2.strip().splitlines() or ["?"])[-1]
    if rc3 != 0:
        detail += "；faiss 未装（可选，索引训练会降级）"
    steps.append(action("applio_verify", "done", detail, root))
    return steps


def _install_torch_action(cfg, execute, python_override=""):
    before = checks.check_torch(cfg)
    if before.get("ok"):
        return action("torch", "skipped", before.get("found"), before.get("path"))
    if not execute:
        return action("torch", "skipped", "dry-run：加 --yes 安装 torch（按显卡自动选 cu 版本）", before.get("path") or "")
    py = checks.resolve_python_path(cfg, python_override)
    det = detect_gpu(py)
    plan = checks.torch_plan_for(det.get("capability"), py, det.get("gpu") or "")
    cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "torch"]
    if plan.get("index"):
        cmd += ["--index-url", plan["index"]]
    rc, out = run_cmd(cmd, timeout=3600)
    after = checks.check_torch({"paths": dict((cfg.get("paths") or {}), python=py)})
    if rc == 0 and after.get("ok"):
        return action("torch", "done", after.get("found"), py)
    return action("torch", "failed", "torch 安装失败（exit=%s）：%s" % (rc, out[-300:]), py,
                  "手动: %s" % (plan.get("pip") or "pip install torch"))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bootstrap.py", description="翻唱工厂依赖检查/安装")
    parser.add_argument("--check", action="store_true", help="只检测（默认）")
    parser.add_argument("--install", action="store_true", help="尝试自动安装能自动装的部分（或作为 --install-torch 的确认）")
    parser.add_argument("--with-models", dest="with_models", action="store_true",
                        help="下载 MSST 代码 + MelBand kim + hubert/rmvpe（默认 dry-run，--yes 才下载）")
    parser.add_argument("--install-torch", action="store_true", help="按显卡 capability 安装对应 torch（默认 dry-run）")
    parser.add_argument("--install-fairseq", dest="install_fairseq", action="store_true",
                        help="安装预编译 fairseq 0.12.2 到 paths.rvc_python（默认 dry-run）")
    parser.add_argument("--install-applio", dest="install_applio", action="store_true",
                        help="安装 Applio 训练器（clone + venv + torch + requirements；默认 dry-run）")
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

    if args.install_fairseq:
        steps = [_normalize_step(s) for s in install_fairseq(cfg, bool(args.yes))]
        payload = {"ok": all(s["status"] != "failed" for s in steps), "steps": steps,
                   "nextSteps": _next_steps(steps)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    if args.install_applio:
        steps = [_normalize_step(s) for s in install_applio_steps(
            cfg, bool(args.yes), base_python=args.python, python_override=args.python)]
        payload = {"ok": all(s["status"] != "failed" for s in steps), "steps": steps,
                   "nextSteps": _next_steps(steps)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    if args.install or args.with_models:
        steps = []
        if args.install:
            log("==== bootstrap --install start ====")
            chain = []
            chain.append(lambda: _install_torch_action(cfg, bool(args.yes), args.python))
            chain.append(lambda: install_applio_steps(cfg, bool(args.yes), base_python=args.python,
                                                     python_override=args.python))
            chain.append(lambda: [install_old_rvc(cfg)])
            chain.append(lambda: install_fairseq(cfg, bool(args.yes)))
            chain.append(lambda: [install_msst(cfg)])
            chain.append(lambda: [install_ytdlp(cfg)])
            chain.append(lambda: [install_ffmpeg(cfg)])
            for fn in chain:
                try:
                    acts = fn()
                except Exception as exc:  # noqa: BLE001
                    acts = [action("install", "failed", "%s: %s" % (type(exc).__name__, exc))]
                if isinstance(acts, dict):
                    acts = [acts]
                for act in acts:
                    log("step %-16s -> %-9s %s" % (act.get("name"), act.get("status"), str(act.get("detail"))[:200]))
                    steps.append(_normalize_step(act))
            log("==== bootstrap --install done ====")
        if args.with_models:
            log("==== bootstrap --with-models start（%s）====" % ("执行" if args.yes else "dry-run"))
            steps.extend(_normalize_step(s) for s in install_models(cfg, bool(args.yes)))
            log("==== bootstrap --with-models done ====")
        payload = {
            "ok": all(s["status"] != "failed" for s in steps),
            "steps": steps,
            "nextSteps": _next_steps(steps),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    payload = checks.doctor_payload(cfg)
    payload["actions"] = []
    payload["log"] = LOG
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
