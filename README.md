# Voice Input Framework

基于大模型的语音输入法开发框架，支持实时流式语音识别。

## 特性

- 🎤 **实时音频采集**：支持麦克风实时录音，带 VAD（语音活动检测）
- 🔊 **音频处理**：降噪、分块、格式转换、端点检测
- 🚀 **流式识别**：一边录音一边识别，低延迟响应
- 🤖 **多模型支持**：Qwen3-ASR、Whisper 等，可扩展
- 🔌 **灵活架构**：客户端/服务端分离，支持远程部署
- 📦 **开箱即用**：完善的 CLI 和示例代码

## 项目结构

```
voice-input-framework/
├── client/                 # 用户端
│   ├── audio_capture.py   # 音频采集
│   ├── audio_processor.py # 音频处理
│   ├── stt_client.py      # STT 客户端
│   └── cli.py             # 命令行界面
├── server/                 # 服务端
│   ├── api.py             # FastAPI 服务
│   ├── stt_engine.py      # STT 引擎抽象
│   ├── models/            # 模型实现
│   │   ├── base.py       # 基类
│   │   ├── whisper.py    # Whisper
│   │   └── qwen_asr.py   # Qwen3-ASR
│   └── config.py          # 服务端配置
├── shared/                 # 共享模块
│   ├── protocol.py        # 通信协议
│   └── types.py           # 共享类型
├── deploy/                 # 部署配置
│   ├── daemon.sh          # 进程管理脚本
│   ├── launchd.plist     # macOS 开机自启动
│   └── voice-input-framework.service  # systemd (Linux)
└── examples/              # 示例代码
    ├── streaming_demo.py  # 流式识别演示
    └── file_transcribe.py # 文件转写演示
```

## 安装

```bash
# 克隆项目
cd ~
git clone https://github.com/3F3Feng/voice-input-framework.git
cd voice-input-framework

# 使用 OpenClaw 虚拟环境（已有 torch 等依赖）
# 或创建独立环境
python -m venv ~/.openclaw/workspace/.venv
source ~/.openclaw/workspace/.venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

## 快速开始

### 手动启动

```bash
# 使用 OpenClaw 虚拟环境
source ~/.openclaw/workspace/.venv/bin/activate

# 启动服务
python -m voice_input_framework.server.api

# 或指定配置
VIF_PORT=8765 VIF_DEFAULT_MODEL=qwen_asr python -m voice_input_framework.server.api
```

### macOS 开机自启动

```bash
# 复制 plist 到 LaunchAgents
mkdir -p ~/Library/LaunchAgents
cp deploy/launchd.plist ~/Library/LaunchAgents/

# 加载服务
launchctl load ~/Library/LaunchAgents/com.openclaw.voice-input-framework.plist

# 查看状态
launchctl list | grep voice-input-framework

# 查看日志
tail -f ~/voice-input-framework/logs/stdout.log
```

### 进程管理脚本

```bash
# 启动
./deploy/daemon.sh start

# 查看状态
./deploy/daemon.sh status

# 查看日志
./deploy/daemon.sh log

# 停止
./deploy/daemon.sh stop

# 重启
./deploy/daemon.sh restart
```

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_PORT` | 8765 | 服务端口 |
| `VIF_HOST` | 0.0.0.0 | 监听地址 |
| `VIF_DEFAULT_MODEL` | whisper | 默认模型 |
| `VIF_LOG_LEVEL` | INFO | 日志级别 |
| `VIF_LOG_FILE` | - | 日志文件路径 |

### 模型配置

编辑 `server/config.py` 或使用环境变量配置。

## 客户端使用

```bash
# 流式识别（实时语音输入）
python -m voice_input_framework.client.cli stream

# 文件转写
python -m voice_input_framework.client.cli transcribe audio.wav

# 列出可用模型
python -m voice_input_framework.client.cli list-models
```

## API 文档

### REST API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/select` | POST | 选择当前使用的模型 |
| `/transcribe` | POST | 文件上传转写 |

### WebSocket API

| 端点 | 说明 |
|------|------|
| `/ws/stream` | 流式语音识别 |

## 扩展模型

继承 `BaseSTTEngine` 实现自定义模型：

```python
from voice_input_framework.server.models.base import BaseSTTEngine

class MyCustomEngine(BaseSTTEngine):
    async def load_model(self) -> None:
        # 加载模型
        pass
    
    async def transcribe(self, audio: bytes) -> TranscriptionResult:
        # 同步转写
        pass
```

## 许可证

MIT License
