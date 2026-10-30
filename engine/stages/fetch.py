# -*- coding: utf-8 -*-
"""stage 0 fetch（v0.2）

import 模式：只复制/转码用户给的文件夹（源目录只读，不改不删）。
search 模式：B 站搜索 API + yt-dlp 下载音频，过滤后落到 work/raw/。

sources 写法：
    {"folder": "D:/素材目录"}                                  # import
    {"search": "歌手名", "limit": 8, "author": "仅该作者"}      # search
    {"kind": "search", "query": "歌手名"}                      # 兼容插件旧字段

抓取前必须 options.copyrightAck=true（或 CLI --copyright-ack），否则 run.py 直接退 2。
网络异常不中断：搜索超时 20s、重试 2 次；单条下载失败跳过并写进 work/raw/fetch_report.json。
"""

import html
import http.cookiejar
import json
import os
import re
import time
import urllib.parse
import urllib.request

from assets import audio
from stages import base

STAGE = "fetch"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HOME = "https://www.bilibili.com/"
REFERER = "https://search.bilibili.com/"
DEFAULT_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword="


def _exts(cfg):
    exts = (cfg.get("fetch") or {}).get("audio_exts") or [".wav", ".flac", ".mp3"]
    return tuple(str(x).lower() for x in exts)


def _is_audio(path, exts):
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in exts


def _list_audio(path, exts):
    if not path:
        return []
    path = os.path.normpath(os.path.expanduser(path))
    if os.path.isdir(path):
        out = []
        for cur, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files):
                fp = os.path.join(cur, name)
                if _is_audio(fp, exts):
                    out.append(os.path.abspath(fp))
        return out
    if _is_audio(path, exts):
        return [os.path.abspath(path)]
    return []


def _slug(text, fallback="track"):
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", str(text or ""))
    s = s.strip("._-") or fallback
    return s[:48]


def _target_source(job):
    t = job.get("target")
    if isinstance(t, str):
        return t.strip()
    if isinstance(t, dict):
        return str(t.get("file") or t.get("path") or t.get("audio") or t.get("source") or "").strip()
    tm = job.get("target_meta") or {}
    return str(tm.get("source") or tm.get("audio") or "").strip()


def _is_search(sources):
    if not isinstance(sources, dict):
        return False
    if sources.get("folder") or sources.get("dir"):
        return False
    return bool(str(sources.get("search") or sources.get("query") or "").strip())


# ------------------------------------------------------------------ import

