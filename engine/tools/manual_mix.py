# -*- coding: utf-8 -*-
"""强制流程：检查源 stem -> 按原比例 -> 统一混音链 -> 校验 -> 输出。

用法:
python engine/tools/manual_mix.py --vocal <换声后人声> --inst <伴奏> \
  --source-vocal <原始人声 stem> --out <输出.mp3> [--preset spica] [--force]
"""
import argparse, hashlib, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from assets import audio, analysis

FF = r"D:/VoiceData/tools/bin/ffmpeg.exe"
IR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "room_ir.wav")

def rms_db(p):
    x = audio.decode(FF, p, sr=22050, channels=1)
    return 20 * np.log10(max(np.sqrt((x * x).mean()), 1e-9))

def check_stem(vocal, inst):
    v = audio.decode(FF, vocal, sr=22050, channels=1)
    i = audio.decode(FF, inst, sr=22050, channels=1)
    n = min(len(v), len(i)); v = v[:n]; i = i[:n]
    corr = float(np.corrcoef(v, i)[0, 1])
    import librosa
    H, P = librosa.effects.hpss(v)
    perc = float((P * P).sum() / ((H * H).sum() + (P * P).sum() + 1e-9))
    f0 = analysis.f0_track(v, 22050); s = analysis.f0_summary(f0)
    voiced = s["voiced_frames"] / max(s["frames"], 1)
    bad = []
    if perc > 0.30: bad.append("打击比例 %.2f > 0.30（人声轨混入伴奏）" % perc)
    if corr > 0.25: bad.append("相关 %.3f > 0.25（串音）" % corr)
    if voiced < 0.12: bad.append("浊音率 %.2f < 0.12（空轨/纯乐器）" % voiced)
    return bad, dict(perc=perc, corr=corr, voiced=voiced)

def gain_for_ratio(vocal, inst, source_vocal):
    # 目标：换声后人声的绝对电平 = 原始人声 stem 的绝对电平（即保持原比例）
    target = rms_db(source_vocal)
    cv = rms_db(vocal)
    ratio = target - rms_db(inst)
    return target - cv, ratio

def chain_default():
    return ("[0:a]aresample=44100,volume=%sdB,highpass=f=45,equalizer=f=260:width_type=q:width=1.4:g=-1.2,"
            "equalizer=f=9000:width_type=q:width=1.0:g=1.6,asplit=2[v1][vs];"
            "[vs][2:a]afir=dry=0:wet=1[w0];[w0]extrastereo=m=1.8,volume=0.30[w];"
            "[v1][w]amix=inputs=2:normalize=0[v];"
            "[1:a]aresample=44100,equalizer=f=2800:width_type=q:width=1.2:g=-1.0,extrastereo=m=1.15[i];"
            "[v][i]amix=inputs=2:duration=longest:normalize=0,"
            "acompressor=threshold=-15dB:ratio=1.3:attack=20:release=250,aformat=channel_layouts=stereo[out]")

