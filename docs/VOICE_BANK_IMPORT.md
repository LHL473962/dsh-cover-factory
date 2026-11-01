# 为什么有些 AI 翻唱听起来“特别像” / 本地音库导入

## 一、BV1hXbN6VEKV 的真相

翻唱区很多“高质量 AI 翻唱”不是 RVC 做的，而是 **Synthesizer V 官方音库**：

- 视频简介写明：`基于：Synthesizer V 夢ノ結唱 HALO`
- 夢ノ結唱（ROSE / MEW / HALO）是 BanG Dream! 与 Synthesizer V 合作的**官方商业音源**
- 录音棚按音素/音高/力度分层录制，再由 SvB 引擎重合成

**所以好听的原因是：音库本身就是官方录制的完整音色库，不是从几首歌里反推出来的。**

## 二、RVC 和 SvB 的本质差别

| | RVC（我们） | Synthesizer V（官方音库） |
|---|---|---|
| 音色来源 | 从目标歌手歌曲里反推 | 官方录音棚分音素录制 |
| 音域覆盖 | 受素材限制 | 设计时覆盖全音域 |
| 气声/嘎裂/弱声 | 素材少就学不会 | 有专门采样 |
| 音高控制 | f0 跟随，易八度错 | 音符级精确控制 |
| 版权 | 自训/社区模型 | 商业授权，不可导出 |

## 三、本地音库导入：能导什么

### 可以：RVC / Applio 模型（.pth + .index）

社区已有大量训练好的 RVC 模型，导入后**不需要再训练**，直接换声。

### 不可以：Synthesizer V 官方音库

- 闭源、加密、绑定官方编辑器
- 没有可导出的声学模型文件
- 想在 SvB 里用，只能用官方软件渲染

### 以后可以：DiffSinger / OpenUtau 音库

- 开源格式（.onnx / 音素表）
- 需要另一套推理引擎，不是 RVC 权重

## 四、插件实现方案（RVC 音库导入）

```
输入：一个 .pth（可带同名 .index），或包含它们的目录
流程：跳过 fetch/select/dataset/train
      → 只分离目标曲人声
      → 用导入的模型换声
      → 混音 / 质检 / 导出
```

接口：`cover_setup({ singer, target, voiceBank: "<pth 或目录>" })`

## 五、当前状态（2026-11-02）

- ✅ 工具参数 `voiceBank` 已加（写入 options.reuseModel）
- ✅ 引擎 train/convert 已支持 reuseModel
- ⏳ 「无素材直通」流程（跳过 fetch/select/dataset）待接，明天和 2P 一起做
- 暂时用法：给一个同歌手素材目录（可为空占位）+ voiceBank，训练阶段会自动跳过