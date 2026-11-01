# -*- coding: utf-8 -*-
"""stage 5 train：Applio 训练（dsh_rvc2.py prep / train N），支持断点续训。

断点逻辑：
- 训练断点（G_*.pth / D_*.pth）落在 APPLIO_LOGS_DIR（= job 的 work/model/applio_logs），
  重跑同一条命令时 Applio 自己从最新断点续训，不会从零开始；
- 已导出权重（<model>_<epoch>e_<step>s.pth 或 *_Ne_*.pth）检测到 epoch >= 目标 -> 直接复用；
- 最终把最新权重/索引拷到 work/model/<model_id>/{model.pth,model.index,model.json}。
"""

import hashlib
import json
import os
import re
import shutil

from assets import commands
from stages import base

STAGE = "train"

_EPOCH_PATTERNS = [
    re.compile(r"_(\d+)e_(\d+)s\.pth$"),
    re.compile(r"_(\d+)Ne_(\d+)(?:s)?\.pth$"),
]


def epoch_of(name):
    for pat in _EPOCH_PATTERNS:
        m = pat.search(name)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def latest_weight(exp_dir):
    best = None
    if not os.path.isdir(exp_dir):
        return None
    for name in os.listdir(exp_dir):
        low = name.lower()
        if not low.endswith(".pth"):
            continue
        if low.startswith(("g_", "d_")):
            continue
        ep = epoch_of(name)
        if not ep:
            continue
        if best is None or ep > best[1]:
            best = (os.path.join(exp_dir, name), ep)
    return best


def latest_index(exp_dir):
    best = None
    if not os.path.isdir(exp_dir):
        return None
    for name in os.listdir(exp_dir):
        if name.lower().endswith(".index"):
            p = os.path.join(exp_dir, name)
            if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                best = p
    return best


def target_epochs(job, cfg):
    tcfg = cfg.get("train") or {}
    opts = job.get("options") or {}
    hi = int(tcfg.get("max_epochs", 20000))
    if opts.get("epochs"):
        return base.clamp_int(opts.get("epochs"), 1, hi, 120)
    if tcfg.get("epochs"):
        return base.clamp_int(tcfg.get("epochs"), 1, hi, 120)
    steps = opts.get("steps")
    if steps:
        est = max(1.0, float(tcfg.get("steps_per_epoch_est", 75)))
        n = int(round(float(steps) / est))
        return base.clamp_int(n, int(tcfg.get("min_epochs", 20)), hi, 120)
    return 120


def probe_hw(cfg):
    paths = cfg.get("paths") or {}
    py = paths.get("applio_python") or paths.get("python") or "python"
    code = (
        "import json,torch;"
        "d={'torch':str(torch.__version__)};"
        "d['cuda']=bool(torch.cuda.is_available());"
        "d['vram_gb']=round(torch.cuda.get_device_properties(0).total_memory/1073741824.0,1) if d['cuda'] else 0.0;"
        "d['gpu']=(torch.cuda.get_device_name(0) if d['cuda'] else '');"
        "print('CF_HW='+json.dumps(d))"
    )
    res = base.run_cmd([py, "-c", code], timeout=180)
    hw = None
    for line in res["output"].splitlines():
        if line.strip().startswith("CF_HW="):
            try:
                hw = json.loads(line.strip()[len("CF_HW="):])
            except ValueError:
                hw = None
    if hw is None:
        return {"ok": False, "error": "探测训练环境失败（exit=%s）：%s" % (res["returncode"], res["tail"][-800:])}
    hw["ok"] = True
    return hw


def log_training_warning(cfg):
    """训练开始前的醒目风险提示（显卡吃满 / 电源 / 散热 / 断点）。"""
    tcfg = cfg.get("train") or {}
    try:
        save_every = int(tcfg.get("save_every_epoch", 5))
    except (TypeError, ValueError):
        save_every = 5
    base.log("=" * 72)
    base.log("【训练警告】训练会跑满显卡：请勿同时开其它 GPU 程序（游戏 / 浏览器硬件加速 / 别的训练 / AI 绘图）")
    base.log("【训练警告】请插电源、保持散热、别让机器睡眠；8G 显存满载仍可能 OOM，失败可降步数或加 --allow-lowmem")
    base.log("【训练警告】每 %d epoch 自动存盘（G_*/D_*.pth）；中断后 run --resume 可续训，不会从头再来" % save_every)
    base.log("=" * 72)


