# -*- coding: utf-8 -*-
"""stage 0 fetch（v0.2）

import 模式：只复制/转码用户给的文件夹（源目录只读，不改不删）。
search 模式：B 站搜索 API + yt-dlp 下载音频，过滤后落到 work/raw/。

sources 写法：
    {"folder": "D:/素材目录"}                                  # import
    {"search": "歌手名", "limit": 8, "author": "仅该作者"}      # search
    {"kind": "search", "query": "歌手名"}                      # 兼容插件旧字段
    {"local_clients": true, "clients": ["netease"], "keyword": "suis"}  # 本机客户端已下载

抓取前必须 options.copyrightAck=true（或 CLI --copyright-ack），否则 run.py 直接退 2。
网络异常不中断：搜索超时 20s、重试 2 次；单条下载失败跳过并写进 work/raw/fetch_report.json。
"""

import html
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
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


def _looks_like_path(text):
    t = str(text or "").strip()
    if not t:
        return False
    if "/" in t or "\\" in t:
        return True
    return bool(re.match(r"^[A-Za-z]:", t))


def _ref_specs(job):
    """把 job.refs 归一成 [{input, kind}]；kind=path|name。"""
    specs = []
    for item in job.get("refs") or []:
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            specs.append({"input": text, "kind": "path" if _looks_like_path(text) else "name"})
        elif isinstance(item, dict):
            text = str(item.get("input") or item.get("path") or item.get("source")
                       or item.get("name") or "").strip()
            if not text:
                continue
            kind = item.get("kind")
            if kind not in ("path", "name"):
                kind = "path" if _looks_like_path(text) else "name"
            specs.append({"input": text, "kind": kind})
    return specs


def _pool_names(path):
    base_name = os.path.basename(path)
    stem = os.path.splitext(base_name)[0]
    names = [stem, base_name]
    out = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def _norm_path(path):
    return os.path.normcase(os.path.abspath(str(path or "")))


def _match_local(spec, pool):
    if spec["kind"] == "path":
        target = _norm_path(spec["input"])
        for item in pool:
            if _norm_path(item["path"]) == target:
                return item["path"]
        return ""
    query = spec["input"].strip().lower()
    if not query:
        return ""
    best, best_len = "", -1
    for item in pool:
        for name in item["names"]:
            low = str(name).lower()
            if query in low and len(name) > best_len:
                best, best_len = item["path"], len(name)
    return best


def _attach_raw(job, resolved, source_to_out):
    for entry in resolved:
        if not entry.get("matched"):
            continue
        out = source_to_out.get(_norm_path(entry.get("matchedPath")))
        if out:
            entry["rawPath"] = base.rel(job, out)
    return resolved


def _search_ref_online(job, cfg, name):
    """用现有 B站搜索 + yt-dlp 下载函数给未匹配的歌名找一条音频，存到 work/raw/refs/。"""
    base.log("fetch: 联网搜索好球曲：%s" % name)
    try:
        ff = audio.find_ffmpeg(cfg.get("paths"))
    except Exception as exc:  # noqa: BLE001
        base.log("fetch: 联网搜索好球曲失败（ffmpeg 不可用）：%s" % exc)
        return None
    results, search_err = _search_videos(name, cfg)
    if search_err:
        base.log("fetch: 好球曲「%s」搜索失败：%s" % (name, search_err))
        return None
    fcfg = cfg.get("fetch") or {}
    min_dur = float(fcfg.get("min_duration_sec", 60))
    max_dur = float(fcfg.get("max_duration_sec", 480))
    exclude_keywords = list(fcfg.get("exclude_title_keywords") or [])
    query = name.strip().lower()
    raw_dir = base.work(job, "raw")
    refs_dir = os.path.join(raw_dir, "refs")
    base.ensure_dir(refs_dir)
    for item in results:
        if not isinstance(item, dict):
            continue
        bvid = str(item.get("bvid") or "").strip()
        title = _clean_title(item.get("title"))
        dur = _duration_sec(item.get("duration"))
        if not bvid or not (min_dur <= dur <= max_dur):
            continue
        if _title_excluded(title, exclude_keywords):
            continue
        if query and query not in title.lower():
            continue
        src, why = _download_one(ff, cfg, bvid, title, 1, raw_dir, out_dir=refs_dir, prefix="refsearch")
        if not src:
            base.log("fetch: 好球曲「%s」下载失败：%s" % (name, why))
            return None
        return {
            "path": os.path.abspath(src),
            "bvid": bvid,
            "title": title,
            "sourceUrl": "https://www.bilibili.com/video/" + bvid,
        }
    return None


