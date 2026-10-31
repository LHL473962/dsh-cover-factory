import os

from fairseq import checkpoint_utils


def get_index_path_from_model(sid):
    """index_root 为空/目录不存在时返回空串；保留原版按 sid 匹配 .index 的语义。"""
    root = os.getenv('index_root') or ''
    if not root or not os.path.isdir(root):
        return ''
    sid_key = str(sid or '').split('.')[0]
    return next(
        (
            f
            for f in [
                os.path.join(r, name)
                for r, _, files in os.walk(root, topdown=False)
                for name in files
                if name.endswith('.index') and 'trained' not in name
            ]
            if sid_key and sid_key in f
        ),
        '',
    )

def load_hubert(config):
    models, _, _ = checkpoint_utils.load_model_ensemble_and_task(
        ["assets/hubert/hubert_base.pt"],
        suffix="",
    )
    hubert_model = models[0]
    hubert_model = hubert_model.to(config.device)
    if config.is_half:
        hubert_model = hubert_model.half()
    else:
        hubert_model = hubert_model.float()
    return hubert_model.eval()