def missing_deps(cfg):
    paths = cfg.get("paths") or {}
    missing = []
    root = paths.get("applio_root") or ""
    if not root or not os.path.isdir(root):
        missing.append("paths.applio_root=" + str(root))
    elif not (os.path.isfile(os.path.join(root, "dsh_rvc2.py"))
              or os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                             "vendor", "applio", "dsh_rvc2.py"))):
        missing.append("dsh_rvc2.py 不存在：<applio_root>/dsh_rvc2.py 或 engine/vendor/applio/dsh_rvc2.py 都没有")
    py = paths.get("applio_python") or ""
    if not py or not os.path.isfile(py):
        missing.append("paths.applio_python=" + str(py))
    pre = (cfg.get("train") or {}).get("pretrained_files") or []
    if pre and root:
        for rel in pre:
            fp = os.path.join(root, rel)
            if not os.path.isfile(fp):
                missing.append("预训练权重缺失（不联网，无法自动下载）: " + fp)
    # contentvec 缺失时 Applio 会尝试联网下载；引擎不联网，提前拦下；并且实际 torch.load 一次
    if root:
        embed_file = os.path.join(root, "rvc", "models", "embedders", "contentvec", "pytorch_model.bin")
        if not os.path.isfile(embed_file):
            missing.append("contentvec embedder 缺失（不联网，无法自动下载）: " + embed_file
                           + "；跑 setup --with-models --yes")
        else:
            try:
                import torch
                torch.load(embed_file, map_location="cpu", weights_only=False)
            except Exception as exc:  # noqa: BLE001
                missing.append("contentvec embedder 无法 torch.load：%s: %s（跑 setup --with-models --yes）"
                               % (type(exc).__name__, str(exc)[:160]))
    return missing


def _metrics_from_meta(meta):
    return {
        "epochs": meta.get("epochs"),
        "steps": meta.get("step"),
        "modelId": meta.get("modelId"),
        "fingerprint": meta.get("fingerprint"),
        "reused": meta.get("reused"),
        "model": meta.get("modelPth") or ("work/model/%s/model.pth" % meta.get("modelId")),
    }


# ---------------------------------------------------------------- 声库缓存 / fingerprint

def bank_dir(cfg):
    paths = cfg.get("paths") or {}
    raw = str(paths.get("bankDir") or paths.get("bank_dir") or "").rstrip("/\\")
    return os.path.expanduser(raw) if raw else ""


def bank_singer_dir(cfg, singer):
    root = bank_dir(cfg)
    if not root:
        return ""
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(singer or "unknown")).strip(" ._") or "unknown"
    return os.path.join(root, safe[:60])


def _meta_time(meta):
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(meta.get("createdAt") or meta.get("generatedAt") or "")).timestamp()
    except Exception:
        return 0.0


def find_bank_model(cfg, singer):
    """在 bankDir/<singer>/ 里找最新（或 fingerprint 匹配）的模型。"""
    d = bank_singer_dir(cfg, singer)
    if not d or not os.path.isdir(d):
        return None
    best = None
    for name in sorted(os.listdir(d)):
        if not name.endswith(".meta.json"):
            continue
        meta = base.read_json(os.path.join(d, name), {}) or {}
        fp = meta.get("fingerprint") or name[: -len(".meta.json")]
        pth = meta.get("bankPth") or os.path.join(d, fp + ".pth")
        if not base.file_ok(pth):
            continue
        idx = meta.get("bankIndex") or os.path.join(d, fp + ".index")
        item = {
            "fingerprint": fp,
            "pth": pth,
            "index": idx if base.file_ok(idx) else "",
            "meta": meta,
        }
        if best is None or _meta_time(meta) > _meta_time(best["meta"]):
            best = item
    return best


