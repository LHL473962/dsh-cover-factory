# -*- coding: utf-8 -*-
"""stage 7 mix：把换声人声和目标曲伴奏按固化混音链合成。

链与 <DATA_DIR>/out/tuning/mix_unity.sh 一致（见 assets/commands.py mix_filter）：
人声 0dB + highpass 45 + 260Hz -1.2 + 9k +1.6 + 2:1 轻压缩；
IR 1.2s 湿 30% 返回加宽 1.8x；伴奏 0dB + 2.8k 让位 -1 + 加宽 1.15x；
总线 1.3:1 glue -> 静态等响 (-14 LUFS) -> limiter -1.5dB。
"""

import os

from assets import audio, commands
from stages import base

STAGE = "mix"


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
        "elapsed_sec": round(res["elapsed_sec"] + master_elapsed, 1),
    }
    base.log("mix: 完成 LUFS=%s peak=%s finalPeak=%s dBFS" % (metrics["lufs"], metrics["peakDb"], metrics["finalPeak"]))
    return base.result("done", [base.rel(job, out)], metrics, input_hash=base.sig(job, inputs, params))
