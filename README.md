# Voice Input Framework

基于大模型的语音识别框架，支持实时流式语音识别。

## 🎯 下载 Windows 客户端 (免安装)

直接从 GitHub Releases 下载 exe 文件：
https://github.com/3F3Feng/voice-input-framework/releases

## ✨ 特性

- 🎤 **实时音频采集**：支持麦克风实时录音
- 🚀 **流式识别**：低延迟响应
- 🤖 **多模型支持**：Whisper、Qwen2-Audio 等
- 🔌 **客户端/服务端分离**：支持远程部署
- 🖥️ **Windows GUI 客户端**：一键安装

## 项目结构

```
voice-input-framework/
├── client/                 # Python 客户端
│   └── gui.py             # GUI 客户端 (PySimpleGUI)
├── server/                 # 服务端
│   ├── api.py             # FastAPI 服务
│   ├── stt_engine.py      # STT 引擎管理
│   └── models/            # 模型实现
├── examples/               # 示例代码
│   ├── simple_client.py   # 简单命令行客户端
│   ├── gui_client.py      # GUI 客户端
│   └── file_transcribe.py # 文件转写
├── deploy/                 # 部署脚本
│   └── daemon.sh          # 进程管理
└── .github/workflows/     # CI/CD
```

## 🚀 快速开始

### 0. 安装依赖

```bash
# 自动安装
python install_dependencies.py

# 或手动安装
pip install -r requirements.txt
```

**Windows 用户**: 可以直接双击 `install.bat` 自动安装所有依赖

### 1. 启动服务端

```bash
# 克隆项目
git clone https://github.com/3F3Feng/voice-input-framework.git
cd voice-input-framework

# 启动服务
python server/api.py
```

### 2. 使用客户端

#### 🎯 最简单的方式（推荐）

```bash
# 直接运行启动脚本
python run_client.py

# 或指定服务器地址
python run_client.py your.server.com 6543
```

#### 📦 下载 Windows exe（无需 Python）

从 GitHub Releases 下载 `VoiceInputFramework-windows-x64.exe`，双击运行。

#### 🐍 Python 运行（需要安装依赖）

```bash
# 安装依赖
pip install PySimpleGUI sounddevice websockets httpx pyautogui pyperclip pynput

# 方式1：运行 GUI 客户端
python -m client.gui

# 方式2：作为库导入
python -c "from client import HotkeyVoiceInput; HotkeyVoiceInput().run()"

# 方式3：运行简单命令行客户端
python examples/simple_client.py
```

#### 🔧 使用环境变量配置

```bash
# 设置服务器地址
export VIF_SERVER_HOST=your.server.com
export VIF_SERVER_PORT=6543

# 或 Windows PowerShell：
$env:VIF_SERVER_HOST = "your.server.com"
$env:VIF_SERVER_PORT = "6543"

# 然后运行
python run_client.py
```

## 📡 服务端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/select` | POST | 切换模型 |
| `/ws/stream` | WebSocket | 流式识别 |
| `/transcribe` | POST | 文件转写 |

### WebSocket 协议

**客户端 → 服务端：**
```json
{"type": "config", "language": "auto"}
{"type": "audio", "data": "<base64>"}
{"type": "end"}
```

**服务端 → 客户端：**
```json
{"type": "ready", "model": "whisper"}
{"type": "result", "text": "识别文字", "confidence": 0.95}
{"type": "done"}
```

## 🔧 配置

### 服务端环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_PORT` | 6543 | 服务端口 |
| `VIF_HOST` | 0.0.0.0 | 监听地址 |
| `VIF_DEFAULT_MODEL` | whisper | 默认模型 |

### 客户端

客户端启动时指定服务器地址：
```bash
python -m client.gui localhost 6543
```

## 🏗️ 从源码构建 exe

```bash
# 安装构建依赖
pip install pyinstaller PySimpleGUI sounddevice websockets pyperclip

# 构建
pyinstaller --name VoiceInputFramework --windowed --onefile client/gui.py

# exe 文件位于 dist/VoiceInputFramework.exe
```

## 📝 开发

### 运行服务端

```bash
./deploy/daemon.sh start
```

### 运行示例

```bash
# 简单客户端
python examples/simple_client.py

# 文件转写
python examples/file_transcribe.py audio.wav --server http://localhost:6543
```

## 📄 许可证

MIT License
