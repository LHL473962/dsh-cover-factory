# engine/assets —— 从现有脚本移植过来的命令与 DSP

原则：只搬运，不重新发明。每个函数都标了来源文件。

| 文件 | 内容 | 移植自 |
| --- | --- | --- |
| audio.py | ffmpeg 定位、probe（时长/声道）、ebur128 响度/峰值、f32le 解码、转码 | <DATA_DIR>/out/tuning/preflight.py（probe/loud/FF 路径） |
| analysis.py | 逐帧 F0（归一化自相关）、男声区占比、频谱质心、立体声相关、断音率（20ms 能量坑）、音域覆盖 | <DATA_DIR>/out/tuning/artifacts.py（gaps/f0jit）、phrase_plan.py（med_f0 自相关） |
| commands.py | 分离 / 训练(prep,train,index) / 换声 / 混音 / mp3 的命令与 filter graph | MSST inference.py、Applio dsh_rvc2.py、old_rvc tools/infer_cli.py、mix_unity.sh |
| mix_unity.sh | 混音链 shell 版（手工复现/对照用；Python 侧走 commands.mix_filter） | <DATA_DIR>/out/tuning/mix_unity.sh |

## 命令对照

分离（MSST，mel_band_roformer + kim 权重）：

    <msst_python> inference.py --model_type mel_band_roformer \
      --config_path configs/config_vocals_mel_band_roformer_kim.yaml \
      --start_check_point pretrain/MelBandRoformer_kim.ckpt \
      --input_folder <work/raw> --store_dir <work/stems> \
      --extract_instrumental --disable_detailed_pbar [--force_cpu] [--device_ids 0]

产物：work/stems/<文件名>/{vocals.wav,instrumental.wav}。

训练（Applio core 脚本；SUIS_MODEL / SUIS_DATASET，另加 APPLIO_LOGS_DIR 把模型留在 job 目录）：

    SUIS_MODEL=<model_id> SUIS_DATASET=<work/dataset/<job>> python dsh_rvc2.py prep
    SUIS_MODEL=<model_id> SUIS_DATASET=<work/dataset/<job>> python dsh_rvc2.py train <epochs>

换声（旧版 RVC，2023 代码）：

    <cwd=rvc_root> <rvc_python> tools/infer_cli.py --model_name model.pth \
      --index_path <model.index> --input_path <target vocals> --opt_path <work/convert/vocal.wav> \
      --f0method rmvpe --device cuda:0 --is_half True --index_rate 0.75 --protect 0.33 \
      --filter_radius 3 --rms_mix_rate 1.0 --f0up_key 0 --resample_sr 0

cwd 必须是 rvc 根目录（它 from configs.config import Config 并读 configs/v1/*.json）；
weight_root 环境变量指向模型目录，所以不需要往 old_rvc/assets 里拷任何东西。

混音：见 commands.mix_filter / mix_unity.sh（人声 0dB + 伴奏 0dB、260Hz -1.2、9k +1.6、2.8k 让位 -1、
IR 1.2s 湿 30%、返回加宽 1.8x、伴奏加宽 1.15x、1.3:1 glue、静响 -14 LUFS、limiter -1.5dB）。