def resolve_reuse(value):
    """config.reuseModel / options.reuseModel -> {"pth","index"}（支持 pth / 目录 / dict）。"""
    if not value:
        return None
    if isinstance(value, dict):
        pth = str(value.get("pth") or value.get("model") or value.get("path") or "")
        idx = str(value.get("index") or value.get("indexPath") or "")
        if not pth:
            return None
        if not idx:
            cand = os.path.splitext(pth)[0] + ".index"
            idx = cand if base.file_ok(cand) else ""
        return {"pth": pth, "index": idx if base.file_ok(idx) else ""}
    pth = str(value).strip()
    if not pth:
        return None
    if os.path.isdir(pth):
        cand_pth = os.path.join(pth, "model.pth")
        if not base.file_ok(cand_pth):
            pths = [n for n in sorted(os.listdir(pth)) if n.lower().endswith(".pth")]
            cand_pth = os.path.join(pth, pths[0]) if pths else ""
        cand_idx = os.path.join(pth, "model.index")
        if not base.file_ok(cand_idx):
            idxs = [n for n in sorted(os.listdir(pth)) if n.lower().endswith(".index")]
            cand_idx = os.path.join(pth, idxs[0]) if idxs else ""
        if not base.file_ok(cand_pth):
            return None
        return {"pth": cand_pth, "index": cand_idx if base.file_ok(cand_idx) else ""}
    idx = os.path.splitext(pth)[0] + ".index"
    return {"pth": pth, "index": idx if base.file_ok(idx) else ""}


def _material_files(job):
    ds_dir = base.work(job, "dataset", str(job.get("id") or "job"))
    if not os.path.isdir(ds_dir):
        return []
    return [os.path.join(ds_dir, n) for n in sorted(os.listdir(ds_dir)) if n.lower().endswith(".wav")]


def _ref_names(job):
    rep = base.read_json(base.work(job, "select", "report.json"), {}) or {}
    names = [str(s.get("name") or "") for s in (rep.get("songs") or [])
             if s.get("role") == "ref" and s.get("kept") and s.get("name")]
    if names:
        return sorted(names)
    out = []
    rows = job.get("songs") or job.get("refs") or []
    for r in rows:
        if isinstance(r, dict):
            out.append(str(r.get("name") or ""))
        else:
            out.append(os.path.splitext(os.path.basename(str(r)))[0])
    return sorted(out)


def steps_of(job, cfg, epochs=None):
    tcfg = cfg.get("train") or {}
    opts = job.get("options") or {}
    if opts.get("steps"):
        return int(opts.get("steps"))
    est = max(1.0, float(tcfg.get("steps_per_epoch_est", 75)))
    return int(round(float(epochs or target_epochs(job, cfg)) * est))


def _range_gate(job, cfg, dataset_dir):
    """训练前音域覆盖闸：素材 p95 必须覆盖目标曲 p95。"""
    opts = job.get("options") or {}
    if opts.get("allowRangeFail") is True:
        return None
    tcfg = cfg.get("train") or {}
    if tcfg.get("range_gate") is False:
        return None
    target = None
    for root, _, files in os.walk(os.path.join(base.workdir(job), "stems")):
        for f in files:
            if f.lower() == "vocals.wav":
                target = os.path.join(root, f)
                break
        if target:
            break
    if not target or not os.path.isdir(dataset_dir):
        return None
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "range_check.py")
    margin = float(tcfg.get("range_margin_hz", 20))
    try:
        r = subprocess.run([sys.executable, tool, "--target", target, "--materials", dataset_dir,
                            "--margin", str(margin), "--json"], capture_output=True, text=True, timeout=600)
        data = json.loads(r.stdout or "{}")
    except Exception:
        return None
    if data.get("verdict") == "FAIL":
        cov = data.get("coverage") or {}
        need = data.get("required") or {}
        raise RuntimeError("RANGE_COVERAGE_FAIL: 素材 p95=%s Hz，目标需要 >=%s Hz；请补高音区素材或设 options.allowRangeFail=true" % (cov.get("p95"), need.get("p95")))
    return data


