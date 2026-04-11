# Voice Input Framework

基于大模型的语音识别框架，支持实时流式语音识别。

> **NEW**: 分离架构 - STT 和 LLM 独立服务，解决 transformers 版本冲突问题。详见 [docs/split-architecture.md](docs/split-architecture.md)

## 🎯 下载 Windows 客户端 (免安装)

直接从 GitHub Releases 下载 exe 文件：

https://github.com/3F3Feng/voice-input-framework/releases

## ✨ 特性

- 🎤 **实时音频采集**：支持麦克风实时录音
- 🚀 **流式识别**：低延迟响应
- 🤖 **多模型支持**：
  - **Qwen3-ASR-1.7B** (推荐) - 52种语言/方言，加载快 (~27秒)
  - **Qwen3-ASR-0.6B** - 更快，适合实时场景
  - **Whisper-large-v3** - OpenAI 经典模型
  - **Whisper-small** - 轻量级，快速启动
- 🔌 **客户端/服务端分离**：支持远程部署
- 🖥️ **跨平台 GUI 客户端**：Windows/macOS/Linux

## 📦 支持的模型

| 模型 | 参数量 | 加载时间 | 特点 |
|------|--------|----------|------|
| qwen_asr (Qwen3-ASR-1.7B) | 1.7B | ~27秒 | 推荐，52种语言/方言 |
| qwen_asr_small (Qwen3-ASR-0.6B) | 0.6B | ~10秒 | 更快，实时场景 |
| whisper | 1.5B | ~3秒 | OpenAI 经典 |
| whisper-small | 0.4B | ~1秒 | 轻量级 |

## 🚀 快速开始

### 方式 1: 分离架构 (推荐)

解决 STT (qwen_asr) 和 LLM (mlx-lm) 的 transformers 版本冲突。

```bash
# 1. 创建 STT 环境 (首次)
./scripts/setup_stt_env.sh

# 2. 启动两个服务
./scripts/start_services.sh start

# 3. 检查状态
./scripts/start_services.sh status
```

服务地址:
- STT Service: http://localhost:6544
- LLM Service: http://localhost:6545

### 方式 2: 传统架构

适用于不需要 LLM 后处理的场景。

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
bash deploy/daemon.sh start
```

### 客户端

```bash
# Python 客户端
python run_client.py

# 或直接运行 GUI
python -m client.gui
```

## 📡 API

### 分离架构 API

**STT Service (Port 6544)**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/transcribe` | POST | 转写音频文件 |
| `/ws/stream` | WebSocket | 流式识别 |

**LLM Service (Port 6545)**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/select` | POST | 切换模型 |
| `/process` | POST | 处理文本 |

### 传统架构 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/status/{model}` | GET | 查询模型加载状态 |
| `/models/select` | POST | 切换模型 |
| `/ws/stream` | WebSocket | 流式识别 |

## 🔧 配置

### 服务端环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_PORT` | 6543 | 服务端口 |
| `VIF_HOST` | 0.0.0.0 | 监听地址 |
| `VIF_DEFAULT_MODEL` | qwen_asr | 默认模型 |

## 📄 许可证

MIT License
