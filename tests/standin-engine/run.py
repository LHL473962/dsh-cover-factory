#!/usr/bin/env python3
"""替身引擎（仅供插件自测，不是真引擎）。

真引擎在 <插件>/engine/ 下，由引擎侧维护。这个文件只实现 plugin 依赖的四条 CLI：

    run.py init   --config job_config.json --workdir <dir>
    run.py run    --job job.json [--from stage] [--to stage] [--resume] [--allow-duet]
    run.py status --job job.json
    run.py cancel --job job.json

行为：init 写 job.json（stages 全 pending）；run 逐阶段 done（每段 sleep 0.2s），
mix 造 out.wav、qa 造 report.json、export 造 out.mp3；status 原样打印 job.json；cancel 直接成功。
用它可以在没有 GPU、没有真引擎的情况下把插件八个工具跑一遍（doctor 也做了替身）。

刻意约定（也是插件对真引擎的假设，引擎侧可按需调整但要两边一致）：
- job.json 落在 --workdir 根目录，字段 version/id/singer/mode/sources/refs/target/options/stages/result
- stages 是 { stage: { status } } 的对象；status 取值 pending|running|done|failed|skipped|cancelled
- 退出码 0 成功 / 1 失败 / 2 需要用户决策
"""

import json
import os
import sys
import time

argv = sys.argv[1:]
cmd = argv[0] if argv else ''

STAGES = ['fetch', 'separate', 'select', 'clean', 'dataset', 'train', 'convert', 'mix', 'qa', 'export']


def opt(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def load(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def log(wd, stage, text):
    logs = os.path.join(wd, 'logs')
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, stage + '.log'), 'a', encoding='utf-8') as fh:
        fh.write('[' + time.strftime('%H:%M:%S') + '] ' + text + '\n')


if cmd == 'doctor':
    here = os.path.dirname(os.path.abspath(__file__))
    red = os.path.exists(os.path.join(here, 'doctor-red.flag'))
    checks = [
        {'id': 'python', 'label': 'Python', 'status': 'ok', 'required': True, 'detail': '3.12 (standin)'},
        {'id': 'ffmpeg', 'label': 'ffmpeg', 'status': 'ok', 'required': True, 'detail': 'imageio-ffmpeg'},
        {'id': 'applio', 'label': 'Applio', 'status': 'missing' if red else 'ok',
         'required': True, 'hint': 'git clone https://github.com/IAHispano/Applio && pip install -r requirements.txt'},
        {'id': 'msst', 'label': 'MSST 分离', 'status': 'ok', 'required': True},
        {'id': 'ytdlp', 'label': 'yt-dlp（抓取模式，可选）', 'status': 'ok', 'required': False,
         'hint': 'pip install yt-dlp'},
        {'id': 'gpu_torch_match', 'label': '显卡与 torch 匹配', 'status': 'ok', 'required': True,
         'detail': 'RTX 5060 Laptop (sm_120) in arch_list [sm_120]',
         'suggestedTorch': {
             'index': 'https://download.pytorch.org/whl/cu128',
             'torch': 'torch>=2.7（50 系必须 cu128）',
             'pip': 'standin-python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128',
             'gpu': 'NVIDIA GeForce RTX 5060 Laptop GPU', 'capability': 'sm_120',
             'note': 'sm_120 只有 cu128 轮子带 kernel',
         }},
    ]
    print(json.dumps({
        'ok': not red,
        # A 卡测试：STANDIN_GPU_VENDOR=amd|intel|nvidia（默认 nvidia）
        'gpu_vendor': os.environ.get('STANDIN_GPU_VENDOR', 'nvidia'),
        'checks': checks,
        'hints': ['见 docs/INSTALL.md 的路径 B'] + (['先装 Applio 再重新体检'] if red else []),
        'suggestedTorch': {
            'gpu': 'NVIDIA GeForce RTX 5060 Laptop GPU', 'capability': 'sm_120',
            'cuda': 'cu128', 'torch': '2.8.x',
            'indexUrl': 'https://download.pytorch.org/whl/cu128',
            'python': 'standin-python.exe',
            'reason': 'Blackwell 只有 cu128+ 才有 sm_120 内核',
        },
    }, ensure_ascii=False))
    sys.exit(0)

if cmd == 'init':
    cfg = load(opt('--config'))
    wd = opt('--workdir')
    job = {
        'version': 1,
        'id': cfg.get('id'),
        'singer': cfg.get('singer'),
        'mode': cfg.get('mode'),
        'sources': cfg.get('sources'),
        'refs': cfg.get('refs') or [],
        'target': cfg.get('target'),
        'options': cfg.get('options') or {},
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'stages': {s: {'status': 'pending'} for s in STAGES},
        'result': None,
    }
    save(os.path.join(wd, 'job.json'), job)
    print(json.dumps({'ok': True, 'job': os.path.join(wd, 'job.json')}))
    sys.exit(0)

if cmd == 'status':
    # ensure_ascii=True：Windows 控制台是 cp936，中文直接输出会被插件按 UTF-8 解成乱码
    print(json.dumps(load(opt('--job')), ensure_ascii=True))
    sys.exit(0)

if cmd == 'cancel':
    print(json.dumps({'ok': True, 'cancelled': True}))
    sys.exit(0)

if cmd == 'run':
    jp = opt('--job')
    wd = os.path.dirname(jp)
    job = load(jp)
    frm = opt('--from') or 'fetch'
    to = opt('--to') or 'export'
    if frm not in STAGES or to not in STAGES or STAGES.index(frm) > STAGES.index(to):
        print(json.dumps({'ok': False, 'error': 'BAD_RANGE'}))
        sys.exit(1)
    for stage in STAGES[STAGES.index(frm):STAGES.index(to) + 1]:
        job['stages'][stage] = {'status': 'running'}
        save(jp, job)
        log(wd, stage, stage + ' running')
        time.sleep(0.35)  # 10 段 ≈3.5s：cover_run 要等 2.5s 早退窗口，取消测试时仍需「正在跑」
        if stage == 'mix':
            os.makedirs(os.path.join(wd, 'mix'), exist_ok=True)
            with open(os.path.join(wd, 'mix', 'out.wav'), 'wb') as fh:
                fh.write(b'RIFF' + b'\0' * 5000)
        if stage == 'qa':
            os.makedirs(os.path.join(wd, 'qa'), exist_ok=True)
            with open(os.path.join(wd, 'qa', 'report.json'), 'w', encoding='utf-8') as fh:
                json.dump({'lufs': -13.2, 'clicks_per_min': 4, 'range_cover': 0.78}, fh)
        if stage == 'export':
            os.makedirs(os.path.join(wd, 'export'), exist_ok=True)
            mp3_path = os.path.join(wd, 'export', 'out.mp3')
            with open(mp3_path, 'wb') as fh:
                fh.write(b'ID3' + b'\0' * 8000)
            # 真引擎会把成品复制到 outputDir 并记进 job.result，这里同样记路径
            job['result'] = {'mp3': mp3_path, 'report': None, 'qaOk': True}
        job['stages'][stage] = {'status': 'done', 'finishedAt': time.strftime('%Y-%m-%dT%H:%M:%S')}
        save(jp, job)
        log(wd, stage, stage + ' done')
    job['updatedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    save(jp, job)
    sys.exit(0)

print(json.dumps({'ok': False, 'error': 'BAD_CMD'}))
sys.exit(1)
