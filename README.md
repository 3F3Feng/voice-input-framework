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
└── examples/              # 示例代码
    ├── streaming_demo.py  # 流式识别演示
    └── file_transcribe.py # 文件转写演示
```

## 快速开始

### 安装

```bash
# 克隆项目
git clone https://github.com/your-org/voice-input-framework.git
cd voice-input-framework

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt
```

### 启动服务端

```bash
# 默认启动（Whisper 模型）
python -m voice_input_framework.server.api

# 指定模型和端口
python -m voice_input_framework.server.api --model whisper --port 8765

# 使用 Qwen3-ASR 模型
python -m voice_input_framework.server.api --model qwen_asr --port 8765
```

### 客户端使用

```bash
# 流式识别（实时）
python -m voice_input_framework.client.cli voice stream --server ws://localhost:8765/ws/stream

# 文件转写
python -m voice_input_framework.examples.file_transcribe audio.wav --server http://localhost:8765
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