def material_fingerprint(job, cfg, epochs=None):
    """sha1(素材文件 size+mtime + steps + 参考曲名集合)。"""
    h = hashlib.sha1()
    for path in _material_files(job):
        try:
            st = os.stat(path)
        except OSError:
            continue
        h.update(("%s|%d|%d;" % (os.path.basename(path), st.st_size, st.st_mtime_ns)).encode("utf-8"))
    h.update(("steps=%d;" % steps_of(job, cfg, epochs)).encode("utf-8"))
    _opts = job.get("options") or {}
    _epochs_fp = _opts.get("epochs") or epochs or target_epochs(job, cfg)
    h.update(("epochs=%s;" % _epochs_fp).encode("utf-8"))
    h.update(("refs=%s" % ",".join(_ref_names(job))).encode("utf-8"))
    return h.hexdigest()


def _artifact(job, path):
    if not path:
        return path
    try:
        rel = os.path.relpath(os.path.abspath(path), base.workdir(job))
    except Exception:
        return path
    if rel.startswith(".."):
        return path
    return rel.replace("\\", "/")


def _write_pointer(job, model_meta, model_id, fingerprint, pth, idx, reused, extra=None):
    meta = {
        "version": 2,
        "generatedAt": base.now_iso(),
        "modelId": model_id,
        "fingerprint": fingerprint,
        "modelPth": pth,
        "indexPath": idx or "",
        "reused": reused,
    }
    for key, val in (extra or {}).items():
        if val is not None:
            meta[key] = val
    base.ensure_dir(os.path.dirname(model_meta))
    base.write_json(model_meta, meta)
    return meta


def _register_bank(cfg, job, fingerprint, pth, idx, meta):
    """把模型登记到 bankDir/<singer>/<fingerprint>.{pth,index,meta.json}；失败不阻塞。"""
    if not (cfg.get("train") or {}).get("bankEnabled", True):
        return {}
    d = bank_singer_dir(cfg, job.get("singer") or "")
    if not d or not base.file_ok(pth):
        return {}
    try:
        base.ensure_dir(d)
        bp = os.path.join(d, fingerprint + ".pth")
        bi = os.path.join(d, fingerprint + ".index")
        if not (base.file_ok(bp) and os.path.getsize(bp) == os.path.getsize(pth)):
            shutil.copy2(pth, bp)
        if idx and base.file_ok(idx):
            if not (base.file_ok(bi) and os.path.getsize(bi) == os.path.getsize(idx)):
                shutil.copy2(idx, bi)
        bank_meta = dict(meta or {})
        sep = ((job.get("separate_model") or {}).get("id")
               or (job.get("options") or {}).get("separateModel") or "kim")
        bank_meta.setdefault("separator", str(sep).strip().lower())
        bank_meta.update({
            "fingerprint": fingerprint,
            "singer": job.get("singer") or "",
            "jobId": job.get("id") or "",
            "bankPth": bp,
            "bankIndex": bi if base.file_ok(bi) else "",
            "registeredAt": base.now_iso(),
        })
        base.write_json(os.path.join(d, fingerprint + ".meta.json"), bank_meta)
        return {"bankPth": bp, "bankIndex": bank_meta["bankIndex"]}
    except Exception as exc:  # noqa: BLE001
        base.log("train: 声库登记失败（不阻塞）：%s" % exc)
        return {"bankError": str(exc)}