def _run_import(job, cfg):
    exts = _exts(cfg)
    sources = job.get("sources") or {}
    ref_sources = []
    for item in job.get("refs") or []:
        if isinstance(item, str) and item.strip():
            ref_sources.append(item.strip())
        elif isinstance(item, dict):
            p = item.get("path") or item.get("source") or item.get("file")
            if p:
                ref_sources.append(str(p))
    folder = sources.get("folder") or sources.get("dir") or sources.get("path") or ""
    if folder and folder not in ref_sources:
        ref_sources.append(str(folder))

    ref_files = []
    for p in ref_sources:
        for f in _list_audio(p, exts):
            if f not in ref_files:
                ref_files.append(f)

    target_src = _target_source(job)
    target_file = ""
    if target_src:
        found = _list_audio(target_src, exts)
        if not found:
            return base.result(
                "failed", [], {},
                error="目标曲不存在或不是支持的音频文件: " + target_src,
            )
        target_file = found[0]
        if target_file in ref_files:
            ref_files.remove(target_file)

    if not ref_files and not target_file:
        return base.result(
            "failed", [], {},
            error="没有可用素材：sources.folder / refs 里没找到音频文件（支持 " + ", ".join(exts) + "）",
        )

    max_songs = int((cfg.get("select") or {}).get("max_songs", 60))
    ref_files = ref_files[:max_songs]
    sr = int((cfg.get("audio") or {}).get("sample_rate", 44100))
    ch = int((cfg.get("audio") or {}).get("channels", 2))

    plan = []
    for i, src in enumerate(ref_files):
        stem = os.path.splitext(os.path.basename(src))[0]
        out = base.work(job, "raw", "ref_%02d_%s.wav" % (i + 1, _slug(stem)))
        plan.append({"role": "ref", "id": "ref_%02d" % (i + 1), "name": stem, "source": src, "out": out})
    target_title = ""
    if target_file:
        tm = job.get("target_meta") or {}
        target_title = str(tm.get("title") or tm.get("name") or os.path.splitext(os.path.basename(target_file))[0])
        out = base.work(job, "raw", "target_%s.wav" % _slug(target_title))
        plan.append({"role": "target", "id": "target", "name": target_title, "source": target_file, "out": out})

    job["refs"] = [
        {
            "id": p["id"], "name": p["name"], "role": "ref", "kind": "ref",
            "path": base.rel(job, p["out"]), "source": p["source"],
        }
        for p in plan if p["role"] == "ref"
    ]
    if target_file:
        t = [p for p in plan if p["role"] == "target"][0]
        job["target_meta"] = {
            "name": target_title, "title": target_title,
            "audio": base.rel(job, t["out"]), "source": target_file,
        }
    else:
        job["target_meta"] = None

    params = {
        "sr": sr, "ch": ch,
        "plan": [{"role": p["role"], "source": p["source"], "out": os.path.basename(p["out"])} for p in plan],
    }
    inputs = [p["source"] for p in plan]
    outputs = [p["out"] for p in plan]

    if base.recon(job, STAGE, inputs, params, outputs):
        base.log("fetch: 产物已存在且输入未变，跳过")
        return base.result(
            "skipped", [base.rel(job, p) for p in outputs],
            {"refs": len(ref_files), "target": bool(target_file), "files": len(plan)},
            reason="产物已存在且输入未变",
        )

    ff = audio.find_ffmpeg(cfg.get("paths"))
    for p in plan:
        base.log("fetch: 转码 %s -> %s" % (p["source"], p["out"]))
        try:
            audio.transcode(ff, p["source"], p["out"], sr=sr, channels=ch)
        except Exception as e:  # noqa: BLE001
            return base.result("failed", [], {"files": len(plan)}, error="转码失败: " + str(e))
        if not base.file_ok(p["out"]):
            return base.result("failed", [], {"files": len(plan)}, error="转码后产物为空: " + p["out"])

    base.log("fetch: 完成，%d 个文件（参考曲 %d，目标曲 %s）" % (len(plan), len(ref_files), target_file or "无"))
    return base.result(
        "done", [base.rel(job, p) for p in outputs],
        {"refs": len(ref_files), "target": bool(target_file), "files": len(plan), "mode": "import"},
        input_hash=base.sig(job, inputs, params),
    )


# ------------------------------------------------------------------ search：网络工具

def _build_opener(timeout):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        req = urllib.request.Request(HOME, headers={"User-Agent": UA})
        with opener.open(req, timeout=timeout):
            pass
    except Exception:  # noqa: BLE001
        pass
    return opener


def _search_videos(keyword, cfg):
    """返回 (results, error)。超时 20s、重试 2 次（共 3 次尝试）。"""
    fcfg = cfg.get("fetch") or {}
    api = str(fcfg.get("search_api") or DEFAULT_SEARCH_API)
    timeout = float(fcfg.get("search_timeout_sec", 20))
    retries = int(fcfg.get("search_retries", 2))
    url = api + urllib.parse.quote(keyword)
    last_err = ""
    for attempt in range(retries + 1):
        try:
            opener = _build_opener(timeout)
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": REFERER,
                "Origin": "https://search.bilibili.com",
            })
            with opener.open(req, timeout=timeout) as resp:
                payload = json.loads((resp.read() or b"{}").decode("utf-8", "replace"))
            if payload.get("code") != 0:
                last_err = "API code=%s message=%s" % (payload.get("code"), payload.get("message"))
            else:
                data = payload.get("data") or {}
                return list(data.get("result") or []), None
        except Exception as exc:  # noqa: BLE001
            last_err = "%s: %s" % (type(exc).__name__, exc)
        base.log("fetch: 搜索失败（第 %d/%d 次）：%s" % (attempt + 1, retries + 1, last_err))
        if attempt < retries:
            time.sleep(1.0 + attempt)
    return [], last_err or "未知网络错误"


