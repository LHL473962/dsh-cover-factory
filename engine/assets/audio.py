# -*- coding: utf-8 -*-
"""音频 I/O 与 ffmpeg 探测。

移植自 <DATA_DIR>/out/tuning/preflight.py（probe / loud / ebur128 解析）与
<DATA_DIR>/out/tuning/mix_unity.sh 里的 ffmpeg 定位方式。
"""

import os
import re
import shutil
import subprocess


class AudioError(RuntimeError):
    pass


def find_ffmpeg(paths):
    """按 config 的 paths.ffmpeg / ffmpeg_fallbacks 找 ffmpeg，最后才用 PATH。"""
    cands = []
    if paths:
        cands.append(paths.get("ffmpeg"))
        cands.extend(paths.get("ffmpeg_fallbacks") or [])
    cands.extend(["ffmpeg", "ffmpeg.exe"])
    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c):
            return c
        w = shutil.which(c)
        if w:
            return w
    raise AudioError("找不到 ffmpeg：请在 config_default.json 的 paths.ffmpeg 配置绝对路径")


def _text_run(ff, args, timeout=900):
    p = subprocess.run([ff] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    out = (p.stderr or b"").decode("utf-8", "replace") + (p.stdout or b"").decode("utf-8", "replace")
    return p.returncode, out


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_AUDIO_RE = re.compile(r"Audio:\s*([^,\n]+),\s*(\d+)\s*Hz,\s*([^,\n]+)")


def probe(ff, path, timeout=120):
    """时长 / 采样率 / 声道 / codec。对应 preflight.probe。"""
    rc, out = _text_run(ff, ["-hide_banner", "-i", path], timeout=timeout)
    info = {
        "path": path,
        "ok": False,
        "duration_sec": None,
        "sample_rate": None,
        "channels": None,
        "layout": None,
        "codec": None,
    }
    m = _DUR_RE.search(out)
    if m:
        info["duration_sec"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 3)
    m = _AUDIO_RE.search(out)
    if m:
        info["codec"] = m.group(1).strip()
        info["sample_rate"] = int(m.group(2))
        layout = m.group(3).strip()
        info["layout"] = layout.split()[0]
        if "stereo" in layout:
            info["channels"] = 2
        elif "mono" in layout:
            info["channels"] = 1
    info["ok"] = info["duration_sec"] is not None and info["sample_rate"] is not None
    return info


def _first_db(s):
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:LUFS|dBFS|dB)", s)
    if m:
        return float(m.group(1))
    m = re.search(r":\s*(-?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def loudness(ff, path, timeout=900):
    """整体响度（LUFS）与 sample peak（dBFS）。对应 preflight.loud 的 ebur128 解析。

    比源脚本稳一点：优先读 Summary 段（源脚本会误读第一条 I: -70）。
    """
    rc, out = _text_run(
        ff,
        ["-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
        timeout=timeout,
    )
    lufs = None
    peak = None
    idx = out.rfind("Summary:")
    seg = out[idx:] if idx >= 0 else out
    for line in seg.splitlines():
        s = line.strip()
        if lufs is None and s.startswith("I:"):
            lufs = _first_db(s)
        if peak is None and s.startswith("Peak:"):
            peak = _first_db(s)
    if lufs is None or peak is None:
        for line in reversed(out.splitlines()):
            s = line.strip()
            if lufs is None and s.startswith("I:"):
                lufs = _first_db(s)
            if peak is None and s.startswith("Peak:"):
                peak = _first_db(s)
    return {"lufs": lufs, "peak_db": peak}


def decode(ff, path, sr=16000, channels=1, timeout=900):
    """用 ffmpeg 解成 float32 numpy 数组（单声道 1D / 多声道 (n, ch)）。"""
    try:
        import numpy as np
    except Exception as e:  # pragma: no cover - 环境缺 numpy
        raise AudioError("需要 numpy 才能做音频分析：" + str(e))
    cmd = [
        ff, "-v", "error", "-i", path,
        "-ac", str(int(channels)), "-ar", str(int(sr)),
        "-f", "f32le", "-",
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        raise AudioError("解码失败: " + path + " :: " + (p.stderr or b"").decode("utf-8", "replace")[:300])
    arr = np.frombuffer(p.stdout, dtype="<f4")
    if channels > 1:
        n = len(arr) // int(channels)
        arr = arr[: n * int(channels)].reshape(n, int(channels))
    return arr


def transcode(ff, src, dst, sr=44100, channels=2, timeout=1800):
    """统一转成 PCM16 wav（fetch 用）。"""
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
        "-ac", str(int(channels)), "-ar", str(int(sr)), "-c:a", "pcm_s16le", dst,
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise AudioError("转码失败: " + src + " -> " + dst + " :: " + (p.stderr or b"").decode("utf-8", "replace")[:300])
    return dst


def encode_mp3(ff, src, dst, bitrate="320k", meta=None, timeout=900, extra_gain_db=0.0):
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if extra_gain_db:
        cmd.extend(["-af", "volume=%.2fdB" % float(extra_gain_db)])
    cmd.extend(["-codec:a", "libmp3lame", "-b:a", str(bitrate)])
    for k, v in (meta or {}).items():
        cmd.extend(["-metadata", str(k) + "=" + str(v)])
    cmd.append(dst)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise AudioError("mp3 编码失败: " + src + " -> " + dst + " :: " + (p.stderr or b"").decode("utf-8", "replace")[:300])
    return dst


def encode_mp3_checked(ff, src, dst, bitrate="320k", meta=None, min_peak_db=-1.0, max_retries=2, timeout=900):
    """编码 mp3 -> 测峰值；> min_peak_db 就整体回退 1dB 重新编码，最多 max_retries 次。

    mp3 是有损编码，解码后样点峰值可能比 wav 高（过冲），所以成片要按 mp3 实测峰值验收。
    返回 {path, peakDb, lufs, attempts, gainDb, ok, minPeakDb}。
    """
    gain = 0.0
    attempts = 0
    last = {"path": dst, "peakDb": None, "lufs": None, "attempts": 0, "gainDb": 0.0, "ok": False, "minPeakDb": min_peak_db}
    while True:
        attempts += 1
        encode_mp3(ff, src, dst, bitrate=bitrate, meta=meta, timeout=timeout, extra_gain_db=gain)
        m = loudness(ff, dst, timeout=timeout)
        last = {
            "path": dst,
            "peakDb": m.get("peak_db"),
            "lufs": m.get("lufs"),
            "attempts": attempts,
            "gainDb": round(gain, 2),
            "ok": False,
            "minPeakDb": min_peak_db,
        }
        peak = last["peakDb"]
        if peak is None or float(peak) <= float(min_peak_db):
            last["ok"] = True
            return last
        if attempts > int(max_retries):
            return last
        gain -= 1.0
