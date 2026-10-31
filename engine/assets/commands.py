# -*- coding: utf-8 -*-
"""外部命令与混音链。

严格移植现有脚本的调用方式，不新增步骤：
- 分离：D:/Apps/MSST/01_人声分离_MSST-GUI/inference.py
- 训练：Applio 根目录的 dsh_rvc2.py（仓库 engine/vendor/applio 提供，env SUIS_MODEL / SUIS_DATASET）
- 换声：D:/Apps/_dl/old_rvc/rvc/tools/infer_cli.py（env weight_root / index_root）
- 混音：本地调音脚本的 filter graph
"""

import os


def _path(cfg, key):
    return (cfg.get("paths") or {}).get(key) or ""


def _py(cfg, key):
    return _path(cfg, key) or _path(cfg, "python")


def _child_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


# ---------------------------------------------------------------- separate

def separate_model(cfg, model_id=None):
    """解析 options.separateModel：kim（默认）/ bs317，返回配置与权重路径。"""
    s = cfg.get("separate") or {}
    models = s.get("models") or {}
    mid = str(model_id or s.get("default_model") or "kim").strip().lower()
    if mid not in models:
        mid = str(s.get("default_model") or "kim").strip().lower()
    m = models.get(mid) or {}
    paths = cfg.get("paths") or {}
    return {
        "id": mid,
        "label": m.get("label") or mid,
        "model_type": m.get("model_type") or s.get("model_type") or "mel_band_roformer",
        "config": paths.get(m.get("config_key") or "msst_config") or "",
        "checkpoint": paths.get(m.get("checkpoint_key") or "msst_checkpoint") or "",
    }


def separate_cmd(cfg, raw_dir, stems_dir, model_id=None):
    s = cfg.get("separate") or {}
    spec = separate_model(cfg, model_id)
    root = _path(cfg, "msst_root")
    cmd = [
        _py(cfg, "msst_python"),
        os.path.join(root, "inference.py"),
        "--model_type", str(spec["model_type"]),
        "--config_path", spec["config"],
        "--start_check_point", spec["checkpoint"],
        "--input_folder", raw_dir,
        "--store_dir", stems_dir,
        "--disable_detailed_pbar",
    ]
    if s.get("extract_instrumental", True):
        cmd.append("--extract_instrumental")
    if s.get("force_cpu"):
        cmd.append("--force_cpu")
    if s.get("device_ids") is not None:
        cmd.extend(["--device_ids", str(int(s.get("device_ids")))])
    return cmd


# ---------------------------------------------------------------- train (Applio)

def applio_env(cfg, model_id, dataset_dir, logs_dir):
    env = _child_env()
    env["SUIS_MODEL"] = model_id
    env["SUIS_DATASET"] = dataset_dir
    env["APPLIO_ROOT"] = _path(cfg, "applio_root")
    env["APPLIO_LOGS_DIR"] = logs_dir
    env["CF_INDEX_ALGO"] = str((cfg.get("train") or {}).get("index_algorithm", "Auto"))
    return env


def applio_driver(cfg):
    """训练驱动：优先配置 paths.applio_driver，其次 Applio 根目录的 dsh_rvc2.py，最后仓库自带 vendor 副本。"""
    explicit = _path(cfg, "applio_driver")
    if explicit:
        return explicit
    root = _path(cfg, "applio_root")
    local = os.path.join(root, "dsh_rvc2.py")
    if os.path.isfile(local):
        return local
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "vendor", "applio", "dsh_rvc2.py")


def applio_cmd(cfg, mode, epochs=None):
    cmd = [_py(cfg, "applio_python"), applio_driver(cfg), mode]
    if mode == "train":
        cmd.append(str(int(epochs)))
    return cmd


def applio_index_cmd(cfg):
    code = (
        "import os,sys;sys.path.insert(0, os.getcwd());"
        "from core import run_index_script;"
        "print(run_index_script(model_name=os.environ['SUIS_MODEL'], "
        "index_algorithm=os.environ.get('CF_INDEX_ALGO', 'Auto')))"
    )
    return [_py(cfg, "applio_python"), "-c", code]


# ---------------------------------------------------------------- convert (old RVC)

def rvc_env(cfg, model_dir):
    env = _child_env()
    env["weight_root"] = model_dir
    env["index_root"] = model_dir
    return env


def rvc_convert_cmd(cfg, model_name, index_path, input_path, opt_path, pitch=0, device=None):
    c = cfg.get("convert") or {}
    root = _path(cfg, "rvc_root")
    cmd = [
        _py(cfg, "rvc_python"),
        os.path.join(root, "tools", "infer_cli.py"),
        "--model_name", model_name,
        "--index_path", index_path or "",
        "--input_path", input_path,
        "--opt_path", opt_path,
        "--f0method", str(c.get("f0_method", "rmvpe")),
        "--device", str(device or c.get("device", "cuda:0")),
        "--index_rate", str(c.get("index_rate", 0.75)),
        "--protect", str(c.get("protect", 0.33)),
        "--filter_radius", str(int(c.get("filter_radius", 3))),
        "--rms_mix_rate", str(c.get("rms_mix_rate", 1.0)),
        "--f0up_key", str(int(pitch)),
        "--resample_sr", str(int(c.get("resample_sr", 0))),
    ]
    # infer_cli 的 --is_half 是 type=bool，传 "False" 也会变 True；所以只在需要半精度时显式传。
    if c.get("is_half", True):
        cmd.extend(["--is_half", "True"])
    return cmd