def run(job, cfg):
    tcfg = cfg.get("train") or {}
    opts = job.get("options") or {}
    model_id = base.model_id(job, cfg)
    logs_dir = base.work(job, "model", "applio_logs")
    exp_dir = os.path.join(logs_dir, model_id)
    model_dir = base.work(job, "model", model_id)
    model_pth = os.path.join(model_dir, "model.pth")
    model_idx = os.path.join(model_dir, "model.index")
    model_meta = os.path.join(model_dir, "model.json")
    ds_dir = base.work(job, "dataset", str(job.get("id") or "job"))
    manifest_path = os.path.join(ds_dir, "manifest.json")
    manifest = base.read_json(manifest_path, None)
    if not manifest or not manifest.get("files"):
        return base.result("failed", [], {}, error="没有数据集：先跑 dataset（" + ds_dir + "）")

    epochs = target_epochs(job, cfg)
    steps_planned = steps_of(job, cfg, epochs)
    eta_seconds = base.estimate_train_seconds(steps_planned, cfg)
    sr = int(tcfg.get("sample_rate", 40000))
    batch = int(tcfg.get("batch_size", 8))
    ds_sig = base.sig(job, [ds_dir], {"sr": sr, "f0": "rmvpe"})
    fingerprint = material_fingerprint(job, cfg, epochs=epochs)
    params = {
        "epochs": epochs, "batch": batch, "sr": sr, "model_id": model_id,
        "index_algorithm": tcfg.get("index_algorithm", "Auto"),
        "datasetSig": ds_sig, "fingerprint": fingerprint,
    }
    inputs = [ds_dir]

    # 0) 显式复用：--reuse-bank / options.reuseModel / config.reuseModel
    explicit = resolve_reuse(opts.get("reuseModel")) or resolve_reuse(cfg.get("reuseModel"))
    if explicit and base.file_ok(explicit["pth"]):
        _write_pointer(job, model_meta, model_id, fingerprint, explicit["pth"], explicit["index"], "explicit", {
            "datasetSig": ds_sig, "targetEpochs": epochs,
        })
        base.log("train: 显式复用模型 %s（不训练）" % explicit["pth"])
        return base.result(
            "skipped", [_artifact(job, model_meta)],
            {"fingerprint": fingerprint, "reused": "explicit", "model": explicit["pth"], "modelId": model_id},
            reason="显式复用已有模型（config.reuseModel / --reuse-bank）",
        )

    meta = base.read_json(model_meta, {}) or {}
    work_pth = meta.get("modelPth") if base.file_ok(meta.get("modelPth") or "") else (model_pth if base.file_ok(model_pth) else "")
    work_idx = meta.get("indexPath") if base.file_ok(meta.get("indexPath") or "") else (model_idx if base.file_ok(model_idx) else "")

    # 1) job 内已有模型：fingerprint 匹配（老 meta 用 datasetSig + step 迁移）
    legacy_ok = ((not meta.get("fingerprint")) and meta.get("datasetSig") == ds_sig
                 and int(meta.get("step") or 0) == steps_of(job, cfg, epochs))
    if (meta.get("fingerprint") == fingerprint or legacy_ok) and base.file_ok(work_pth):
        bank = _register_bank(cfg, job, fingerprint, work_pth, work_idx, meta)
        _write_pointer(job, model_meta, model_id, fingerprint, work_pth, work_idx, "work", {
            "datasetSig": ds_sig, "epochs": meta.get("epochs"), "step": meta.get("step"),
            "targetEpochs": epochs, "bankPth": bank.get("bankPth"), "bankIndex": bank.get("bankIndex"),
        })
        base.log("train: fingerprint 命中（%s），复用 %s（不重训）" % (fingerprint[:10], work_pth))
        artifacts = [_artifact(job, model_meta)]
        wd = os.path.abspath(base.workdir(job))
        if os.path.abspath(work_pth).startswith(wd):
            artifacts.append(_artifact(job, work_pth))
        if work_idx and os.path.abspath(work_idx).startswith(wd):
            artifacts.append(_artifact(job, work_idx))
        return base.result(
            "skipped", artifacts,
            {"fingerprint": fingerprint, "reused": "work", "epochs": meta.get("epochs"),
             "modelId": model_id, "bankPth": bank.get("bankPth")},
            reason="已有模型 fingerprint 匹配，不重训",
        )

    # 2) 声库缓存命中：bankDir/<singer>/<fingerprint>.pth
    bd = bank_singer_dir(cfg, job.get("singer") or "")
    bank_pth = os.path.join(bd, fingerprint + ".pth") if bd else ""
    if bank_pth and base.file_ok(bank_pth):
        bank_idx = os.path.splitext(bank_pth)[0] + ".index"
        bank_meta = base.read_json(os.path.join(bd, fingerprint + ".meta.json"), {}) or {}
        _write_pointer(job, model_meta, model_id, fingerprint, bank_pth,
                       bank_idx if base.file_ok(bank_idx) else "", "bank", {
                           "datasetSig": ds_sig, "epochs": bank_meta.get("epochs"),
                           "step": bank_meta.get("step"), "targetEpochs": epochs,
                           "bankPth": bank_pth,
                       })
        base.log("train: 声库命中 fingerprint=%s，直接复用 %s（免训练）" % (fingerprint[:10], bank_pth))
        return base.result(
            "skipped", [_artifact(job, model_meta)],
            {"fingerprint": fingerprint, "reused": "bank", "epochs": bank_meta.get("epochs"),
             "modelId": model_id, "bankPth": bank_pth},
            reason="声库缓存 fingerprint 匹配，免训练",
        )

    missing = missing_deps(cfg)
    if missing:
        return base.result("failed", [], {}, error="训练依赖缺失：" + "；".join(missing))

    hw = None
    job_hw = job.get("hw") if isinstance(job.get("hw"), dict) else None
    if job_hw and ("vramTotalGb" in job_hw or "gpu" in job_hw):
        hw = dict(job_hw)
        hw["ok"] = True
        if hw.get("vram_gb") is None:
            hw["vram_gb"] = hw.get("vramTotalGb")
    if not hw:
        hw = probe_hw(cfg)
        if not hw.get("ok"):
            return base.result("failed", [], {}, error=hw.get("error") or "训练环境探测失败")

    min_vram = float(tcfg.get("min_vram_gb", 8.0))
    if not hw.get("cuda") or hw.get("cpuFallback"):
        base.log("【硬件警告】未检测到可用 CUDA：走 CPU 训练（约慢 10 倍），继续执行；"
                 "run 启动时的体检结果在 job.hw / logs/hw.log")
    else:
        vram = hw.get("vram_gb") if hw.get("vram_gb") is not None else hw.get("vramTotalGb")
        try:
            vram = float(vram)
        except (TypeError, ValueError):
            vram = None
        if vram is not None and vram < min_vram:
            base.log("【硬件警告】显存 %.1fG < 建议 %.1fG：训练可能 OOM，继续执行"
                     "（<5G 的硬闸在 run 启动时，可 --allow-lowmem 放行）" % (vram, min_vram))

    base.log("train: 计划 %d 步（%d epoch），预计约 %s（%.2f 秒/步，RTX 5060 Laptop 实测）" % (
        steps_planned, epochs, base.format_eta(eta_seconds), base.sec_per_step(cfg)))
    log_training_warning(cfg)

    applio_root = (cfg.get("paths") or {}).get("applio_root")
    env = commands.applio_env(cfg, model_id, ds_dir, logs_dir)
    timeout = float(tcfg.get("timeout_sec", 43200))
    base.ensure_dir(exp_dir)
    base.ensure_dir(model_dir)

    # ---- prep（含特征提取）：数据集没变且特征已在 -> 跳过
    marker_path = os.path.join(exp_dir, ".dsh_prep.json")
    marker = base.read_json(marker_path, {}) or {}
    feature_dir = os.path.join(exp_dir, "3_feature768")
    need_prep = not (marker.get("datasetSig") == ds_sig and os.path.isdir(feature_dir))
    prep_elapsed = 0.0
    if need_prep:
        base.log("train: 预处理/特征提取（dataset=%s）" % ds_dir)
        res = base.run_cmd(commands.applio_cmd(cfg, "prep"), cwd=applio_root, env=env, timeout=timeout)
        prep_elapsed = res["elapsed_sec"]
        if res["returncode"] != 0:
            return base.result(
                "failed", [], {"epochs": epochs},
                error="预处理失败（exit=%s%s）：%s" % (res["returncode"], "，超时" if res["timed_out"] else "", res["tail"][-1200:]),
            )
        base.write_json(marker_path, {"datasetSig": ds_sig, "at": base.now_iso(), "sampleRate": sr})
    else:
        base.log("train: 数据集未变且已有特征，跳过预处理")

    # ---- 断点续训：先看是否已有达标权重
    latest = latest_weight(exp_dir)
    resumed = bool(os.path.isdir(exp_dir) and any(
        n.lower().startswith(("g_", "d_")) for n in os.listdir(exp_dir)
    ))
    train_elapsed = 0.0
    if latest and latest[1][0] >= epochs and base.file_ok(latest[0]):
        base.log("train: 已有 %d epoch 权重（%s），达到目标 %d epoch，跳过训练"
                 % (latest[1][0], os.path.basename(latest[0]), epochs))
    else:
        _range_gate(job, cfg, ds_dir)
        base.log("train: 开始训练 %d epoch（%s）" % (epochs, "断点续训" if resumed else "从头开始"))
        res = base.run_cmd(
            commands.applio_cmd(cfg, "train", epochs),
            cwd=applio_root, env=env, timeout=timeout,
        )
        train_elapsed = res["elapsed_sec"]
        if res["returncode"] != 0:
            return base.result(
                "failed", [], {"epochs": epochs, "resumed": resumed, "elapsed_sec": train_elapsed},
                error="训练失败（exit=%s%s）。断点已保留，可 run --resume 续训。尾部：%s" % (
                    res["returncode"], "，超时" if res["timed_out"] else "", res["tail"][-1500:],
                ),
            )
        latest = latest_weight(exp_dir)
        if latest is None:
            return base.result(
                "failed", [], {"epochs": epochs},
                error="训练进程结束但没有找到权重（期望 *e_*s.pth 或 *_Ne_*.pth）在 " + exp_dir,
            )

    # ---- index（dsh_rvc2 train 内部已生成；缺了就补跑）
    idx = latest_index(exp_dir)
    idx_elapsed = 0.0
    if not idx:
        base.log("train: 补生成 index")
        res = base.run_cmd(commands.applio_index_cmd(cfg), cwd=applio_root, env=env, timeout=1800)
        idx_elapsed = res["elapsed_sec"]
        if res["returncode"] != 0:
            return base.result(
                "failed", [], {"epochs": epochs},
                error="index 生成失败（exit=%s）：%s" % (res["returncode"], res["tail"][-1000:]),
            )
        idx = latest_index(exp_dir)

    # ---- 导出到 work/model/<model_id>/
    shutil.copy2(latest[0], model_pth)
    if idx:
        shutil.copy2(idx, model_idx)
    ep, step = latest[1]
    idx_path = model_idx if base.file_ok(model_idx) else (idx or "")
    meta = {
        "version": 2,
        "generatedAt": base.now_iso(),
        "modelId": model_id,
        "fingerprint": fingerprint,
        "modelPth": model_pth,
        "indexPath": idx_path,
        "reused": "trained",
        "epochs": ep,
        "step": step,
        "datasetSig": ds_sig,
        "sampleRate": sr,
        "batchSize": batch,
        "source": latest[0],
        "index": idx,
        "targetEpochs": epochs,
        "separator": ((job.get("separate_model") or {}).get("id")
                      or (job.get("options") or {}).get("separateModel") or "kim"),
    }
    bank = _register_bank(cfg, job, fingerprint, model_pth, idx_path, meta)
    if bank.get("bankPth"):
        meta["bankPth"] = bank["bankPth"]
        meta["bankIndex"] = bank.get("bankIndex") or ""
    base.write_json(model_meta, meta)

    sec_per_step_actual = None
    if not resumed and train_elapsed and step:
        sec_per_step_actual = round(train_elapsed / float(step), 3)

    artifacts = [base.rel(job, model_pth)]
    if base.file_ok(model_idx):
        artifacts.append(base.rel(job, model_idx))
    artifacts.append(base.rel(job, model_meta))
    metrics = {
        "epochs": ep,
        "steps": step,
        "targetEpochs": epochs,
        "modelId": model_id,
        "fingerprint": fingerprint,
        "reused": "trained",
        "bankPth": meta.get("bankPth"),
        "resumed": resumed,
        "cpuFallback": bool(hw.get("cpuFallback")),
        "stepsPlanned": steps_planned,
        "etaSec": eta_seconds,
        "etaText": base.format_eta(eta_seconds),
        "secPerStepActual": sec_per_step_actual,
        "prepElapsed": round(prep_elapsed, 1),
        "trainElapsed": round(train_elapsed, 1),
        "indexElapsed": round(idx_elapsed, 1),
        "vramGb": hw.get("vram_gb"),
    }
    base.log("train: 完成 %s（%d epoch / %d step）" % (model_id, ep, step))
    return base.result("done", artifacts, metrics, input_hash=base.sig(job, inputs, params))