def _resolve_refs(job, cfg, specs, pool, allow_search, search_wanted):
    """本地模糊匹配优先；未命中且允许联网时搜/下一条；结果写 normalized entries。"""
    resolved, warnings = [], []
    prev = {}
    for item in job.get("refs") or []:
        if isinstance(item, dict) and item.get("input"):
            prev[str(item["input"])] = item

    for spec in specs:
        text = spec["input"]
        entry = {"input": text, "kind": spec["kind"], "matched": False, "matchedPath": None}
        local = _match_local(spec, pool)
        if local:
            entry["matched"] = "local"
            entry["matchedPath"] = local
            resolved.append(entry)
            continue
        if spec["kind"] == "name":
            old = prev.get(text)
            if old and old.get("matched") == "search" and base.file_ok(base.resolve(job, old.get("matchedPath") or "")):
                entry = dict(old)
                resolved.append(entry)
                continue
            if allow_search:
                hit = _search_ref_online(job, cfg, text)
                if hit:
                    entry.update({
                        "matched": "search",
                        "matchedPath": hit["path"],
                        "bvid": hit["bvid"],
                        "title": hit["title"],
                        "sourceUrl": hit["sourceUrl"],
                    })
                    resolved.append(entry)
                    continue
                warnings.append("好球曲「%s」联网没搜到合适的音频，已忽略" % text)
            elif search_wanted:
                warnings.append("好球曲「%s」需要联网搜索，请勾选版权确认或手动放素材" % text)
            else:
                warnings.append("好球曲「%s」没在素材里找到，已忽略" % text)
        else:
            warnings.append("好球曲「%s」不是有效音频文件，已忽略" % text)
        resolved.append(entry)
    for warning in warnings:
        base.log("fetch: WARNING " + warning)
    return resolved, warnings


def _songs_from_plan(job, plan):
    songs = [
        {
            "id": p["id"], "name": p["name"], "role": "ref", "kind": "ref",
            "path": base.rel(job, p["out"]), "source": p["source"],
        }
        for p in plan if p["role"] == "ref"
    ]
    job["songs"] = songs
    return songs


# ------------------------------------------------------------------ 本机音乐客户端

CLIENT_LABELS = {"netease": "netease-client", "qqmusic": "qqmusic-client",
                 "kugou": "kugou-client", "kuwo": "kuwo-client"}
CLIENT_FORMATS = (".mp3", ".flac", ".wav", ".m4a")
ENCRYPTED_SUFFIXES = (".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8",
                      ".qmcflac", ".qmcogg", ".kgm", ".kgma", ".kgg",
                      ".mflac", ".mgg", ".kwm")
_NCMDUMP_CACHE = {"py": None, "exe": None}


def _is_local_clients(sources):
    return isinstance(sources, dict) and bool(sources.get("local_clients"))


def _windows_drives():
    return [d for d in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(d + ":/")]


def _client_roots(cfg, clients):
    home = os.path.expanduser("~")
    drives = _windows_drives()
    roots = []

    def add(path, client):
        p = os.path.abspath(path)
        if os.path.isdir(p):
            roots.append((p, client))

    for client in clients:
        if client == "netease":
            add(os.path.join(home, "Music", "CloudMusic"), client)
            for d in drives:
                add(os.path.join(d + ":/", "CloudMusic"), client)
                add(os.path.join(d + ":/", "CloudMusic", "VipSongsDownload"), client)
        elif client == "qqmusic":
            add(os.path.join(home, "Music", "QQMusic"), client)
            for d in drives:
                add(os.path.join(d + ":/", "QQMusic"), client)
        elif client == "kugou":
            add(os.path.join(home, "Music", "KuGou"), client)
            for d in drives:
                add(os.path.join(d + ":/", "KuGou"), client)
        elif client == "kuwo":
            add(os.path.join(home, "Music", "Kuwo"), client)
    # 兼容其它音乐软件：允许配置额外目录 [{"path": "...", "client": "other"}]
    for extra in (cfg.get("fetch") or {}).get("local_client_dirs") or []:
        if isinstance(extra, str):
            add(extra, "custom")
        elif isinstance(extra, dict) and extra.get("path"):
            add(extra["path"], str(extra.get("client") or "custom"))
    out = []
    seen = set()
    for path, client in roots:
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        out.append((path, client))
    return out