def _duration_sec(v):
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return 0.0
    if ":" in s:
        try:
            nums = [float(p) for p in s.split(":") if p != ""]
        except ValueError:
            return 0.0
        sec = 0.0
        for n in nums:
            sec = sec * 60.0 + n
        return sec
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_title(t):
    t = html.unescape(str(t or ""))
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _title_excluded(title, keywords):
    low = title.lower()
    for kw in keywords or []:
        k = str(kw).strip().lower()
        if k and k in low:
            return str(kw)
    return None


def _download_one(ff, cfg, bvid, title, index, raw_dir):
    """下载单条音频；成功返回 (wav_path, None)，失败返回 (None, reason)。"""
    fcfg = cfg.get("fetch") or {}
    ytdlp_py = (cfg.get("paths") or {}).get("ytdlp_python") or ""
    if not ytdlp_py or not os.path.isfile(ytdlp_py):
        return None, "paths.ytdlp_python 不存在: " + str(ytdlp_py)
    tmp_dir = os.path.join(raw_dir, "_dl")
    base.ensure_dir(tmp_dir)
    url = "https://www.bilibili.com/video/" + bvid
    template = os.path.join(tmp_dir, "%(id)s.%(ext)s")
    cmd = [
        ytdlp_py, "-m", "yt_dlp", "-f", "ba", "--no-playlist",
        "--no-progress", "--quiet",
        "--retries", str(int(fcfg.get("ytdlp_retries", 2))),
        "-o", template, url,
    ]
    res = base.run_cmd(cmd, cwd=raw_dir, timeout=float(fcfg.get("download_timeout_sec", 900)))
    if res["returncode"] != 0:
        return None, "yt-dlp 失败（exit=%s）：%s" % (res["returncode"], (res["tail"] or "")[-600:])

    candidates = []
    for name in sorted(os.listdir(tmp_dir)):
        if name.startswith(bvid + ".") and not name.endswith(".part"):
            candidates.append(os.path.join(tmp_dir, name))
    if not candidates:
        return None, "yt-dlp 返回成功但没找到下载文件（%s.*）" % bvid
    src = max(candidates, key=os.path.getmtime)

    sr = int((cfg.get("audio") or {}).get("sample_rate", 44100))
    ch = int((cfg.get("audio") or {}).get("channels", 2))
    dst = os.path.join(raw_dir, "ref_%02d_%s_%s.wav" % (index, bvid, _slug(title or bvid, "track")[:36]))
    try:
        audio.transcode(ff, src, dst, sr=sr, channels=ch)
    except Exception as exc:  # noqa: BLE001
        return None, "转码失败：%s" % exc
    for name in candidates + [src + ".part"]:
        try:
            if os.path.isfile(name):
                os.remove(name)
        except OSError:
            pass
    return dst, None


