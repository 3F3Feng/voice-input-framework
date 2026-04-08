# Models Directory

此目录用于存放 STT 模型文件。

## 下载模型

### 自动下载（推荐）
```bash
# 下载所有模型
./scripts/download-models.sh 4

# 下载指定模型
./scripts/download-models.sh 1  # Whisper Small
./scripts/download-models.sh 2  # Qwen3-ASR 0.6B
./scripts/download-models.sh 3  # Whisper Large v3
```

### 手动下载

```bash
# 设置模型目录
export VOICE_INPUT_FRAMEWORK_MODELS_DIR=~/voice-input-framework/models

# Whisper Small
huggingface-cli download openai/whisper-small --local $VOICE_INPUT_FRAMEWORK_MODELS_DIR/whisper-small

# Qwen3-ASR 0.6B (ModelScope，国内推荐)
modelscope download Qwen/Qwen3-ASR-0.6B --local $VOICE_INPUT_FRAMEWORK_MODELS_DIR/qwen3-asr-0.6b

# Whisper Large v3
huggingface-cli download openai/whisper-large-v3 --local $VOICE_INPUT_FRAMEWORK_MODELS_DIR/whisper-large-v3
```

## 模型列表

| 模型 | 大小 | 语言 | 内存需求 | 推荐场景 |
|------|------|------|----------|----------|
| whisper-small | ~242MB | 多语言 | ~1GB | 轻量快速，英语为主 |
| qwen3-asr-1.7b | ~3.5GB | 中英 | ~5GB | **中文首选，高准确率** |
| whisper-large-v3 | ~3.1GB | 99+ | ~6GB | 最高准确率 |

## 配置

在 `server/config.py` 中指定模型路径：

```python
config.add_model(ModelConfig(
    name="whisper-small",
    model_path="./models/whisper-small",
    device="auto",
))
```
