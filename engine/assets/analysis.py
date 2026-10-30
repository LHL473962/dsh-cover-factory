# -*- coding: utf-8 -*-
"""F0 / 音色 / 立体声 / 断音率分析。

移植自：
- <DATA_DIR>/out/tuning/phrase_plan.py 的 med_f0（归一化自相关，峰值 > 0.35）
- <DATA_DIR>/out/tuning/artifacts.py 的 gaps（20ms 能量坑）与 f0jit / octave-jump
实现差别：自相关改用 FFT 计算（数学等价，快一个量级）。
"""

import math

import numpy as np


def f0_track(x, sr, fmin=80.0, fmax=800.0, frame_sec=0.064, hop_sec=0.016, thresh=0.35):
    """逐帧 F0；未发声帧为 nan。"""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    frame = max(256, int(round(frame_sec * sr)))
    hop = max(64, int(round(hop_sec * sr)))
    if len(x) < frame:
        return np.array([], dtype=np.float64)
    n_frames = 1 + (len(x) - frame) // hop
    out = np.full(n_frames, np.nan, dtype=np.float64)
    lo = max(2, int(sr / float(fmax)))
    nfft = 1 << int(math.ceil(math.log2(2 * frame)))
    for i in range(n_frames):
        seg = x[i * hop: i * hop + frame].astype(np.float64)
        seg = seg - seg.mean()
        if math.sqrt(float((seg * seg).mean())) < 0.01:
            continue
        spec = np.fft.rfft(seg, nfft)
        ac = np.fft.irfft(spec * np.conj(spec))[:frame]
        ac = ac / (ac[0] + 1e-9)
        hi = min(int(sr / float(fmin)), frame - 1)
        if hi <= lo:
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > thresh:
            out[i] = sr / k
    return out


def f0_summary(f0, male_lo=60.0, male_hi=165.0):
    v = np.asarray(f0, dtype=np.float64)
    v = v[np.isfinite(v) & (v > 0)]
    total_frames = int(len(f0))
    if v.size == 0:
        return {
            "frames": total_frames, "voiced_frames": 0, "median": None, "p5": None, "p95": None,
            "male_ratio": None, "jitter_cent": None, "octave_jump_ratio": None,
        }
    med = float(np.median(v))
    vv = np.where(v > med * 1.7, v / 2.0, v)  # phrase_plan：高八度误判折半
    p5 = float(np.percentile(vv, 5))
    p95 = float(np.percentile(vv, 95))
    d = np.abs(np.diff(np.log2(vv))) if vv.size > 1 else np.array([])
    return {
        "frames": total_frames,
        "voiced_frames": int(v.size),
        "median": round(float(np.median(vv)), 2),
        "p5": round(p5, 2),
        "p95": round(p95, 2),
        "male_ratio": round(float(np.mean((vv >= male_lo) & (vv <= male_hi))), 4),
        "jitter_cent": round(float(np.median(d) * 1200.0), 1) if d.size else 0.0,
        "octave_jump_ratio": round(float(np.mean(d > 0.5)), 4) if d.size else 0.0,
    }


def spectral_centroid(x, sr, frame_sec=0.046, hop_sec=0.023):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    frame = max(256, int(round(frame_sec * sr)))
    hop = max(128, int(round(hop_sec * sr)))
    if len(x) < frame:
        return None
    cents = []
    weights = []
    win = np.hanning(frame)
    freqs = np.fft.rfftfreq(frame, 1.0 / sr)
    for i in range(0, len(x) - frame, hop):
        seg = x[i: i + frame].astype(np.float64)
        seg = seg - seg.mean()
        if math.sqrt(float((seg * seg).mean())) < 0.01:
            continue
        spec = np.abs(np.fft.rfft(seg * win))
        total = float(spec.sum())
        if total <= 0:
            continue
        cents.append(float((freqs * spec).sum() / total))
        weights.append(total)
    if not cents:
        return None
    c = np.array(cents)
    w = np.array(weights)
    return {"median_hz": round(float(np.median(c)), 1), "weighted_hz": round(float((c * w).sum() / w.sum()), 1)}


def stereo_corr(stereo):
    a = np.asarray(stereo, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] < 2 or a.shape[0] < 16:
        return None
    l = a[:, 0]
    r = a[:, 1]
    if l.std() < 1e-6 or r.std() < 1e-6:
        return 0.0
    return round(float(np.corrcoef(l, r)[0, 1]), 4)


def gap_rate(x, sr, win_sec=0.02, floor_db=-40.0, dip_db=12.0):
    """断音率（次/分）。判据照抄 artifacts.gaps：
    某个 20ms 窗不是静音（> -40dB），但比前后各 2 个窗低 12dB 以上。"""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    win = max(1, int(round(win_sec * sr)))
    dur = len(x) / float(sr)
    if len(x) < win * 5:
        return {"count": 0, "per_min": 0.0, "duration_sec": round(dur, 2)}
    n = len(x) // win
    env = np.empty(n, dtype=np.float64)
    for i in range(n):
        seg = x[i * win:(i + 1) * win]
        env[i] = math.sqrt(float((seg * seg).mean()))
    db = 20.0 * np.log10(np.maximum(env, 1e-6))
    count = 0
    for i in range(2, n - 2):
        if db[i] > floor_db and db[i] < db[i - 2] - dip_db and db[i] < db[i + 2] - dip_db:
            count += 1
    per_min = count / (dur / 60.0) if dur > 0 else 0.0
    return {"count": int(count), "per_min": round(per_min, 2), "duration_sec": round(dur, 2)}


def range_coverage(f0, lo, hi, semitones=6.0):
    """落在训练音域 ±N 半音内的帧占比。"""
    v = np.asarray(f0, dtype=np.float64)
    v = v[np.isfinite(v) & (v > 0)]
    if v.size == 0 or not lo or not hi:
        return None
    low = float(lo) * 2.0 ** (-float(semitones) / 12.0)
    high = float(hi) * 2.0 ** (float(semitones) / 12.0)
    return round(float(np.mean((v >= low) & (v <= high))), 4)


def in_range_ratio(f0, lo, hi):
    v = np.asarray(f0, dtype=np.float64)
    v = v[np.isfinite(v) & (v > 0)]
    if v.size == 0 or not lo or not hi:
        return None
    return round(float(np.mean((v >= float(lo)) & (v <= float(hi)))), 4)
