# -*- coding: utf-8 -*-
"""stage 7 mix：把换声人声和目标曲伴奏按固化混音链合成。

链与本地调音脚本一致（见 assets/commands.py mix_filter）：
人声 0dB + highpass 45 + 260Hz -1.2 + 9k +1.6 + 2:1 轻压缩；
IR 1.2s 湿 30% 返回加宽 1.8x；伴奏 0dB + 2.8k 让位 -1 + 加宽 1.15x；
总线 1.3:1 glue -> 静态等响 (-14 LUFS) -> limiter -1.5dB。
"""

import os

import numpy as np

from assets import audio, commands
from stages import base

STAGE = "mix"


def _decode_mono(ff, path, sr=44100):
    x = audio.decode(ff, path, sr, 2)
    return x.mean(axis=1) if x.ndim > 1 else x


def _best_corr(ff, mix_path, inst_path, max_lag=300):
    """返回 (0 延迟相关, ±max_lag 内最佳相关, lag)。

    alimiter 的 lookahead 会让输出整体延迟（攻击 5ms ≈ 219 samples），
    0 延迟相关会假性变负；先按 ±max_lag 对齐再判断极性。
    """
    a = _decode_mono(ff, mix_path).astype(np.float64)
    b = _decode_mono(ff, inst_path).astype(np.float64)
    n = min(len(a), len(b))
    if n < 256:
        return 0.0, 0.0, 0
    a = a[:n]
    b = b[:n]
    c0 = float(np.corrcoef(a, b)[0, 1]) if float(a.std()) > 1e-9 and float(b.std()) > 1e-9 else 0.0
    aa = a - a.mean()
    bb = b - b.mean()
    denom = float(np.sqrt(np.sum(aa * aa) * np.sum(bb * bb)))
    if denom <= 1e-12:
        return c0, c0, 0
    cc = np.fft.irfft(np.fft.rfft(aa) * np.conj(np.fft.rfft(bb)), n=n)
    limit = max(0, min(int(max_lag), n // 2 - 1))
    best_lag = 0
    best = c0
    for lag in range(-limit, limit + 1):
        val = float(cc[lag % n]) / denom
        if abs(val) > abs(best):
            best = val
            best_lag = lag
    return c0, best, best_lag


def _invert_wav(ff, path, timeout):
    tmp = path + ".polarity.wav"
    res = base.run_cmd(
        [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path,
         "-af", "volume=-1", "-ac", "2", "-c:a", "pcm_s16le", tmp],
        timeout=timeout,
    )
    if res["returncode"] != 0 or not base.file_ok(tmp):
        return False
    os.replace(tmp, path)
    return True


def run(job, cfg):
    tgt = base.target_entry(job)
    if not tgt:
        return base.result("skipped", [], {"reason": "no-target"}, reason="没有目标曲（job.target 为空）：只能跑到 train")

    stem = os.path.splitext(os.path.basename(tgt["raw"]))[0]
    inst = base.work(job, "stems", stem, "instrumental.wav")
    vocal = base.work(job, "convert", "vocal.wav")
    if not base.file_ok(vocal):
        return base.result(
            "skipped", [], {"reason": "no-vocal"},
            reason="没有换声产物 work/convert/vocal.wav（convert 未完成）",
        )
    if not base.file_ok(inst):
        return base.result("failed", [], {}, error="目标曲伴奏 stem 缺失：先跑 separate（" + inst + "）")

    mcfg = cfg.get("mix") or {}
    opts = job.get("options") or {}
    shift = base.clamp_int(opts.get("shift", 0), -12, 12)
    ir = (cfg.get("paths") or {}).get("room_ir") or ""
    if ir:
        ir = os.path.expanduser(ir)
        if not os.path.isabs(ir):
            ir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ir)
            ir = os.path.normpath(ir)
    ir_used = base.file_ok(ir)
    if not ir_used:
        base.log("mix: 没找到 IR（%s），本次不带卷积混响" % ir)

    out = base.work(job, "mix", "out.wav")
    premix = base.work(job, "mix", "_tmp", "premix.wav")
    params = {"shift": shift, "mix": mcfg, "ir": os.path.basename(ir) if ir_used else None}
    inputs = [vocal, inst, ir] if ir_used else [vocal, inst]

    if base.recon(job, STAGE, inputs, params, [out]):
        base.log("mix: 产物已存在且输入未变，跳过")
        return base.result("skipped", [base.rel(job, out)], dict(base.prev_metrics(job, STAGE)), reason="产物已存在且输入未变")

    ff = audio.find_ffmpeg(cfg.get("paths"))
    base.ensure_dir(os.path.dirname(premix))
    timeout = float(mcfg.get("timeout_sec", 1800))

    render_cmd, graph, used_ir = commands.mix_render_cmd(ff, cfg, vocal, inst, premix, ir_path=ir, shift=shift)
    base.log("mix: 渲染混音（shift=%s, IR=%s）" % (shift, used_ir))
    res = base.run_cmd(render_cmd, timeout=timeout)
    if res["returncode"] != 0 or not base.file_ok(premix):
        return base.result(
            "failed", [], {"shift": shift},
            error="混音渲染失败（exit=%s）：%s" % (res["returncode"], res["tail"][-1200:]),
        )

    loud_pre = audio.loudness(ff, premix)
    if loud_pre.get("lufs") is None:
        return base.result("failed", [], {}, error="读不到混音响度（ebur128 无 Summary）")
    target = float(mcfg.get("target_lufs", -14.0))
    headroom = float(mcfg.get("headroom_db", 1.0))
    gain = target - float(loud_pre["lufs"]) - headroom

    # 1) 静态等响：alimiter 关掉 level（否则峰值会被自动拉回 0dBFS），
    #    实测响度与目标差 >0.3dB 时再校正一次 gain。
    lufs_after = None
    peak_wav = None
    master_elapsed = 0.0
    for _ in range(4):
        res2 = base.run_cmd(commands.mix_master_cmd(ff, cfg, premix, out, gain), timeout=timeout)
        master_elapsed += res2["elapsed_sec"]
        if res2["returncode"] != 0 or not base.file_ok(out):
            return base.result(
                "failed", [], {"shift": shift},
                error="母带失败（exit=%s）：%s" % (res2["returncode"], res2["tail"][-1200:]),
            )
        loud_after = audio.loudness(ff, out)
        lufs_after = loud_after.get("lufs")
        peak_wav = loud_after.get("peak_db")
        if lufs_after is None or abs(float(lufs_after) - target) <= 0.3:
            break
        gain += target - float(lufs_after)
        base.log("mix: 等响校正 gain=%sdB（实测 %s LUFS）" % (round(gain, 2), lufs_after))

    # 2) 编码后校验：mp3 实测峰值必须 <= -1.0 dBFS；超了整体回退 1dB 重渲染+重编码，最多 2 次
    min_peak = float(mcfg.get("mp3_peak_max_db", -1.0))
    bitrate = str((cfg.get("export") or {}).get("mp3_bitrate", "320k"))
    check_mp3 = base.work(job, "mix", "_tmp", "master_check.mp3")
    check = audio.encode_mp3_checked(
        ff, out, check_mp3, bitrate=bitrate, min_peak_db=min_peak, max_retries=0, timeout=timeout,
    )
    retries = 0
    while check.get("peakDb") is not None and float(check["peakDb"]) > min_peak and retries < 2:
        retries += 1
        gain -= 1.0
        base.log("mix: mp3 峰值 %s dBFS > %s dBFS，回退 1dB 重编（第 %d 次）" % (
            check.get("peakDb"), min_peak, retries))
        res2 = base.run_cmd(commands.mix_master_cmd(ff, cfg, premix, out, gain), timeout=timeout)
        master_elapsed += res2["elapsed_sec"]
        if res2["returncode"] != 0 or not base.file_ok(out):
            return base.result(
                "failed", [], {"shift": shift},
                error="母带回退失败（exit=%s）：%s" % (res2["returncode"], res2["tail"][-1200:]),
            )
        loud_after = audio.loudness(ff, out)
        lufs_after = loud_after.get("lufs")
        peak_wav = loud_after.get("peak_db")
        check = audio.encode_mp3_checked(
            ff, out, check_mp3, bitrate=bitrate, min_peak_db=min_peak, max_retries=0, timeout=timeout,
        )

    final_peak = check.get("peakDb")
    peak_ok = bool(final_peak is not None and float(final_peak) <= min_peak)
    if not peak_ok:
        base.log("mix: 警告：mp3 峰值 %s dBFS 仍未 <= %s dBFS（已回退 %d 次）" % (
            final_peak, min_peak, retries))

    # 3) 极性自检：按 ±N samples 最佳对齐后再判断（0 延迟会因 limiter lookahead 假性为负）
    polarity_fixed = False
    polarity_corr0 = None
    polarity_corr = None
    polarity_lag = None
    if bool(mcfg.get("polarity_check", True)):
        max_lag = int(mcfg.get("polarity_max_lag", 300))
        c0, cbest, lag = _best_corr(ff, out, inst, max_lag=max_lag)
        polarity_corr0 = round(float(c0), 4)
        polarity_corr = round(float(cbest), 4)
        polarity_lag = int(lag)
        base.log("mix: 极性检查 0延迟=%.3f，±%d 最佳对齐=%.3f（lag=%d）" % (c0, max_lag, cbest, lag))
        if cbest < 0:
            if _invert_wav(ff, out, timeout):
                polarity_fixed = True
                polarity_corr = round(float(-cbest), 4)
                check = audio.encode_mp3_checked(
                    ff, out, check_mp3, bitrate=bitrate, min_peak_db=min_peak,
                    max_retries=0, timeout=timeout,
                )
                final_peak = check.get("peakDb")
                peak_ok = bool(final_peak is not None and float(final_peak) <= min_peak)
                base.log("mix: 检测到反相，已整体乘 -1（polarityFixed=true）")
            else:
                base.log("mix: 检测到反相，但反相重编码失败")
        elif c0 < 0:
            base.log("mix: 0 延迟相关为负但最佳对齐为正：判定为 limiter lookahead/延迟，不做反相")

    metrics = {
        "shift": shift,
        "irUsed": used_ir,
        "lufsPre": loud_pre.get("lufs"),
        "lufs": lufs_after,
        "peakDb": peak_wav,
        "finalPeak": final_peak,
        "finalPeakOk": peak_ok,
        "mp3PeakMaxDb": min_peak,
        "encodeRetries": retries,
        "gainDb": round(gain, 2),
        "limiterAttackMs": float(mcfg.get("limiter_attack_ms", 0.1)),
        "polarityFixed": polarity_fixed,
        "polarityCorr0": polarity_corr0,
        "polarityCorr": polarity_corr,
        "polarityLag": polarity_lag,
        "elapsed_sec": round(res["elapsed_sec"] + master_elapsed, 1),
    }
    base.log("mix: 完成 LUFS=%s peak=%s finalPeak=%s dBFS" % (metrics["lufs"], metrics["peakDb"], metrics["finalPeak"]))
    return base.result("done", [base.rel(job, out)], metrics, input_hash=base.sig(job, inputs, params))
