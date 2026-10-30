# -*- coding: utf-8 -*-
"""stage 公共设施：日志、外部命令、幂等签名、路径约定。

路径约定（workdir = job.json 所在目录）：
    <workdir>/job.json              唯一状态源
    <workdir>/logs/<stage>.log      每个 stage 一份日志
    <workdir>/work/<stage>/...      各 stage 产物（DESIGN.md 的 work/...）

stage.run(job, cfg) 返回 {status, artifacts, metrics}，可选 reason / error / inputHash。
status 取值：done | skipped | failed | needs_decision（run.py 负责写 job.json）。
"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import time

STAGES = ["fetch", "separate", "select", "clean", "dataset", "train", "convert", "mix", "qa", "export"]

_CTX = {"workdir": None, "stage": None, "log": None}


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def configure(workdir, stage):
    _CTX["workdir"] = os.path.abspath(workdir)
    _CTX["stage"] = stage
    logs = os.path.join(_CTX["workdir"], "logs")
    os.makedirs(logs, exist_ok=True)
    _CTX["log"] = os.path.join(logs, stage + ".log")
    with open(_CTX["log"], "a", encoding="utf-8") as f:
        f.write("\n===== %s stage=%s =====\n" % (now_iso(), stage))


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    p = _CTX.get("log")
    if p:
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def log_raw(text):
    """只写日志文件，不刷屏（外部命令的完整输出）。"""
    p = _CTX.get("log")
    if not p or not text:
        return
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except Exception:
        pass


def workdir(job):
    wd = job.get("_workdir")
    if not wd:
        raise RuntimeError("job 缺少 _workdir：只能用 run.py 调用 engine")
    return os.path.abspath(wd)


def work(job, *parts):
    return os.path.join(workdir(job), "work", *parts)


def rel(job, path):
    if not path:
        return path
    p = os.path.abspath(path)
    try:
        r = os.path.relpath(p, workdir(job))
    except Exception:
        return p
    return r.replace("\\", "/")


def resolve(job, path):
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(workdir(job), path.replace("/", os.sep))


def ensure_dir(path):
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def file_ok(path):
    return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def child_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_cmd(args, cwd=None, env=None, timeout=None):
    """subprocess + 完整输出进日志；返回 returncode/output/tail。"""
    args = [str(a) for a in args]
    log("$ " + " ".join(args))
    if cwd:
        log("  cwd=" + str(cwd))
    t0 = time.time()
    try:
        p = subprocess.run(
            args, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        out = (p.stdout or b"").decode("utf-8", "replace")
        rc = p.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        raw = e.stdout or b""
        out = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        rc = -9
        timed_out = True
        out += "\n[TIMEOUT] 超过 %.0f 秒" % (timeout or 0)
    except FileNotFoundError as e:
        out = "命令不存在: " + str(e)
        rc = -1
        timed_out = False
    except Exception as e:  # noqa: BLE001
        out = "执行失败: " + repr(e)
        rc = -1
        timed_out = False
    log_raw(out)
    tail = "\n".join([ln for ln in out.strip().splitlines()[-30:]])
    if tail:
        log("  ---- 输出尾部 ----\n" + tail)
    elapsed = round(time.time() - t0, 1)
    log("  exit=%s elapsed=%ss%s" % (rc, elapsed, " TIMEOUT" if timed_out else ""))
    return {"returncode": rc, "timed_out": timed_out, "output": out, "tail": tail, "elapsed_sec": elapsed}


def _filesig(path):
    try:
        st = os.stat(path)
    except Exception:
        return "MISSING"
    return "%d:%d" % (st.st_size, st.st_mtime_ns)


def sig(job, inputs, params):
    """输入签名：文件/目录的 size+mtime_ns + 参数 JSON。用于“输入未变则 skipped”。"""
    h = hashlib.sha256()
    h.update(json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
    for raw in sorted(set(str(x) for x in (inputs or []) if x)):
        p = resolve(job, raw)
        if os.path.isdir(p):
            for cur, dirs, files in os.walk(p):
                dirs.sort()
                for name in sorted(files):
                    fp = os.path.join(cur, name)
                    h.update(("%s|%s;" % (os.path.relpath(fp, p).replace("\\", "/"), _filesig(fp))).encode("utf-8"))
        else:
            h.update(("%s|%s;" % (os.path.basename(p), _filesig(p))).encode("utf-8"))
    return h.hexdigest()[:20]


def recon(job, stage, inputs, params, outputs):
    """产物已存在、状态为 done/skipped、输入签名未变 -> True（幂等跳过）。"""
    rec = (job.get("stages") or {}).get(stage) or {}
    status = rec.get("status")
    if status not in ("done", "skipped"):
        return False
    if not rec.get("inputHash"):
        return False
    if rec.get("inputHash") != sig(job, inputs, params):
        return False
    for p in outputs or []:
        if not file_ok(resolve(job, p)):
            return False
    return True


def prev_metrics(job, stage):
    rec = (job.get("stages") or {}).get(stage) or {}
    return rec.get("metrics") or {}


def result(status, artifacts=None, metrics=None, reason=None, error=None, input_hash=None):
    out = {"status": status, "artifacts": artifacts or [], "metrics": metrics or {}}
    if reason:
        out["reason"] = reason
    if error:
        out["error"] = error
    if input_hash:
        out["inputHash"] = input_hash
    return out


def songs(job):
    """当前 job 的素材条目（fetch 写入 job.refs / job.target_meta）。"""
    wd = workdir(job)
    entries = []
    for item in job.get("refs") or []:
        if isinstance(item, str):
            raw = item
            eid = "ref"
            name = os.path.splitext(os.path.basename(raw))[0]
            meta = {}
        elif isinstance(item, dict):
            raw = item.get("path") or item.get("audio") or item.get("source") or ""
            eid = item.get("id") or os.path.splitext(os.path.basename(str(raw)))[0]
            name = item.get("name") or eid
            meta = item
        else:
            continue
        if not raw:
            continue
        p = resolve(job, raw)
        entries.append({
            "id": eid, "name": name, "role": "ref", "raw": p,
            "rawRel": rel(job, p), "weight": meta.get("weight", 1.0),
        })
    tm = job.get("target_meta") or {}
    tpath = tm.get("audio") or ""
    if not tpath and isinstance(job.get("target"), str):
        tpath = job.get("target")
    if tpath:
        p = resolve(job, tpath)
        entries.append({
            "id": "target", "name": tm.get("title") or tm.get("name") or os.path.splitext(os.path.basename(p))[0],
            "role": "target", "raw": p, "rawRel": rel(job, p),
        })
    return entries


def raw_files(job):
    out = []
    for e in songs(job):
        if file_ok(e["raw"]):
            out.append(e["raw"])
    if out:
        return out
    raw_dir = work(job, "raw")
    if os.path.isdir(raw_dir):
        for name in sorted(os.listdir(raw_dir)):
            p = os.path.join(raw_dir, name)
            if os.path.isfile(p) and name.lower().endswith((".wav", ".flac", ".mp3")):
                out.append(p)
    return out




def target_entry(job):
    for e in songs(job):
        if e["role"] == "target":
            return e
    return None


def model_id(job, cfg):
    prefix = str((cfg.get("train") or {}).get("model_id_prefix", "cf"))
    raw = str(job.get("id") or "job")
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", raw).strip("_")[:40] or "job"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    return prefix + "_" + safe + "_" + h


def desktop_dir():
    home = os.path.expanduser("~")
    for name in ("Desktop", "桌面", os.path.join("OneDrive", "Desktop")):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            return p
    return os.path.join(home, "Desktop")


def clamp_int(v, lo, hi, default=0):
    try:
        n = int(round(float(v)))
    except Exception:
        n = default
    return max(lo, min(hi, n))


def sec_per_step(cfg):
    """训练耗时模型：秒/步。默认 0.37（RTX 5060 Laptop 实测，见 README）。"""
    tcfg = cfg.get("train") or {}
    try:
        value = float(tcfg.get("sec_per_step_est", 0.37))
    except (TypeError, ValueError):
        value = 0.37
    return value if value > 0 else 0.37


def estimate_train_seconds(steps, cfg):
    """ETA = steps x sec_per_step_est。只覆盖 train 阶段。"""
    try:
        n = float(steps)
    except (TypeError, ValueError):
        return 0
    return int(round(max(0.0, n) * sec_per_step(cfg)))


def format_duration(seconds):
    seconds = int(max(0, seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d 小时 %d 分" % (h, m)
    if m:
        return "%d 分 %d 秒" % (m, s)
    return "%d 秒" % s


def format_eta(seconds):
    """人类可读 ETA；>=10 分钟按最近 5 分钟取整（与实测表 30/55/110 分钟对齐）。"""
    seconds = int(max(0, seconds or 0))
    if seconds >= 600:
        minutes = int(round(seconds / 300.0)) * 5
        h, m = divmod(minutes, 60)
        if h and m:
            return "约 %d 小时 %d 分" % (h, m)
        if h:
            return "约 %d 小时" % h
        return "约 %d 分钟" % m
    if seconds >= 60:
        return "约 %d 分钟" % int(round(seconds / 60.0))
    return "约 %d 秒" % seconds
