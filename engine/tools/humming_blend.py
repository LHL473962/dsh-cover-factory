#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""哼唱段保留原声：按歌词词时间生成 mask，平滑后混合原声 stem 与换声输出。

CLI:
  python engine/tools/humming_blend.py --source Vocals.wav --converted vocal_new.wav \
      --out blended.wav --mode keep --report humming.json [--words-json words.json]
"""
import argparse
import json
import os
import sys

import numpy as np

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)

from assets import audio  # noqa: E402

SR = 44100
PAD_SEC = 0.15
SMOOTH_KEEP = 1.2
SMOOTH_MUTE = 0.25


def _decode(ff, path):
    x = audio.decode(ff, path, SR, 2)
    return np.asarray(x, dtype=np.float32)


def _rms(x):
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2))) if len(x) else 0.0


def _smooth(mask, sec):
    from scipy.signal import fftconvolve
    k = max(1, int(round(sec * SR)))
    h = np.hanning(k).astype(np.float64)
    if h.sum() <= 0:
        return mask
    y = fftconvolve(mask.astype(np.float64), h / h.sum(), mode="same")
    return np.clip(y, 0.0, 1.0).astype(np.float32)


def _load_segments(path):
    data = json.load(open(path, "r", encoding="utf-8-sig"))
    segs = data.get("segments") if isinstance(data, dict) else data
    out = []
    for item in segs or []:
        if not isinstance(item, dict):
            continue
        try:
            a, b = float(item.get("a")), float(item.get("b"))
        except (TypeError, ValueError):
            continue
        typ = str(item.get("type") or "conv").strip().lower()
        if typ not in ("keep", "mute", "conv") or b <= a:
            continue
        out.append((a, b, typ))
    return out


def _load_words(path):
    data = json.load(open(path, "r", encoding="utf-8-sig"))
    words = []
    if isinstance(data, dict):
        data = data.get("words") or data.get("segments") or []
    for item in data or []:
        if isinstance(item, dict):
            st, en = item.get("start"), item.get("end")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            st, en = item[0], item[1]
        else:
            continue
        try:
            words.append((float(st), float(en)))
        except (TypeError, ValueError):
            continue
    return words


MODEL_ROOT = os.path.expanduser("~/.dsh/cover-factory/models/faster-whisper-small")


def _asr_words(source, cache, model_name, model_root=MODEL_ROOT):
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        return None, "faster_whisper 未安装：" + type(exc).__name__
    if cache and os.path.isfile(cache):
        try:
            return _load_words(cache), ""
        except Exception:
            pass
    os.makedirs(model_root, exist_ok=True)
    # 若本地已缓存对应模型目录，直接用本地路径，避免联网
    base_root = os.path.dirname(model_root) if os.path.basename(model_root).startswith("faster-whisper-") else model_root
    local_model_dir = os.path.join(base_root, "faster-whisper-" + str(model_name))
    if not os.path.isfile(os.path.join(local_model_dir, "model.bin")):
        local_model_dir = model_root if os.path.isfile(os.path.join(model_root, "model.bin")) else ""
    errors = []
    for lang in ("ja", None):
        try:
            try:
                model = WhisperModel(local_model_dir or model_name, device="cpu", compute_type="int8",
                                     download_root=model_root)
            except Exception:
                os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                model = WhisperModel(model_name, device="cpu", compute_type="int8",
                                     download_root=model_root)
            segments, _info = model.transcribe(source, language=lang, word_timestamps=True,
                                                vad_filter=False)
            words = []
            for s in segments:
                for w in (getattr(s, "words", None) or []):
                    if w.start is None or w.end is None:
                        continue
                    words.append((float(w.start), float(w.end)))
            if words:
                if cache:
                    try:
                        os.makedirs(os.path.dirname(cache), exist_ok=True)
                        json.dump([[a, b] for a, b in words], open(cache, "w", encoding="utf-8"))
                    except OSError:
                        pass
                return words, ""
            errors.append("lang=%s 无词" % lang)
        except Exception as exc:  # noqa: BLE001
            errors.append("lang=%s %s" % (lang, str(exc)[:100]))
    return None, "faster-whisper 转写失败：" + "；".join(errors)


def _f0_confidence_mask(source, n, ff):
    """F0 置信度 fallback：conf < 0.12 的帧视为无歌词/哼唱，保留原声。"""
    try:
        x = audio.decode(ff, source, 16000, 1).astype(np.float64)
        frame = 800
        hop = 400
        if len(x) < frame:
            return np.zeros(n, dtype=np.float32), 0.0
        times = []
        confs = []
        active = []
        for st in range(0, len(x) - frame + 1, hop):
            seg = x[st:st + frame]
            seg = seg - seg.mean()
            rms = float(np.sqrt(np.mean(seg * seg)))
            conf = 0.0
            is_active = rms > 0.005
            if is_active:
                spec = np.fft.rfft(seg, n=2048)
                ac = np.fft.irfft(spec * np.conj(spec), n=2048)[:frame]
                if ac[0] > 1e-12:
                    ac = ac / ac[0]
                    lo, hi = 20, min(int(16000 / 60.0), frame - 1)
                    if hi > lo:
                        conf = float(np.max(ac[lo:hi]))
            times.append(st / 16000.0)
            confs.append(max(0.0, conf))
            active.append(is_active)
        # 默认换声；只有“有能量但 F0 置信度 < 0.12”的帧才当哼唱保留原声。
        m = np.ones(n, dtype=np.float32)
        for t, c, act in zip(times, confs, active):
            if act and c < 0.12:
                a = int(t * SR)
                b = min(n, int((t + 0.05) * SR))
                if b > a:
                    m[a:b] = 0.0
        return _smooth(m, SMOOTH_KEEP), (float(np.mean(confs)) if confs else 0.0)
    except Exception:
        return np.zeros(n, dtype=np.float32), 0.0


def _f0_fallback_mask(source, n, ff):
    return _f0_confidence_mask(source, n, ff)


def main(argv=None):
    ap = argparse.ArgumentParser(description="humming keep/mute blend")
    ap.add_argument("--source", required=True, help="原始目标曲人声 stem")
    ap.add_argument("--converted", required=True, help="RVC 换声输出")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("auto", "keep", "mute", "off", "manual"), default="auto")
    ap.add_argument("--segments", default="", help="manual 标记 JSON：{segments:[{a,b,type}]}")
    ap.add_argument("--report", default="")
    ap.add_argument("--words-json", default="", help="测试/缓存：[[start,end],...]")
    ap.add_argument("--cache", default="", help="ASR 词时间缓存 JSON")
    ap.add_argument("--whisper-model", default="small")
    ap.add_argument("--model-root", default=MODEL_ROOT)
    ap.add_argument("--ffmpeg", default="")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ff = audio.find_ffmpeg({"ffmpeg": args.ffmpeg} if args.ffmpeg else {})
    src = _decode(ff, args.source)
    cv = _decode(ff, args.converted)
    n = min(len(src), len(cv))
    if n <= 0:
        print("ERROR: empty audio", file=sys.stderr)
        return 2
    src, cv = src[:n], cv[:n]
    report = {"mode": args.mode, "durationSec": round(n / float(SR), 3),
              "fallback": False, "warning": "", "words": 0, "gain": 1.0,
              "keptRatio": 0.0, "hummingKeptRatio": 0.0}
    mask = np.zeros(n, dtype=np.float32)
    effective = args.mode
    report["hummingMode"] = args.mode
    report["asrModel"] = ""
    out = None
    if args.mode == "manual":
        segs = []
        if args.segments and os.path.isfile(args.segments):
            try:
                segs = _load_segments(args.segments)
            except Exception as exc:  # noqa: BLE001
                report["warning"] = "WARN: humming segments 解析失败（%s）；退化为全部换声" % type(exc).__name__
        else:
            report["warning"] = "WARN: hummingSegments 缺失或不存在；退化为全部换声"
        if not segs:
            report["fallback"] = True
            if not report["warning"]:
                report["warning"] = "WARN: hummingSegments 无有效区间；退化为全部换声"
            print(report["warning"], file=sys.stderr)
        keep = np.ones(n, dtype=np.float32)
        mute = np.ones(n, dtype=np.float32)
        for a, b, typ in segs:
            i0 = max(0, int(float(a) * SR))
            i1 = min(n, int(float(b) * SR))
            if i1 <= i0:
                continue
            if typ == "keep":
                keep[i0:i1] = 0.0
            elif typ == "mute":
                mute[i0:i1] = 0.0
            else:
                keep[i0:i1] = 1.0
        keep = _smooth(keep, 0.02)
        mute = _smooth(mute, 0.02)
        out = (cv * keep[:, None] + src * (1.0 - keep)[:, None]) * mute[:, None]
        report["segments"] = len(segs)
        report["keptRatio"] = round(float((1.0 - keep).mean()), 4)
        report["manualFallback"] = bool(report.get("fallback"))
    elif args.mode == "off":
        out = cv.copy()
        report["keptRatio"] = 0.0
    else:
        if args.words_json:
            words = _load_words(args.words_json)
            err = ""
            asr_model = "words-json" if words else ""
        else:
            words, err = _asr_words(args.source, args.cache or "", args.whisper_model, args.model_root)
            asr_model = args.whisper_model if words else ""
        if words is None:
            words = []
            report["fallback"] = True
            report["warning"] = "WARN: " + (err or "ASR 不可用") + "；转 F0 置信度 fallback"
            print(report["warning"], file=sys.stderr)
        report["words"] = len(words)
        for st, en in words:
            a = max(0, int((float(st) - PAD_SEC) * SR))
            b = min(n, int((float(en) + PAD_SEC) * SR))
            if b > a:
                mask[a:b] = 1.0
        mask = _smooth(mask, SMOOTH_MUTE if args.mode == "mute" else SMOOTH_KEEP)
        kept = float((1.0 - mask).mean())
        report["asrModel"] = asr_model
        if args.mode == "auto":
            if not (0.03 <= kept <= 0.35):
                report["warning"] = ("WARN: ASR keptRatio %.1f%% 不在 3%%-35%%，转 F0 置信度 fallback" % (kept * 100))
                print(report["warning"], file=sys.stderr)
                mask, conf = _f0_confidence_mask(args.source, n, ff)
                kept = float((1.0 - mask).mean())
                report["f0Confidence"] = round(float(conf), 4)
                report["asrModel"] = "f0"
                report["fallback"] = True
                if not (0.03 <= kept <= 0.35):
                    effective = "off"
                    report["warning"] += "；F0 仍不合格，降级 humming: off（全换声）"
                    report["autoFallback"] = "off"
                    print(report["warning"], file=sys.stderr)
                    out = cv.copy()
                    report["keptRatio"] = 0.0
                else:
                    report["autoFallback"] = "f0"
        if out is None:
            if args.mode == "mute":
                out = cv * mask[:, None]
                report["keptRatio"] = 0.0
            else:
                voiced = mask > 0.5
                unvoiced = mask < 0.5
                v_rms = _rms(cv[voiced]) if np.any(voiced) else 0.0
                o_rms = _rms(src[unvoiced]) if np.any(unvoiced) else 0.0
                gain = float(np.clip(v_rms / max(o_rms, 1e-9), 0.5, 2.0)) if o_rms > 0 else 1.0
                out = cv * mask[:, None] + src * (gain * (1.0 - mask))[:, None]
                report["gain"] = round(gain, 4)
                report["keptRatio"] = round(float((1.0 - mask).mean()), 4)
    report["hummingMode"] = effective
    report["hummingKeptRatio"] = report["keptRatio"]
    report["peak"] = round(float(np.max(np.abs(out))), 6) if len(out) else 0.0
    report["sourcePeak"] = round(float(np.max(np.abs(src))), 6) if len(src) else 0.0
    report["convertedPeak"] = round(float(np.max(np.abs(cv))), 6) if len(cv) else 0.0

    try:
        import soundfile as sf
    except Exception as exc:  # noqa: BLE001
        print("ERROR: soundfile unavailable: %s" % exc, file=sys.stderr)
        return 2
    base = os.path.dirname(os.path.abspath(args.out))
    if base:
        os.makedirs(base, exist_ok=True)
    sf.write(args.out, np.clip(out, -1.0, 1.0), SR, subtype="PCM_16")
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        json.dump(report, open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