def _walk_limited(root, max_depth=3, max_files=2000):
    root = os.path.abspath(root)
    base_depth = root.rstrip("\\/").count(os.sep)
    out = []
    for cur, dirs, files in os.walk(root):
        depth = cur.rstrip("\\/").count(os.sep) - base_depth
        if depth >= max_depth:
            dirs[:] = []
        for name in sorted(files):
            if len(out) >= max_files:
                return out, True
            out.append(os.path.join(cur, name))
    return out, False


def _client_format(path):
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]
    if ext == ".ncm":
        return "ncm"
    if ext in CLIENT_FORMATS:
        return ext[1:]
    if name.endswith(ENCRYPTED_SUFFIXES) or ext in ENCRYPTED_SUFFIXES:
        if ext in (".kgm", ".kgma", ".kgg"):
            return "kgm"
        if ext.startswith(".qmc"):
            return "qmc"
        return ext[1:]
    return ""


def _candidate_pythons(cfg, key):
    paths = cfg.get("paths") or {}
    raw = [paths.get(key), paths.get("python")]
    raw.extend(paths.get(key + "_fallbacks") or [])
    out = []
    for cand in raw:
        if not cand:
            continue
        p = shutil.which(str(cand)) or (str(cand) if os.path.isfile(str(cand)) else "")
        if p and p not in out:
            out.append(p)
    return out


