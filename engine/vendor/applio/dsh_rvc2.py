# -*- coding: utf-8 -*-
"""翻唱工厂训练/推理驱动（随仓库分发）。

用法：由 engine/assets/commands.py 调用，工作目录切到 Applio 根目录：
    APPLIO_ROOT=<Applio 目录> python dsh_rvc2.py train 120
模型名与数据集走环境变量 SUIS_MODEL / SUIS_DATASET。

依赖：Applio 本体 + 其 requirements。
注意：训练（prep/train）不依赖 fairseq（Applio 用 transformers 加载 hubert/contentvec）；
只有旧版 RVC 换声（engine/stages/convert.py 调 infer_cli）才需要 fairseq。
"""
import os, sys, glob

APPLIO_ROOT = os.environ.get("APPLIO_ROOT") or os.environ.get("APPLIO_DIR") or ""
if not APPLIO_ROOT or not os.path.isdir(APPLIO_ROOT):
    here = os.path.dirname(os.path.abspath(__file__))
    # 兼容：驱动被复制到 Applio 根目录的情况
    APPLIO_ROOT = here
os.chdir(APPLIO_ROOT)
sys.path.insert(0, APPLIO_ROOT)

MODEL = os.environ.get("SUIS_MODEL", "suis2")
DATASET = os.environ.get("SUIS_DATASET", "")
SR = int(os.environ.get("SUIS_SR", "40000"))
CORES = int(os.environ.get("SUIS_CORES", "6"))


def prep():
    from core import run_preprocess_script, run_extract_script
    print(run_preprocess_script(model_name=MODEL, dataset_path=DATASET, sample_rate=SR, cpu_cores=CORES,
        cut_preprocess='Automatic', process_effects=False, chunk_len=3.0, overlap_len=0.3,
        normalization_mode='none'), flush=True)
    print(run_extract_script(model_name=MODEL, f0_method='rmvpe', cpu_cores=CORES, gpu=0, sample_rate=SR,
        embedder_model='contentvec', embedder_model_custom=None, include_mutes=2), flush=True)


def train(epochs=120, batch=8):
    from core import run_train_script, run_index_script
    print(run_train_script(model_name=MODEL, save_every_epoch=5, save_only_latest=False, save_every_weights=True,
        total_epoch=epochs, sample_rate=SR, batch_size=batch, gpu=0, pretrained=True, cleanup=False,
        index_algorithm='Auto', cache_data_in_gpu=False, custom_pretrained=False, g_pretrained_path=None,
        d_pretrained_path=None, vocoder='HiFi-GAN', checkpointing=False, shutdown_check=False), flush=True)
    print(run_index_script(model_name=MODEL, index_algorithm='Auto'), flush=True)


def infer(src, dst, pitch=0, index_rate=0.7, protect=0.33, pe='rmvpe', formant=0, model_dir=None):
    from core import run_infer_script
    logs = os.path.join(APPLIO_ROOT, 'logs', model_dir or MODEL)
    by = lambda pat: sorted(glob.glob(os.path.join(logs, pat)), key=os.path.getmtime)
    pths = by('*e_*.pth') or by('*.pth')
    idxs = by('*.index')
    if not pths:
        raise SystemExit('找不到模型 pth：' + logs)
    pth = pths[-1]; idx = idxs[-1] if idxs else ''
    print('pth:', pth, 'index:', idx, flush=True)
    msg, out = run_infer_script(pitch=pitch, index_rate=index_rate, volume_envelope=1.0,
        protect=max(0.33, min(0.5, protect)), f0_method=pe, input_path=src, output_path=dst,
        pth_path=pth, index_path=idx, f0_autotune=False, f0_autotune_strength=1.0, proposed_pitch=False,
        proposed_pitch_threshold=0.5, export_format='WAV', embedder_model='contentvec',
        embedder_model_custom=None, formant_shifting=bool(formant),
        formant_qfrency=(float(formant) if formant else 1.0), formant_timbre=1.0,
        sid=0, inference_engine=None, onnx_precision='auto', cleanup_mode='off', cleanup_strength=10)
    print(msg, out, flush=True)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: prep | train [epochs] | infer src dst [pitch] [index_rate] [protect]', flush=True)
        raise SystemExit(0)
    cmd = sys.argv[1]
    if cmd == 'prep':
        prep()
    elif cmd == 'train':
        train(int(sys.argv[2]) if len(sys.argv) > 2 else 120)
    elif cmd == 'infer':
        infer(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 0,
              float(sys.argv[5]) if len(sys.argv) > 5 else 0.7, float(sys.argv[6]) if len(sys.argv) > 6 else 0.33)
    else:
        raise SystemExit('usage: prep | train [epochs] | infer src dst [pitch]')
