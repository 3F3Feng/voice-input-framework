# Pyannote Speaker Diarization 集成计划

## 背景

当前音频分段基于静音检测（VAD），存在以下问题：
- 对话中的 "嗯、对、好" 等回应被切成独立片段，孤立转写产生大量冗余
- 短片段输入 ASR 模型容易产生重复字幻觉（如 "去×60"）
- 句子可能在自然停顿处被切断

Pyannote.audio 的说话人分离（Speaker Diarization）按**说话人切换**边界切分，从根本上解决这些问题。

## 集成方案

### 数据流

```
音频文件 (99min WAV)
    │
    ├──→ Pyannote Diarization
    │      └──→ [{speaker: 0, start: 0.0, end: 5.2},
    │             {speaker: 1, start: 5.2, end: 12.8}, ...]
    │             │
    │             ▼ 按说话人边界切分（不会切断一句话）
    │             │
    └──→ 逐段 Qwen3-ASR 转写 → 拼接结果（标注说话人）

    Fallback: pyannote 不可用时 → 静音分段（现有方案）
```

### 阶段 1: Server 端

**新增文件**: `services/diarize_engine.py`
- DiarizationEngine 类（类似 STTEngine 结构）
- 加载 pyannote 预训练模型（pipeline）
- model_id: `pyannote/speaker-diarization-3.1`
- 输出格式: `[{speaker, start, end, confidence}]`

**修改文件**: `services/stt_server.py`
- 新增 `POST /diarize` 端点
- 新增 `GET /diarize/models` 端点
- 新增 `POST /diarize/models/select` 端点
- 新增 HealthStatus 中的 diarize 状态

**MacBook 依赖**:
```bash
pip install pyannote.audio==3.3.3
huggingface-cli login  # 模型 gated，需要 token
```

**Apple Silicon 性能**:
- Pyannote 用 PyTorch MPS 后端（非 MLX 原生）
- 99 分钟音频预计处理时间: 5-15 分钟
- 内存占用: ~2-4GB
- 没有专用的 MLX 版本，但 MPS 加速足够

### 阶段 2: Client 端

**修改文件**: `tools/audio_transcriber_gui.py`
- 新增 "使用说话人分离" Checkbox
- 新增 diarization 状态显示
- 分段策略: diarization → 静音 → 时间切分（三级 fallback）
- 结果中标注说话人: `[A]` `[B]` 标签
- 说话人着色

### 阶段 3: Fallback 优化

在 pyannote 集成之前，短期改进现有静音分段:
- 合并小片段: 相邻 <3s 的片段合并
- 最小片段长度: 不低于 3 秒
- 忽略纯语气片段: 仅包含 "嗯/啊/对" 的短片段

## API 设计

```json
// POST /diarize
// Content-Type: multipart/form-data; file=audio.wav
{
    "segments": [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.2, "confidence": 0.95},
        {"speaker": "SPEAKER_01", "start": 5.2, "end": 12.8, "confidence": 0.92},
        {"speaker": "SPEAKER_00", "start": 12.8, "end": 18.5, "confidence": 0.88}
    ],
    "model": "pyannote/speaker-diarization-3.1",
    "duration": 5940.0  // 99 分钟
}
```

## 里程碑

| # | 内容 | 工作量 |
|---|------|--------|
| 1 | 安装 pyannote + HF 授权 | 15min |
| 2 | DiarizationEngine 类 | 1h |
| 3 | `POST /diarize` 端点 | 0.5h |
| 4 | GUI 集成（分段 + 说话人标签） | 1h |
| 5 | 三级 fallback + 小片段合并 | 0.5h |
| 6 | 端到端测试（99分钟音频） | 1h |