def _ensure_target(job, cfg, ff):
    """search 模式下如果 job.target 是本地文件，也导入成 work/raw/target_*.wav。"""
    src = _target_source(job)
    if not src:
        return None
    found = _list_audio(src, _exts(cfg))
    if not found:
        base.log("fetch: 目标曲路径不存在或不是音频，跳过目标导入: " + src)
        return None
    src = found[0]
    tm = job.get("target_meta") or {}
    title = str(tm.get("title") or tm.get("name") or os.path.splitext(os.path.basename(src))[0])
    out = base.work(job, "raw", "target_%s.wav" % _slug(title))
    sr = int((cfg.get("audio") or {}).get("sample_rate", 44100))
    ch = int((cfg.get("audio") or {}).get("channels", 2))
    try:
        if not base.file_ok(out) or os.path.getmtime(out) < os.path.getmtime(src):
            audio.transcode(ff, src, out, sr=sr, channels=ch)
    except Exception as exc:  # noqa: BLE001
        base.log("fetch: 目标曲转码失败，忽略：%s" % exc)
        return None
    job["target_meta"] = {"name": title, "title": title, "audio": base.rel(job, out), "source": src}
    return job["target_meta"]


def _set_refs(job, used):
    refs = []
    for i, entry in enumerate(used, 1):
        refs.append({
            "id": "ref_%02d" % i,
            "name": entry.get("title") or entry.get("bvid") or ("ref_%02d" % i),
            "role": "ref",
            "kind": "search",
            "bvid": entry.get("bvid"),
            "path": entry.get("localPath"),
            "source": entry.get("sourceUrl"),
        })
    job["refs"] = refs
    return refs


# ------------------------------------------------------------------ search：主流程