def _resolve_ncmdump_python(cfg):
    if _NCMDUMP_CACHE.get("py") is not None:
        return _NCMDUMP_CACHE["py"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for py in _candidate_pythons(cfg, "ncmdump_python"):
        try:
            proc = subprocess.run([py, "-c", "import ncmdump"], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=60, creationflags=flags)
            if proc.returncode == 0:
                _NCMDUMP_CACHE["py"] = py
                return py
        except Exception:  # noqa: BLE001
            continue
    _NCMDUMP_CACHE["py"] = ""
    return ""


def _resolve_ncmdump_exe(cfg):
    if _NCMDUMP_CACHE.get("exe") is not None:
        return _NCMDUMP_CACHE["exe"]
    for py in _candidate_pythons(cfg, "ncmdump_python"):
        exe = os.path.join(os.path.dirname(py), "Scripts", "ncmdump.exe")
        if os.path.isfile(exe):
            _NCMDUMP_CACHE["exe"] = exe
            return exe
    exe = shutil.which("ncmdump") or ""
    _NCMDUMP_CACHE["exe"] = exe
    return exe


def _decrypt_ncm(job, cfg, src):
    fcfg = cfg.get("fetch") or {}
    dec_dir = os.path.join(base.work(job, "raw"), "_ncm")
    base.ensure_dir(dec_dir)
    stem = _slug(os.path.splitext(os.path.basename(src))[0])
    dst = os.path.join(dec_dir, stem + ".decoded")
    try:
        if base.file_ok(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            return dst, ""
    except OSError:
        pass
    timeout = float(fcfg.get("ncm_timeout_sec", 300))
    py = _resolve_ncmdump_python(cfg)
    if py:
        script = "import sys;from ncmdump import dump;dump(sys.argv[1],sys.argv[2])"
        res = base.run_cmd([py, "-c", script, src, dst], timeout=timeout)
        if res["returncode"] == 0 and base.file_ok(dst):
            return dst, ""
        base.log("fetch: ncmdump python 解密失败，尝试 ncmdump.exe：%s" % (res.get("tail") or "")[-300:])
    exe = _resolve_ncmdump_exe(cfg)
    if exe:
        before = set(os.listdir(dec_dir))
        res = base.run_cmd([exe, "-o", dec_dir, src], timeout=timeout)
        if res["returncode"] == 0:
            for name in sorted(os.listdir(dec_dir)):
                p = os.path.join(dec_dir, name)
                if name not in before and os.path.isfile(p) and not name.endswith((".ncm", ".decoded")):
                    return p, ""
            for name in sorted(os.listdir(dec_dir)):
                if name.lower().startswith(stem.lower()) and not name.endswith((".ncm", ".decoded")):
                    return os.path.join(dec_dir, name), ""
        return "", "ncmdump 解密失败：%s" % (res.get("tail") or "")[-300:]
    return "", "找不到可用的 ncmdump（paths.ncmdump_python / fallbacks / ncmdump.exe）"


def _local_client_song(ff, cfg, job, entry, index, raw_dir):
    fmt = entry["format"]
    src = entry["file"]
    work_src = src
    if fmt == "ncm":
        work_src, why = _decrypt_ncm(job, cfg, src)
        if not work_src:
            return None, why
    out = os.path.join(raw_dir, "ref_%02d_%s.wav" % (
        index, _slug(os.path.splitext(os.path.basename(src))[0])[:40]))
    sr = int((cfg.get("audio") or {}).get("sample_rate", 44100))
    ch = int((cfg.get("audio") or {}).get("channels", 2))
    audio.transcode(ff, work_src, out, sr=sr, channels=ch)
    return out, ""


def _run_local_clients(job, cfg, sources):
    from assets import audio

    fcfg = cfg.get("fetch") or {}
    clients = sources.get("clients") or ["netease", "qqmusic", "kugou", "kuwo"]
    if isinstance(clients, str):
        clients = [clients]
    clients = [str(c).lower() for c in clients if str(c).lower() in CLIENT_LABELS]
    if not clients:
        clients = ["netease", "qqmusic", "kugou", "kuwo"]
    keyword = str(sources.get("keyword") or job.get("singer") or "").strip()
    try:
        days = float(sources.get("days") or 0)
    except (TypeError, ValueError):
        days = 0.0
    limit = base.clamp_int(sources.get("limit") or 20, 1, 500, 20)
    max_size = int(fcfg.get("local_client_max_size_mb", 200)) * 1024 * 1024
    max_depth = int(fcfg.get("local_client_max_depth", 3))
    max_files = int(fcfg.get("local_client_max_files", 2000))

    roots = _client_roots(cfg, clients)
    if not roots:
        return base.result(
            "failed", [], {},
            error="没有找到常见音乐客户端下载目录（网易云/QQ音乐/酷狗/酷我）；也可以用 fetch.local_client_dirs 指定",
        )

    raw_dir = base.work(job, "raw")
    base.ensure_dir(raw_dir)
    report_path = os.path.join(raw_dir, "local_client_report.json")
    entries = []
    truncated_roots = []
    seen = set()
    for root, client in roots:
        files, truncated = _walk_limited(root, max_depth=max_depth, max_files=max_files)
        if truncated:
            truncated_roots.append(root)
        for path in files:
            key = os.path.normcase(os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            fmt = _client_format(path)
            if not fmt:
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            item = {
                "file": path,
                "client": CLIENT_LABELS.get(client, client),
                "format": fmt,
                "used": False,
                "reason": "",
                "localPath": None,
            }
            if st.st_size > max_size:
                item["reason"] = "文件 >%dMB，跳过" % int(max_size / 1024 / 1024)
            elif days > 0 and st.st_mtime < time.time() - days * 86400:
                item["reason"] = "超过 %g 天，跳过" % days
            elif keyword and keyword.lower() not in os.path.basename(path).lower():
                item["reason"] = "文件名不含关键词「%s」" % keyword
            entries.append(item)

    usable = [e for e in entries if not e["reason"] and e["format"] in ("ncm", "mp3", "flac", "wav", "m4a")]
    encrypted = [e for e in entries if e["format"] in ("qmc", "kgm", "mflac", "mgg", "kwm")]

    specs = _ref_specs(job)
    pool = [{"path": e["file"], "names": _pool_names(e["file"])} for e in usable]
    resolved, warnings = _resolve_refs(job, cfg, specs, pool, False, False)
    matched = {_norm_path(e["matchedPath"]) for e in resolved if e.get("matched")}

    chosen = []
    for e in usable:
        if _norm_path(e["file"]) in matched:
            chosen.append(e)
    if keyword:
        for e in usable:
            if e not in chosen:
                chosen.append(e)
        if len(chosen) > 200:
            chosen = chosen[:200]
    else:
        for e in usable:
            if len(chosen) >= limit:
                break
            if e not in chosen:
                chosen.append(e)
        for e in usable:
            if e not in chosen and not e["reason"]:
                e["reason"] = "超出 limit=%d" % limit

    if encrypted:
        names = [os.path.basename(e["file"]) for e in encrypted]
        message = ("检测到加密格式 %d 个（%s）：需专用解密工具，建议在客户端里另存/手动转换；已跳过"
                   % (len(names), "、".join(names[:5])))
        warnings.append(message)
        for e in encrypted:
            e["reason"] = "加密格式需专用解密工具，建议在客户端里另存/手动转换"
        for w in warnings:
            base.log("fetch: WARNING " + w)

    # 先写元数据（即使 recon 跳过，job 里也有来源）
    source_to_out = {}
    for i, e in enumerate(chosen, 1):
        source_to_out[_norm_path(e["file"])] = os.path.join(
            raw_dir, "ref_%02d_%s.wav" % (i, _slug(os.path.splitext(os.path.basename(e["file"]))[0])[:40]))
    _attach_raw(job, resolved, source_to_out)
    job["refs"] = resolved
    job["warnings"] = warnings
    job["local_client_report"] = base.rel(job, report_path)

    params = {
        "mode": "local_clients", "clients": clients, "keyword": keyword,
        "days": days, "limit": limit, "refs": specs,
        "roots": [os.path.normcase(p) for p, _ in roots],
    }
    inputs = [e["file"] for e in chosen]
    outputs = [source_to_out[_norm_path(e["file"])] for e in chosen]

    if base.recon(job, STAGE, inputs, params, [report_path] + outputs):
        base.log("fetch: 本机客户端产物已存在且输入未变，跳过")
        return base.result(
            "skipped",
            [base.rel(job, report_path)] + [base.rel(job, p) for p in outputs],
            dict(base.prev_metrics(job, STAGE)),
            reason="产物已存在且输入未变",
        )

    ff = audio.find_ffmpeg(cfg.get("paths"))
    songs = []
    song_by_source = {}
    succeeded = []
    for i, e in enumerate(chosen, 1):
        try:
            out, why = _local_client_song(ff, cfg, job, e, i, raw_dir)
        except Exception as exc:  # noqa: BLE001
            out, why = None, "处理失败：%s" % exc
        if not out:
            e["reason"] = why or "处理失败"
            base.log("fetch: 跳过 %s：%s" % (os.path.basename(e["file"]), e["reason"]))
            continue
        e["used"] = True
        e["localPath"] = base.rel(job, out)
        song = {
            "id": "ref_%02d" % i,
            "name": os.path.splitext(os.path.basename(e["file"]))[0],
            "role": "ref",
            "kind": "local_client",
            "path": e["localPath"],
            "source": e["client"],
            "sourcePath": e["file"],
            "client": e["client"],
            "format": e["format"],
        }
        songs.append(song)
        song_by_source[_norm_path(e["file"])] = out
        succeeded.append(out)

    _attach_raw(job, resolved, song_by_source)
    job["refs"] = resolved
    job["songs"] = songs
    target = _ensure_target(job, cfg, ff)

    report = {
        "version": 1,
        "generatedAt": base.now_iso(),
        "mode": "local_clients",
        "clients": clients,
        "keyword": keyword,
        "days": days,
        "limit": limit,
        "roots": [{"path": p, "client": CLIENT_LABELS.get(c, c)} for p, c in roots],
        "truncatedRoots": truncated_roots,
        "maxDepth": max_depth,
        "maxFilesPerRoot": max_files,
        "maxSizeMb": int(max_size / 1024 / 1024),
        "counts": {
            "scanned": len(entries),
            "usable": len(usable),
            "used": len(songs),
            "encrypted": len(encrypted),
        },
        "files": entries,
        "params": params,
    }
    base.write_json(report_path, report)

    if songs:
        base.log("fetch: 本机客户端完成，采用 %d 个文件（扫描 %d，加密跳过 %d，目标曲 %s）" % (
            len(songs), len(entries), len(encrypted), "有" if target else "无"))
        return base.result(
            "done",
            [base.rel(job, report_path)] + [base.rel(job, p) for p in succeeded],
            dict(report["counts"], mode="local_clients", target=bool(target),
                 keyword=keyword, encrypted=len(encrypted)),
            input_hash=base.sig(job, inputs, params),
        )

    reasons = "；".join("%s: %s" % (os.path.basename(e["file"]), e["reason"] or "未采用")
                       for e in entries[:5])
    return base.result(
        "failed", [base.rel(job, report_path)], report["counts"],
        error="本机客户端里没有可用的音频（扫描 %d 个格式文件）：%s" % (len(entries), reasons),
    )


# ------------------------------------------------------------------ import

def _run_import(job, cfg):
    exts = _exts(cfg)
    sources = job.get("sources") or {}
    opts = job.get("options") or {}
    specs = _ref_specs(job)
    search_wanted = (str(job.get("mode") or "import").lower() == "auto"
                     or bool(opts.get("refSearch")))
    allow_search = bool(opts.get("copyrightAck")) and search_wanted

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

    folder = sources.get("folder") or sources.get("dir") or sources.get("path") or ""
    folder_files = []
    if folder:
        for f in _list_audio(folder, exts):
            if os.path.normcase(os.path.abspath(f)) != os.path.normcase(os.path.abspath(target_file or "")):
                folder_files.append(f)

    path_refs = []
    for spec in specs:
        if spec["kind"] != "path":
            continue
        for f in _list_audio(spec["input"], exts):
            if os.path.normcase(os.path.abspath(f)) != os.path.normcase(os.path.abspath(target_file or "")):
                path_refs.append(f)

    pool = []
    seen = set()
    for f in folder_files + path_refs:
        key = os.path.normcase(os.path.abspath(f))
        if key in seen:
            continue
        seen.add(key)
        pool.append({"path": os.path.abspath(f), "names": _pool_names(f)})

    resolved, warnings = _resolve_refs(job, cfg, specs, pool, allow_search, search_wanted)
    job["warnings"] = warnings

    ref_sources = [item["path"] for item in pool]
    for entry in resolved:
        if entry.get("matched") == "search":
            p = entry.get("matchedPath") or ""
            key = os.path.normcase(os.path.abspath(p)) if p else ""
            if p and key not in seen:
                seen.add(key)
                ref_sources.append(p)

    if not ref_sources and not target_file:
        return base.result(
            "failed", [], {},
            error="没有可用素材：sources.folder / refs 里没找到音频文件（支持 " + ", ".join(exts) + "）",
        )

    max_songs = int((cfg.get("select") or {}).get("max_songs", 60))
    ref_sources = ref_sources[:max_songs]
    sr = int((cfg.get("audio") or {}).get("sample_rate", 44100))
    ch = int((cfg.get("audio") or {}).get("channels", 2))

    plan = []
    for i, src in enumerate(ref_sources):
        stem = os.path.splitext(os.path.basename(src))[0]
        out = base.work(job, "raw", "ref_%02d_%s.wav" % (i + 1, _slug(stem)))
        plan.append({"role": "ref", "id": "ref_%02d" % (i + 1), "name": stem, "source": src, "out": out})
    target_title = ""
    if target_file:
        tm = job.get("target_meta") or {}
        target_title = str(tm.get("title") or tm.get("name") or os.path.splitext(os.path.basename(target_file))[0])
        out = base.work(job, "raw", "target_%s.wav" % _slug(target_title))
        plan.append({"role": "target", "id": "target", "name": target_title, "source": target_file, "out": out})

    source_to_out = {}
    for p in plan:
        if p["role"] == "ref":
            source_to_out[_norm_path(p["source"])] = p["out"]
    _attach_raw(job, resolved, source_to_out)
    job["refs"] = resolved
    _songs_from_plan(job, plan)

    if target_file:
        t = [p for p in plan if p["role"] == "target"][0]
        job["target_meta"] = {
            "name": target_title, "title": target_title,
            "audio": base.rel(job, t["out"]), "source": target_file,
        }
    else:
        job["target_meta"] = None

    ref_count = len([p for p in plan if p["role"] == "ref"])
    params = {
        "sr": sr, "ch": ch,
        "refs": specs,
        "plan": [{"role": p["role"], "source": p["source"], "out": os.path.basename(p["out"])} for p in plan],
    }
    inputs = [p["source"] for p in plan]
    outputs = [p["out"] for p in plan]

    if base.recon(job, STAGE, inputs, params, outputs):
        base.log("fetch: 产物已存在且输入未变，跳过")
        return base.result(
            "skipped", [base.rel(job, p) for p in outputs],
            {"refs": ref_count, "target": bool(target_file), "files": len(plan)},
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

    base.log("fetch: 完成，%d 个文件（参考曲 %d，目标曲 %s）" % (len(plan), ref_count, target_file or "无"))
    return base.result(
        "done", [base.rel(job, p) for p in outputs],
        {"refs": ref_count, "target": bool(target_file), "files": len(plan), "mode": "import"},
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


_YTDLP_CACHE = {}


def _resolve_ytdlp_python(cfg):
    """在 paths.ytdlp_python / fallbacks / paths.python 里找第一个能跑 -m yt_dlp 的解释器。"""
    if _YTDLP_CACHE.get("py"):
        return _YTDLP_CACHE["py"]
    paths = cfg.get("paths") or {}
    cands = []
    for key in ("ytdlp_python", "python"):
        if paths.get(key):
            cands.append(str(paths[key]))
    cands.extend(str(x) for x in (paths.get("ytdlp_python_fallbacks") or []))
    cands.append(sys.executable)
    seen = set()
    found = ""
    for cand in cands:
        p = shutil.which(cand) or (cand if os.path.isfile(cand) else "")
        if not p or p in seen:
            continue
        seen.add(p)
        try:
            proc = subprocess.run(
                [p, "-m", "yt_dlp", "--version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0:
                found = p
                break
        except Exception:  # noqa: BLE001
            continue
    _YTDLP_CACHE["py"] = found
    return found


def _download_one(ff, cfg, bvid, title, index, raw_dir, out_dir=None, prefix="ref"):
    """下载单条音频；成功返回 (wav_path, None)，失败返回 (None, reason)。"""
    fcfg = cfg.get("fetch") or {}
    ytdlp_py = _resolve_ytdlp_python(cfg)
    if not ytdlp_py:
        return None, "找不到可用的 yt-dlp（paths.ytdlp_python / fallbacks / 先 pip install yt-dlp）"
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
    target_dir = out_dir or raw_dir
    base.ensure_dir(target_dir)
    dst = os.path.join(target_dir, "%s_%02d_%s_%s.wav" % (
        prefix, index, bvid, _slug(title or bvid, "track")[:36]))
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


def _set_songs(job, used):
    songs = []
    for i, entry in enumerate(used, 1):
        songs.append({
            "id": "ref_%02d" % i,
            "name": entry.get("title") or entry.get("bvid") or ("ref_%02d" % i),
            "role": "ref",
            "kind": "search",
            "bvid": entry.get("bvid"),
            "path": entry.get("localPath"),
            "source": entry.get("sourceUrl"),
        })
    job["songs"] = songs
    return songs


def _materialize_search_refs(job, cfg, resolved, songs):
    """把联网搜到的好球曲（work/raw/refs/）转成正式素材，追加进 songs。"""
    refs = [r for r in resolved if r.get("matched") == "search" and r.get("matchedPath")]
    if not refs:
        return songs
    ff = audio.find_ffmpeg(cfg.get("paths"))
    sr = int((cfg.get("audio") or {}).get("sample_rate", 44100))
    ch = int((cfg.get("audio") or {}).get("channels", 2))
    for entry in refs:
        src = base.resolve(job, entry.get("matchedPath") or "")
        if not base.file_ok(src):
            entry["matched"] = False
            continue
        idx = len(songs) + 1
        out = base.work(job, "raw", "ref_%02d_%s.wav" % (
            idx, _slug(entry.get("title") or entry.get("input"))[:36]))
        try:
            audio.transcode(ff, src, out, sr=sr, channels=ch)
        except Exception as exc:  # noqa: BLE001
            base.log("fetch: 好球曲转码失败，忽略：%s" % exc)
            entry["matched"] = False
            continue
        entry["rawPath"] = base.rel(job, out)
        songs.append({
            "id": "ref_%02d" % idx,
            "name": entry.get("title") or entry.get("input"),
            "role": "ref",
            "kind": "ref",
            "path": base.rel(job, out),
            "source": entry.get("sourceUrl") or src,
            "bvid": entry.get("bvid"),
        })
    return songs


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
    specs = _ref_specs(job)
    allow_search = bool(opts.get("copyrightAck"))
    search_wanted = True
    params = {
        "mode": "search", "keyword": keyword, "limit": limit, "author": author,
        "api": str(fcfg.get("search_api") or DEFAULT_SEARCH_API),
        "minDuration": min_dur, "maxDuration": max_dur, "exclude": exclude_keywords,
        "refs": specs,
    }

    if base.recon(job, STAGE, [], params, [report_path]):
        rep = base.read_json(report_path, {}) or {}
        used = [s for s in (rep.get("songs") or []) if s.get("used") and base.file_ok(base.resolve(job, s.get("localPath") or ""))]
        if used:
            songs = _set_songs(job, used)
            for extra in rep.get("ref_downloads") or []:
                if extra.get("songPath") and base.file_ok(base.resolve(job, extra["songPath"])):
                    songs.append({
                        "id": "ref_%02d" % (len(songs) + 1),
                        "name": extra.get("title") or extra.get("input") or "ref",
                        "role": "ref", "kind": "ref",
                        "path": extra.get("songPath"),
                        "source": extra.get("sourceUrl") or "",
                        "bvid": extra.get("bvid"),
                    })
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
        songs = _set_songs(job, used)
        pool = []
        for entry in used:
            p = base.resolve(job, entry.get("localPath") or "")
            names = [entry.get("title") or "", os.path.basename(p), os.path.splitext(os.path.basename(p))[0]]
            pool.append({"path": os.path.abspath(p), "names": [n for n in names if n]})
        resolved, ref_warnings = _resolve_refs(job, cfg, specs, pool, allow_search, search_wanted)
        songs = _materialize_search_refs(job, cfg, resolved, songs)
        job["refs"] = resolved
        job["warnings"] = ref_warnings
        report["ref_downloads"] = [
            {
                "input": r.get("input"),
                "bvid": r.get("bvid"),
                "title": r.get("title"),
                "sourceUrl": r.get("sourceUrl"),
                "refPath": base.rel(job, r.get("matchedPath")) if r.get("matchedPath") else "",
                "songPath": r.get("rawPath") or "",
            }
            for r in resolved if r.get("matched") == "search" and r.get("rawPath")
        ]
        base.write_json(report_path, report)
        target = _ensure_target(job, cfg, ff)
        base.log("fetch: 搜索完成，%d 首可用（候选 %d，下载失败 %d）" % (
            len(used), len(matched_all), len(failures)))
        return base.result(
            "done",
            [base.rel(job, report_path)] + [s["localPath"] for s in used],
            dict(report["counts"], keyword=keyword, mode="search", target=bool(target),
                 refSearch=len(report["ref_downloads"])),
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
    if _is_local_clients(sources):
        return _run_local_clients(job, cfg, sources)
    if _is_search(sources):
        return _run_search(job, cfg, sources)
    if str(job.get("mode") or "import").lower() != "import":
        return base.result(
            "failed", [], {},
            error="抓取模式缺 sources.search / query：无法搜索（插件 auto 模式应传 search 关键词）",
        )
    return _run_import(job, cfg)
