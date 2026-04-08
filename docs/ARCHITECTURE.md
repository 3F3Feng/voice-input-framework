# Voice Input Framework - 系统架构文档

## 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         客户端 (Client)                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐     │
│  │  麦克风      │───▶│ 音频采集器    │───▶│ WebSocket   │     │
│  │ Microphone  │    │ AudioCapture │    │   Client    │     │
│  └──────────────┘    └──────────────┘    └──────┬───────┘     │
│                                                 │              │
└─────────────────────────────────────────────────┼──────────────┘
                                                  │ WebSocket
                                                  │ (ws://host:port/ws/stream)
                                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                         服务端 (Server)                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐     │
│  │  WebSocket  │───▶│ STT Engine   │───▶│   模型       │     │
│  │   Handler   │    │   Manager    │    │ (Whisper/   │     │
│  └──────────────┘    └──────────────┘    │  Qwen2Audio) │     │
│                                      │    └──────────────┘     │
│                                      │                        │
│  ┌──────────────┐                   │                        │
│  │   REST API   │───────────────────┘                        │
│  │ /health     │                                           │
│  │ /models     │                                           │
│  │ /transcribe │                                           │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
voice-input-framework/
├── client/
│   ├── audio_capture.py    # 音频采集（sounddevice）
│   ├── stt_client.py       # WebSocket/REST 客户端
│   └── cli.py             # 命令行界面
├── server/
│   ├── api.py             # FastAPI 服务 + WebSocket
│   ├── config.py          # 服务配置
│   ├── stt_engine.py       # STT 引擎管理器
│   └── models/
│       ├── base.py         # 引擎基类
│       ├── whisper.py      # Whisper 实现
│       └── qwen_asr.py    # Qwen2Audio 实现
├── shared/
│   ├── protocol.py         # 通信协议定义
│   └── data_types.py       # 数据类型
├── deploy/
│   ├── daemon.sh          # 进程管理脚本
│   └── launchd.plist      # macOS 自启动
├── run_cli.py              # CLI 客户端入口
├── run_gui.py              # GUI 客户端入口 (PySide6)
└── run_gui_qt.py          # GUI 客户端入口 (PyQt6)
```

## 通信协议

### WebSocket 流式识别 (`/ws/stream`)

#### 1. 客户端 → 服务端

| 消息类型 | 字段 | 说明 |
|----------|------|------|
| `config` | `type`, `language` | 连接初始化配置 |
| `audio` | `type`, `data` (base64) | 音频数据块 |
| `end` | `type` | 结束信号 |

**示例：**
```json
// 配置
{"type": "config", "language": "auto"}

// 音频
{"type": "audio", "data": "base64encoded_audio..."}

// 结束
{"type": "end"}
```

#### 2. 服务端 → 客户端

| 消息类型 | 字段 | 说明 |
|----------|------|------|
| `ready` | `type`, `model` | 连接就绪 |
| `result` | `type`, `text`, `confidence`, `language`, `is_final` | 识别结果 |
| `done` | `type` | 识别完成 |
| `error` | `type`, `error_code`, `error_message` | 错误信息 |

**示例：**
```json
// 就绪
{"type": "ready", "model": "qwen_asr"}

// 识别结果
{"type": "result", "text": "识别文字", "confidence": 1.0, "language": "zh", "is_final": true}

// 完成
{"type": "done"}

// 错误
{"type": "error", "error_code": "E5001", "error_message": "..."}
```

### REST API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/select` | POST | 切换模型 (form: `model_name`) |
| `/transcribe` | POST | 文件转写 (form: `file`, `language`) |

## 客户端流程

### CLI 客户端 (`run_cli.py`)

```
启动
  │
  ▼
显示菜单 ───────────────────────────────────────▶ 用户选择
  │                                                   │
  │ 1. 开始录音                                        │
  │    │                                              │
  │    ▼                                              │
  │ 连接服务器 WebSocket ──────────────────────────────▶ │
  │    │                                              │
  │    ▼                                              │
  │ 发送 config {"type": "config", ...}              │
  │    │                                              │
  │    ▼                                              │
  │ 等待 ready 响应                                   │
  │    │                                              │
  │    ▼                                              │
  │ 从麦克风采集音频 ────────────────────────────────▶   │
  │    │                                              │
  │    ▼                                              │
  │ 发送音频 {"type": "audio", "data": ...}           │
  │    │                                              │
  │    ▼                                              │
  │ 接收 result {"type": "result", ...}              │
  │    │                                              │
  │    ▼                                              │
  │ 用户按 Enter                                      │
  │    │                                              │
  │    ▼                                              │
  │ 发送结束 {"type": "end"}                          │
  │    │                                              │
  │    ▼                                              │
  │ 接收 done {"type": "done"}                        │
  │    │                                              │
  │    ▼                                              │
  │ 复制结果到剪贴板                                   │
  │    │                                              │
  └────┘
```

### 音频采集 (`AudioCapture`)

- 使用 `sounddevice` 库
- 采样率: 16000 Hz
- 声道: 1 (单声道)
- 格式: 16-bit PCM (int16)
- 块大小: 1024 frames

## 服务端流程

### WebSocket 处理

```
连接
  │
  ▼
accept() ──────────────────────────────────────▶ 发送 ready
  │                                                 │
  ▼                                                 │
接收 config {"type": "config"}                       │
  │                                                 │
  ▼                                                 │
获取当前 STT 引擎                                    │
  │                                                 │
  ▼                                                 │
进入音频生成器循环 ◀────────────────────────────────┐
  │                                                 │
  ▼                                                 │
接收 audio {"type": "audio", "data": ...}           │
  │                                                 │
  ▼                                                 │
发送给 STT 引擎进行识别                              │
  │                                                 │
  ▼                                                 │
发送 result {"type": "result", ...} ─────────────────┘
  │                                              ▲
  │                                              │ 循环
  ▼                                              │
接收 end {"type": "end"} ──────────────────────────┘
  │
  ▼
发送 done {"type": "done"}
  │
  ▼
关闭连接
```

### STT 引擎

#### Whisper (`WhisperEngine`)

- 使用 `transformers` 库
- 模型: `openai/whisper-large-v3`
- 支持 99+ 语言
- 流式处理: 累积 10 个音频块后识别

#### Qwen2Audio (`QwenASREngine`)

- 使用 `transformers` 库
- 模型: `Qwen/Qwen2-Audio-7B-Instruct`
- 专门优化中文和英文混输
- 流式处理: 累积 5 个音频块后识别

## 配置

### 客户端配置 (`~/.voice_input_config.json`)

```json
{
  "server_host": "localhost",
  "server_port": 6543,
  "hotkey": "alt+space",
  "language": "auto",
  "auto_paste": true,
  "microphone": "default",
  "sample_rate": 16000
}
```

### 服务端配置 (环境变量)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_PORT` | 6543 | 服务端口 |
| `VIF_HOST` | 0.0.0.0 | 监听地址 |
| `VIF_DEFAULT_MODEL` | whisper | 默认模型 |
| `VIF_LOG_LEVEL` | INFO | 日志级别 |

## 已知问题排查

### 1. 客户端连接失败
- 检查服务端是否运行: `curl http://localhost:6543/health`
- 检查端口是否正确
- 检查防火墙设置

### 2. 没有声音输入
- 检查麦克风权限
- 使用选项 5 选择正确的麦克风
- 确认 sounddevice 已安装

### 3. 识别结果为空
- 检查音频数据是否正确发送
- 检查服务端日志
- 确认模型已正确加载

### 4. 服务端模型加载失败
- 检查 transformers 是否安装
- 检查模型文件是否存在
- 检查 GPU/CUDA 是否可用
