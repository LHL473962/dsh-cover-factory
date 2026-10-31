# RVC 2.2.231006 compatibility patches

这些文件是旧版 RVC（`RVC-Project/Retrieval-based-Voice-Conversion-WebUI` tag `2.2.231006`，MIT）的兼容补丁；
引擎 `bootstrap install_old_rvc` 在 clone 后会自动执行 `apply.py` 覆盖到 RVC 根目录。

为什么需要：

1. **PyAV 新版本**：`infer/lib/audio.py` 里 `av.open(path, 'rb')` 已不能这样用；改为调用 ffmpeg 解码（`RVC_FFMPEG` 环境变量可由引擎注入，缺省用 PATH 里的 ffmpeg）。
2. **torch >= 2.6**：`torch.load` 默认 `weights_only=True`，RVC 的 rmvpe/模型权重会加载失败；三处 `torch.load(..., weights_only=False)`。
3. **index 为空**：`index_root` 未设置时 `os.walk(None)` 崩溃；`infer/modules/vc/utils.py` 返回空串，`tools/infer_cli.py` 把 `None` index 归一成空串，允许空 index 换声。
4. `infer/lib/audio.py` 的 `av.open` 参数写法也同步到新 PyAV API（`av.open(i)` / `av.open(o, 'w', ...)`）。

文件：

    infer/lib/audio.py
    infer/lib/jit/get_rmvpe.py
    infer/lib/jit/get_synthesizer.py
    infer/lib/rmvpe.py
    infer/modules/vc/utils.py
    tools/infer_cli.py

用法：

    python engine/vendor/rvc_patches/apply.py <rvc_root>

- 首次覆盖前会保留 `<file>.orig`（重复 apply 不会覆盖 .orig）；
- 幂等：内容相同的文件跳过，写 `.dsh_rvc_patches.json` 标记。

许可证：RVC 2.2.231006 为 MIT；本目录 `LICENSE` 是上游许可证副本，补丁文件同样按 MIT 分发。