# ---------------------------------------------------------------- mix

def mix_filter(cfg, ir_path=None, shift=0):
    """返回 (filter_complex, ir_used)。

    graph 与 mix_unity.sh 一致：人声 0dB + 伴奏 0dB，IR 湿 30% 返回加宽 1.8x，
    伴奏 2.8k 让位 -1dB + 加宽 1.15x，1.3:1 glue，aformat stereo。
    """
    m = cfg.get("mix") or {}
    comp = m.get("compressor") or {}
    vocal = (
        "[0:a]aresample=44100,highpass=f=%s,equalizer=f=%s:width_type=q:width=1.4:g=%s"
        % (m.get("highpass", 45), m.get("warm_freq", 260), m.get("warm_gain_db", -1.2))
    )
    if m.get("presence", True):
        vocal += ",equalizer=f=%s:width_type=q:width=1.0:g=%s" % (
            m.get("presence_freq", 9000), m.get("presence_gain_db", 1.6),
        )
    # acompressor 的 attack/release 单位就是毫秒，写数字（源脚本同样是 attack=20:release=250）
    vocal += ",acompressor=threshold=%sdB:ratio=%s:attack=%s:release=%s" % (
        comp.get("threshold_db", -18), comp.get("ratio", 2.0),
        comp.get("attack_ms", 10), comp.get("release_ms", 120),
    )

    inst = "[1:a]aresample=44100"
    shift = int(shift or 0)
    if shift:
        ratio = 2.0 ** (shift / 12.0)
        inst += ",asetrate=%.4f,aresample=44100,atempo=%.6f" % (44100.0 * ratio, 1.0 / ratio)
    inst += ",equalizer=f=2800:width_type=q:width=1.2:g=%s" % m.get("inst_duck_db", -1.0)
    inst += ",extrastereo=m=%s" % m.get("inst_widen", 1.15)

    bus = (
        "[v][i]amix=inputs=2:duration=longest:normalize=0,"
        "acompressor=threshold=%sdB:ratio=%s:attack=20:release=250,"
        "aformat=channel_layouts=stereo[out]"
        % (m.get("glue_threshold_db", -15), m.get("glue_ratio", 1.3))
    )

    if ir_path and os.path.isfile(ir_path):
        graph = (
            vocal
            + ",asplit=2[v1][vs];[vs][2:a]afir=dry=0:wet=1[w0];"
            + "[w0]extrastereo=m=%s,volume=%s[w];[v1][w]amix=inputs=2:normalize=0[v];"
            % (m.get("ir_widen", 1.8), m.get("ir_wet", 0.3))
            + inst + "[i];" + bus
        )
        return graph, True
    graph = vocal + ",anull[v];" + inst + "[i];" + bus
    return graph, False


def mix_render_cmd(ff, cfg, vocal, inst, out, ir_path=None, shift=0):
    graph, ir_used = mix_filter(cfg, ir_path=ir_path, shift=shift)
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", vocal, "-i", inst]
    if ir_used:
        cmd.extend(["-i", ir_path])
    cmd.extend(["-filter_complex", graph, "-map", "[out]", "-ac", "2", "-c:a", "pcm_f32le", out])
    return cmd, graph, ir_used


def mix_master_cmd(ff, cfg, src, dst, gain_db):
    m = cfg.get("mix") or {}
    # level=false：alimiter 默认 level=true 会把输出自动拉回 0dBFS（实测峰值 0.1dBFS），
    # 必须关掉 limit 才真正生效；等响由 mix.py 的二次校正负责。
    # attack 是 lookahead（毫秒）：默认 5ms 会延迟 219 samples，导致与伴奏 0 延迟相关为负。
    # 这里默认 0.1ms（最小允许值，延迟 3 samples），从根上避免“假反相”。
    attack = max(0.1, float(m.get("limiter_attack_ms", 0.1)))
    release = max(1.0, float(m.get("limiter_release_ms", 50)))
    af = "volume=%sdB,alimiter=limit=%sdB:level=false:attack=%s:release=%s" % (
        round(float(gain_db), 2), m.get("limiter_db", -1.5), attack, release)
    return [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-af", af, "-ac", "2", "-c:a", "pcm_s16le", dst]


def mp3_cmd(ff, src, dst, bitrate="320k", meta=None):
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-codec:a", "libmp3lame", "-b:a", str(bitrate)]
    for k, v in (meta or {}).items():
        cmd.extend(["-metadata", str(k) + "=" + str(v)])
    cmd.append(dst)
    return cmd


# ---------------------------------------------------------------- clean

def clean_filter(cfg):
    c = cfg.get("clean") or {}
    thr = float(c.get("silence_db", -45.0))
    pad = max(0, int(c.get("trim_pad_ms", 120)))
    hp = int(c.get("highpass", 45))
    trim = "silenceremove=start_periods=1:start_duration=0:start_threshold=%sdB:start_silence=%s" % (
        thr, round(pad / 1000.0, 3),
    )
    return "highpass=f=%s,%s,areverse,%s,areverse" % (hp, trim, trim)


def clean_cmd(ff, cfg, src, dst):
    sr = int((cfg.get("clean") or {}).get("sample_rate", 44100))
    return [
        ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
        "-af", clean_filter(cfg), "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", dst,
    ]