def chain_preset(gain_db):
    return (
        "[0:a]aresample=44100,volume=%.2fdB,lowshelf=f=120:g=-2,equalizer=f=800:width_type=q:width=1.0:g=-2,"
        "equalizer=f=3000:width_type=q:width=1.1:g=3,equalizer=f=9000:width_type=q:width=1.0:g=-2,"
        "asoftclip=type=tanh:threshold=0.5,asplit=2[v1][vs];"
        "[vs][2:a]afir=dry=0:wet=1[w0];[w0]volume=0.10[w];[v1]volume=0.90[vd];[vd][w]amix=inputs=2:normalize=0[v];"
        "[1:a]aresample=44100[i];[v][i]amix=inputs=2:duration=longest:normalize=0,"
        "aformat=channel_layouts=stereo[out]"
    ) % gain_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocal", required=True); ap.add_argument("--inst", required=True)
    ap.add_argument("--source-vocal", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--preset", choices=["old", "new", "default", "spica"], default="old")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--auto-gain", action="store_true", help="按人声/伴奏活跃帧中位电平自动算增益（覆盖原比例）")
    ap.add_argument("--target-db", type=float, default=None, help="auto-gain 目标差；old 默认走原比例，new 默认 0（人声=伴奏）")
    ap.add_argument("--vocal-db", type=float, default=0.0, help="在自动/原比例基础上再给一轨加/减增益（模拟推子）")
    ap.add_argument("--no-normalize", action="store_true", help="不做 -14 LUFS 归一化（分轨增益直接生效，只防削波）")
    a = ap.parse_args()

    # 两套标准：old = 旧库（unity 原比例 + 默认链 + -14 LUFS）；new = 新库（等响基准 + 人声 +3 + EQ/饱和 + 不归一化）
    if a.preset in ("new", "spica"):
        if a.target_db is None:
            a.target_db = 0.0
        if a.vocal_db == 0.0:
            a.vocal_db = 3.0
        a.auto_gain = True
        a.no_normalize = True
    elif a.preset in ("old", "default"):
        if a.target_db is None:
            a.target_db = 0.0
        a.auto_gain = False
        a.no_normalize = False

    bad, st = check_stem(a.source_vocal, a.inst)
    print("stem:", st)
    if bad and not a.force:
        print("REFUSED: " + "; ".join(bad)); sys.exit(2)

    if a.auto_gain:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from auto_gain import active_median_db
        v = active_median_db(a.vocal); i = active_median_db(a.inst)
        gain = (i + a.target_db) - v
        ratio = v - i
        print("AUTO vocal=%.2f inst=%.2f ratio=%.2f target=%.2f gain=%.2f dB" % (v, i, ratio, a.target_db, gain))
    else:
        gain, ratio = gain_for_ratio(a.vocal, a.inst, a.source_vocal)
        print("ratio_src=%.2f dB  gain=%.2f dB" % (ratio, gain))
    # 用统一的 placeholder 替换
    gain += a.vocal_db
    if a.preset in ("old", "default"):
        g = chain_default().replace("[0:a]aresample=44100,volume=%sdB", "[0:a]aresample=44100,volume=%.2fdB" % gain)
    else:
        g = chain_preset(gain)
    tmp = tempfile.mktemp(suffix=".wav")
    subprocess.run([FF,"-y","-hide_banner","-loglevel","error","-i",a.vocal,"-i",a.inst,"-i",IR,
                    "-filter_complex",g,"-map","[out]","-ac","2",tmp], check=True)
    probe = subprocess.run([FF,"-hide_banner","-nostats","-i",tmp,"-af","ebur128","-f","null","-"],
                           capture_output=True, text=True, errors="ignore")
    cur = None
    for line in probe.stderr.splitlines():
        if "I:" in line and "LUFS" in line: cur = float(line.split("I:")[1].split("LUFS")[0])
    adj = 0.0 if a.no_normalize else (-14.0 - (cur if cur else -14.0))
    subprocess.run([FF,"-y","-hide_banner","-loglevel","error","-i",tmp,
                    "-af","volume=%.2fdB,alimiter=limit=-2dB:level=false" % adj,
                    "-codec:a","libmp3lame","-b:a","320k",a.out], check=True)
    os.remove(tmp)
    if a.auto_gain:
        from auto_gain import active_median_db as _amd
        ratio_mix = (_amd(a.vocal) + gain) - _amd(a.inst)
    else:
        ratio_mix = (rms_db(a.vocal) + gain) - rms_db(a.inst)
    delta = (ratio_mix - (a.target_db if a.auto_gain else ratio))
    peak = subprocess.run([FF,"-hide_banner","-nostats","-i",a.out,"-af","astats=metadata=1:reset=0","-f","null","-"],
                          capture_output=True, text=True, errors="ignore").stderr
    pk = [l for l in peak.splitlines() if "Peak level dB" in l]
    print("ratio_mix=%.2f delta=%.2f dB  %s" % (ratio_mix, delta, pk[0].split("]")[-1].strip() if pk else ""))
    print("md5", hashlib.md5(open(a.out,"rb").read()).hexdigest(), a.out)
    if (not a.no_normalize) and abs(delta) > 0.5:
        print("FAIL: ratio delta > 0.5"); sys.exit(1)

if __name__ == "__main__": main()
