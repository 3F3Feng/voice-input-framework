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
  - **MLX 加速** - Apple Silicon 原生优化
- 🧠 **LLM 后处理** - 自动优化识别结果
- 🔌 **客户端/服务端分离**：支持远程部署
- 🖥️ **跨平台 GUI 客户端**：Windows/macOS/Linux

## 📦 支持的模型

### STT 模型

| 模型 | 参数量 | 加载时间 | 特点 | 架构 |
|------|--------|----------|------|------|
| qwen_asr_mlx | 1.7B | ~15秒 | **推荐 (Apple Silicon)** | 主服务 |
| qwen_asr | 1.7B | ~27秒 | 推荐，52种语言/方言 | 分离架构 |
| qwen_asr_small | 0.6B | ~10秒 | 更快，实时场景 | 两者 |
| whisper_mlx | 1.5B | ~5秒 | MLX 加速 Whisper | 主服务 |
| whisper_mlx_turbo | 0.8B | ~3秒 | MLX Whisper Turbo | 主服务 |
| whisper | 1.5B | ~3秒 | OpenAI 经典 | 两者 |
| whisper-small | 0.4B | ~1秒 | 轻量级 | 两者 |
| whisper_cpp | - | ~1秒 | C++ 实现，低延迟 | 两者 |

### LLM 后处理模型

| 模型 | 参数量 | 内存占用 | 特点 |
|------|--------|----------|------|
| Qwen3.5-4B-OptiQ | 4B | ~2.5GB | **默认**，中文能力强 |
| Qwen3.5-2B-OptiQ | 2B | ~1.5GB | 速度更快 |
| Gemma-4-E4B-DECKARD | 4B | ~2.5GB | Google 模型 |

## 🚀 快速开始

### 方式 1: 分离架构 (推荐)

解决 STT (qwen_asr) 和 LLM (mlx-lm) 的 transformers 版本冲突。

```bash
# 1. 创建 conda 环境 (首次)
conda create -n vif-stt python=3.11 -y
conda activate vif-stt
pip install -r requirements-stt.txt

conda create -n mlx-test python=3.11 -y
conda activate mlx-test
pip install -r requirements-llm.txt

# 2. 配置 launchd (macOS，自动启动)
cp config/launchd/*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.voiceinput.stt.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.voiceinput.llm.plist

# 3. 检查状态
curl http://localhost:6544/health
curl http://localhost:6545/health
```

服务地址:
- STT Service: http://localhost:6544
- LLM Service: http://localhost:6545

### 方式 2: 主服务 (单体架构)

包含 STT + LLM 所有功能，适合本地开发。

```bash
# 创建 venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 启动服务
python -m server.api
```

服务地址: http://localhost:6543

### 方式 3: 传统架构

适用于不需要 LLM 后处理的场景。

```bash
pip install -r requirements.txt
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
| `/models/select` | POST | 切换模型 |
| `/models/status/{model}` | GET | 查询模型加载状态 |
| `/transcribe` | POST | 转写音频文件 |
| `/ws/stream` | WebSocket | 流式识别 |

**LLM Service (Port 6545)**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/select` | POST | 切换模型 |
| `/process` | POST | 处理文本 |

### 主服务 API (Port 6543)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用 STT 模型列表 |
| `/models/select` | POST | 切换 STT 模型 |
| `/models/status/{model}` | GET | 查询模型加载状态 |
| `/llm/models` | GET | 获取 LLM 模型列表 |
| `/llm/models/select` | POST | 切换 LLM 模型 |
| `/ws/stream` | WebSocket | 流式识别 |

## 🔧 配置

### 服务端环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_PORT` | 6543 | 主服务端口 |
| `VIF_HOST` | 0.0.0.0 | 主服务监听地址 |
| `VIF_DEFAULT_MODEL` | qwen_asr_mlx | 默认 STT 模型 |
| `VIF_STT_PORT` | 6544 | STT 服务端口 |
| `VIF_STT_HOST` | 0.0.0.0 | STT 服务监听地址 |
| `VIF_LLM_PORT` | 6545 | LLM 服务端口 |
| `VIF_LLM_HOST` | 127.0.0.1 | LLM 服务监听地址 (仅本地) |

### 客户端配置

客户端配置保存在 `~/.voice-input/config.json`，支持：
- 服务器地址和端口
- 快捷键设置
- LLM 启用/禁用
- UI 设置（透明度、最小化等）

## 🧪 测试

```bash
# 安装测试依赖
pip install pytest pytest-asyncio

# 运行单元测试
.venv/bin/python -m pytest tests/ -v

# 仅运行 API 端点测试 (需要服务器运行)
.venv/bin/python -m pytest tests/test_api_endpoints.py -v

# 跳过集成测试
.venv/bin/python -m pytest tests/ -v -m "not integration"
```

## 📄 许可证

MIT License