def _run_search(job, cfg, sources):
    opts = job.get("options") or {}
    if not opts.get("copyrightAck"):
        return {
            "status": "needs_decision", "code": "COPYRIGHT_ACK_REQUIRED",
            "artifacts": [], "metrics": {},
            "reason": "抓取模式需用户确认版权风险：加 --copyright-ack（或 options.copyrightAck=true）后重跑。",
        }

    fcfg = cfg.get("fetch") or {}
    keyword = str(sources.get("search") or sources.get("query") or job.get("singer") or "").strip()
    if not keyword:
        return base.result("failed", [], {}, error="抓取模式缺关键词：sources.search / sources.query / singer 都为空")

    limit = base.clamp_int(sources.get("limit") or fcfg.get("default_limit", 8), 1, 50, 8)
    author = sources.get("author", None)
    if author is None:
        author = str(job.get("singer") or "").strip()
    author = str(author).strip()
    min_dur = float(fcfg.get("min_duration_sec", 60))
    max_dur = float(fcfg.get("max_duration_sec", 480))
    exclude_keywords = list(fcfg.get("exclude_title_keywords") or [])

    raw_dir = base.work(job, "raw")
    base.ensure_dir(raw_dir)
    report_path = os.path.join(raw_dir, "fetch_report.json")
    params = {
        "mode": "search", "keyword": keyword, "limit": limit, "author": author,
        "api": str(fcfg.get("search_api") or DEFAULT_SEARCH_API),
        "minDuration": min_dur, "maxDuration": max_dur, "exclude": exclude_keywords,
    }

    if base.recon(job, STAGE, [], params, [report_path]):
        rep = base.read_json(report_path, {}) or {}
        used = [s for s in (rep.get("songs") or []) if s.get("used") and base.file_ok(base.resolve(job, s.get("localPath") or ""))]
        if used:
            _set_refs(job, used)
            base.log("fetch: fetch_report.json 已存在且输入未变，跳过")
            return base.result(
                "skipped",
                [base.rel(job, report_path)] + [s["localPath"] for s in used],
                dict(base.prev_metrics(job, STAGE)),
                reason="产物已存在且输入未变",
            )

    ff = audio.find_ffmpeg(cfg.get("paths"))
    results, search_err = _search_videos(keyword, cfg)
    if search_err:
        base.write_json(report_path, {
            "version": 2, "generatedAt": base.now_iso(), "mode": "search",
            "keyword": keyword, "error": search_err, "songs": [], "params": params,
        })
        return base.result(
            "failed", [base.rel(job, report_path)], {"keyword": keyword},
            error="B站搜索失败（已重试 %s 次）：%s" % (fcfg.get("search_retries", 2), search_err),
        )

    songs = []
    matched_all = []
    for item in results:
        if not isinstance(item, dict):
            continue
        bvid = str(item.get("bvid") or "").strip()
        title = _clean_title(item.get("title"))
        author_name = str(item.get("author") or "").strip()
        dur = _duration_sec(item.get("duration"))
        entry = {
            "bvid": bvid, "title": title, "author": author_name,
            "duration": round(dur, 1), "durationText": str(item.get("duration") or ""),
            "play": item.get("play"), "used": False, "reason": "",
            "localPath": None,
            "sourceUrl": ("https://www.bilibili.com/video/" + bvid) if bvid else "",
        }
        if not bvid:
            entry["reason"] = "缺 bvid"
        elif not (min_dur <= dur <= max_dur):
            entry["reason"] = "时长 %.0fs 不在 %.0f-%.0fs" % (dur, min_dur, max_dur)
        else:
            hit = _title_excluded(title, exclude_keywords)
            if hit:
                entry["reason"] = "标题含排除词 %s" % hit
            elif author and author not in author_name:
                entry["reason"] = "作者不含 %s" % author
            else:
                matched_all.append(entry)
        songs.append(entry)

    matched = matched_all[:limit]
    for entry in matched_all[limit:]:
        entry["reason"] = "超出 limit=%d，本次不下载" % limit

    used = []
    failures = []
    for i, entry in enumerate(matched, 1):
        base.log("fetch: 下载 [%d/%d] %s %s" % (i, len(matched), entry["bvid"], entry["title"][:40]))
        dst, why = _download_one(ff, cfg, entry["bvid"], entry["title"], i, raw_dir)
        if dst:
            entry["used"] = True
            entry["localPath"] = base.rel(job, dst)
            entry["reason"] = ""
            used.append(entry)
        else:
            entry["reason"] = why or "下载失败"
            failures.append({"bvid": entry["bvid"], "title": entry["title"], "reason": entry["reason"]})
            base.log("fetch: 跳过 %s：%s" % (entry["bvid"], entry["reason"]))

    report = {
        "version": 2,
        "generatedAt": base.now_iso(),
        "mode": "search",
        "keyword": keyword,
        "authorFilter": author,
        "limit": limit,
        "searchApi": params["api"],
        "counts": {
            "results": len(results),
            "matched": len(matched_all),
            "downloaded": len(used),
            "failed": len(failures),
        },
        "songs": songs,
        "failures": failures,
        "params": params,
    }
    base.write_json(report_path, report)

    if used:
        _set_refs(job, used)
        target = _ensure_target(job, cfg, ff)
        base.log("fetch: 搜索完成，%d 首可用（候选 %d，下载失败 %d）" % (
            len(used), len(matched_all), len(failures)))
        return base.result(
            "done",
            [base.rel(job, report_path)] + [s["localPath"] for s in used],
            dict(report["counts"], keyword=keyword, mode="search", target=bool(target)),
            input_hash=base.sig(job, [], params),
        )

    if not matched_all:
        return base.result(
            "failed", [base.rel(job, report_path)],
            report["counts"],
            error="搜索到 %d 条，但都不满足条件（时长 %.0f-%.0fs / 排除伴奏 / 作者含 %s）" % (
                len(results), min_dur, max_dur, author or "不限"),
        )
    return base.result(
        "failed", [base.rel(job, report_path)], report["counts"],
        error="候选 %d 首，下载全部失败：%s" % (
            len(matched), "；".join("%s: %s" % (f["bvid"], f["reason"][:80]) for f in failures[:3])),
    )


def run(job, cfg):
    sources = job.get("sources") or {}
    if _is_search(sources):
        return _run_search(job, cfg, sources)
    if str(job.get("mode") or "import").lower() != "import":
        return base.result(
            "failed", [], {},
            error="抓取模式缺 sources.search / query：无法搜索（插件 auto 模式应传 search 关键词）",
        )
    return _run_import(job, cfg)
