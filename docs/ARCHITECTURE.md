# Voice Input Framework - 系统架构与开发文档

## 一、项目概述

Voice Input Framework (VIF) 是一个基于大模型的语音识别框架，支持实时流式语音识别。

**核心特性：**
- 🎤 实时音频采集与处理
- 🚀 流式识别（低延迟）
- 🤖 多模型支持（Whisper、Qwen2-Audio）
- 🔌 客户端/服务端分离架构
- 📦 支持多平台部署

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           客户端层 (Client Layer)                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │   CLI       │  │   GUI       │  │   API Client│  │  Mobile     │   │
│  │  (run_cli) │  │ (run_gui)   │  │ (stt_client)│  │  (TBD)      │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│         │                │                │                 │          │
│         └────────────────┴────────────────┴─────────────────┘          │
│                                    │                                    │
│                            ┌───────▼───────┐                          │
│                            │  AudioCapture │                          │
│                            │  音频采集模块  │                          │
│                            └───────┬───────┘                          │
└────────────────────────────────────┼────────────────────────────────────┘
                                     │ PCM Audio (bytes)
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           网络层 (Network Layer)                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│     WebSocket (ws://host:6543/ws/stream)                               │
│     ─────────────────────────────────────────                           │
│     HTTP REST API (http://host:6543)                                   │
│                                                                         │
│     ┌──────────────────────────────────────────────────────────┐        │
│     │ 支持多客户端并发连接                                      │        │
│     │ - 每个客户端独立 WebSocket 会话                          │        │
│     │ - 服务端维护连接状态                                      │        │
│     │ - 支持跨平台访问（Tailscale/VPN）                        │        │
│     └──────────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           服务端层 (Server Layer)                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │                      FastAPI Application                         │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │   │
│  │  │ /health      │  │ /models      │  │ /ws/stream   │         │   │
│  │  │ GET          │  │ GET/POST     │  │ WebSocket    │         │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘         │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                    │                                    │
│                            ┌───────▼───────┐                          │
│                            │ STTEngineMgr  │                          │
│                            │ 引擎管理器     │                          │
│                            └───────┬───────┘                          │
│                                    │                                    │
│         ┌──────────────────────────┼──────────────────────────┐       │
│         │                          │                          │       │
│         ▼                          ▼                          ▼       │
│  ┌─────────────┐          ┌─────────────┐          ┌─────────────┐   │
│  │  Whisper   │          │ Qwen2Audio  │          │   Future    │   │
│  │  Engine    │          │   Engine    │          │   Models    │   │
│  └─────────────┘          └─────────────┘          └─────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 目录结构

```
voice-input-framework/
├── client/                         # 客户端模块
│   ├── __init__.py
│   ├── audio_capture.py            # 音频采集（sounddevice）
│   ├── audio_processor.py          # 音频处理（降噪、VAD）
│   ├── stt_client.py               # STT 客户端（HTTP/WebSocket）
│   ├── cli.py                      # CLI 界面组件
│   └── gui.py                      # GUI 界面组件
│
├── server/                         # 服务端模块
│   ├── __init__.py
│   ├── api.py                      # FastAPI 应用（主入口）
│   ├── config.py                   # 服务配置
│   ├── stt_engine.py               # STT 引擎管理器
│   └── models/                     # 模型实现
│       ├── __init__.py
│       ├── base.py                 # 引擎基类
│       ├── whisper.py              # Whisper 实现
│       └── qwen_asr.py            # Qwen2Audio 实现
│
├── shared/                         # 共享模块
│   ├── __init__.py
│   ├── protocol.py                 # 通信协议定义
│   └── data_types.py               # 数据类型定义
│
├── deploy/                         # 部署配置
│   ├── daemon.sh                   # 进程管理脚本
│   ├── launchd.plist               # macOS 自启动
│   └── voice-input-framework.service # systemd 服务
│
├── examples/                       # 示例代码
│   ├── streaming_demo.py           # 流式识别演示
│   └── file_transcribe.py          # 文件转写演示
│
├── docs/                           # 文档
│   └── ARCHITECTURE.md             # 本文档
│
├── run_cli.py                      # CLI 客户端入口
├── run_gui.py                      # GUI 客户端入口 (PySide6)
├── run_gui_qt.py                   # GUI 客户端入口 (PyQt6)
├── run_gui_tk.py                   # GUI 客户端入口 (Tkinter)
│
├── pyproject.toml                  # 项目配置
└── requirements.txt                # 依赖列表
```

---

## 三、通信协议

### 3.1 WebSocket 流式识别 (`/ws/stream`)

#### 客户端 → 服务端

| 消息类型 | 字段 | 说明 | 示例 |
|----------|------|------|------|
| `config` | `type`, `language` | 连接初始化配置 | `{"type": "config", "language": "auto"}` |
| `audio` | `type`, `data` (base64) | 音频数据块 | `{"type": "audio", "data": "..."}` |
| `end` | `type` | 结束信号 | `{"type": "end"}` |

#### 服务端 → 客户端

| 消息类型 | 字段 | 说明 | 示例 |
|----------|------|------|------|
| `ready` | `type`, `model` | 连接就绪 | `{"type": "ready", "model": "whisper"}` |
| `result` | `type`, `text`, `confidence`, `language`, `is_final` | 识别结果 | `{"type": "result", "text": "你好", "confidence": 0.95, "language": "zh", "is_final": true}` |
| `done` | `type` | 识别完成 | `{"type": "done"}` |
| `error` | `type`, `error_code`, `error_message` | 错误信息 | `{"type": "error", "error_code": "E5001", "error_message": "..."}` |

### 3.2 REST API

| 端点 | 方法 | 说明 | 参数 |
|------|------|------|------|
| `/health` | GET | 健康检查 | - |
| `/models` | GET | 获取可用模型列表 | - |
| `/models/select` | POST | 切换模型 | `model_name` (form) |
| `/transcribe` | POST | 文件转写 | `file`, `language` (form) |

---

## 四、客户端流程

### 4.1 CLI 客户端流程

```
用户启动
    │
    ▼
显示菜单 ──────────────────────────────────────────────────────────┐
    │                                                             │
    ├─ [1] 开始录音 ───────────────────────────────────────────┐   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    1. 连接 WebSocket 服务器                             │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    2. 发送 {"type": "config", "language": "auto"}      │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    3. 等待 {"type": "ready"}                          │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    4. 显示录音面板（"🔴 正在录音... 按 Enter 停止"）     │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    5. 开始采集麦克风音频                                 │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    6. 采集完成（用户按 Enter）                          │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    7. 发送 {"type": "audio", "data": <full_audio>}     │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    8. 发送 {"type": "end"}                             │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    9. 等待 {"type": "result"}                          │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    10. 等待 {"type": "done"}                           │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    11. 显示识别结果                                     │   │
    │    │                                                    │   │
    │    ▼                                                    │   │
    │    12. 复制到剪贴板（如果配置）                         │   │
    │    │                                                    │   │
    └────┘                                                    │   │
    │                                                             │
    ├─ [2] 设置 ───▶ 设置菜单 ───▶ 保存配置 ───────────────────┤   │
    ├─ [3] 查看配置 ──▶ 显示当前配置 ──────────────────────────┤   │
    ├─ [4] 测试连接 ──▶ WebSocket 连接测试 ────────────────────┤   │
    ├─ [5] 选择麦克风 ─▶ 列出设备 ─▶ 选择 ─────────────────────┤   │
    ├─ [6] 帮助 ────▶ 显示帮助信息 ────────────────────────────┤   │
    └─ [q] 退出 ────▶ 清理并退出 ───────────────────────────────┘   │
```

### 4.2 正确的交互逻辑（关键）

**CLI 客户端的核心交互规则：**

1. **录音开始时：**
   - 显示录音面板（`Panel`）
   - 启动 `sounddevice` 音频采集
   - 等待用户按 Enter

2. **用户按 Enter 时：**
   - 停止音频采集
   - 关闭 `sounddevice` 流
   - 发送音频数据到服务器
   - 发送 `end` 信号
   - 等待识别结果

3. **收到 `done` 时：**
   - 关闭 WebSocket 连接
   - 显示识别结果
   - 返回菜单

**错误做法（之前的实现）：**
```python
# ❌ 错误：在主线程中使用 input() 阻塞
asyncio.run_coroutine_threadsafe(self.record_and_transcribe(), self._loop)
input("\n按 Enter 停止录音...")  # 这里又调用了一次 input！
```

**正确做法：**
```python
# ✅ 正确：录音逻辑内部处理 Enter 按键
async def record_and_transcribe(self):
    # 录音面板
    self.console.print(Panel("[bold red]🔴 正在录音...[/bold red]\n[dim]按 Enter 停止[/dim]"))
    
    # 启动音频采集
    stream = sd.InputStream(...)
    
    # 等待用户按 Enter（不阻塞事件循环）
    await asyncio.get_event_loop().run_in_executor(None, input, "")
    
    # 用户按了 Enter，停止录音
    stream.close()
    
    # 发送音频到服务器
    ...
```

---

## 五、服务端流程

### 5.1 WebSocket 处理流程

```
WebSocket 连接请求
        │
        ▼
    accept()
        │
        ▼
发送 {"type": "ready", "model": "..."}
        │
        ▼
接收 {"type": "config", ...}
        │
        ▼
获取当前 STT 引擎
        │
        ▼
┌───────────────────────────────────────┐
│           主循环：接收音频               │
│                                       │
│  接收 {"type": "audio", "data": ...}  │
│        │                              │
│        ▼                              │
│  累积音频到缓冲区                      │
│        │                              │
│        ▼                              │
│  缓冲区 ≥ 1秒音频？                     │
│        │                              │
│    Yes │ No                          │
│     │   │                            │
│     ▼   │                            │
│  调用 engine.transcribe()             │
│        │                              │
│        ▼                              │
│  发送 {"type": "result", ...}        │
│        │                              │
│        └──────────────────────────────┘
        │
        ▼
接收 {"type": "end"}
        │
        ▼
处理剩余音频
        │
        ▼
发送 {"type": "done"}
        │
        ▼
关闭连接
```

---

## 六、多客户端支持

### 6.1 架构设计

服务端支持**多客户端并发连接**：

```
                    ┌─────────────────┐
                    │   Server        │
                    │  (Port 6543)    │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
   ┌─────────┐         ┌─────────┐         ┌─────────┐
   │ Client1 │         │ Client2 │         │ Client3 │
   │ (Mac)   │         │ (Win)   │         │ (Mobile)│
   │ CLI/GUI │         │ GUI     │         │ TBD     │
   └─────────┘         └─────────┘         └─────────┘
```

**关键特性：**
- 每个客户端独立 WebSocket 会话
- 服务端维护连接状态
- 支持跨网段访问（Tailscale/VPN）
- 连接超时：30 秒（等待 config）

### 6.2 并发处理

```python
# 服务端为每个连接创建独立的协程
@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    # 每个连接独立运行
    await websocket.accept()
    # ... 处理逻辑
```

### 6.3 客户端配置

```python
@dataclass
class STTClientConfig:
    server_url: str = "http://localhost:6543"
    ws_url: Optional[str] = None  # 自动从 server_url 转换
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
```

---

## 七、数据流

### 7.1 音频数据流

```
麦克风 ──[PCM 16kHz 16bit]──▶ AudioCapture ──[bytes]──▶ WebSocket Client
                                                              │
                                                              ▼
                                                        base64 编码
                                                              │
                                                              ▼
WebSocket Server ◀─────── JSON {"type": "audio", "data": "..."} ─────────┐
    │                                                                   │
    ▼                                                                   │
base64 解码                                                                │
    │                                                                   │
    ▼                                                                   │
STT Engine (Whisper/Qwen) ──[text]──▶ StreamResponse ──[JSON]─────────┘
```

### 7.2 消息时序

```
Client                              Server
  │                                    │
  │──── WebSocket Connect ────────────▶│
  │                                    │
  │◀──── {"type": "ready"} ───────────│
  │                                    │
  │──── {"type": "config", ...} ─────▶│
  │                                    │
  │──── {"type": "audio", ...} ──────▶│ (多次)
  │                                    │
  │◀──── {"type": "result", ...} ─────│ (多次)
  │                                    │
  │──── {"type": "end"} ─────────────▶│
  │                                    │
  │◀──── {"type": "result", ...} ─────│ (最终)
  │◀──── {"type": "done"} ─────────────│
  │                                    │
  │──── WebSocket Close ──────────────▶│
```

---

## 八、配置

### 8.1 服务端配置

```python
# server/config.py
@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 6543
    default_model: str = "whisper"
    cors_origins: list = field(default_factory=lambda: ["*"])
    log_level: str = "INFO"
```

**环境变量：**
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_HOST` | 0.0.0.0 | 监听地址 |
| `VIF_PORT` | 6543 | 服务端口 |
| `VIF_DEFAULT_MODEL` | whisper | 默认模型 |
| `VIF_LOG_LEVEL` | INFO | 日志级别 |

### 8.2 客户端配置

```json
// ~/.voice_input_config.json
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

---

## 九、错误处理

### 9.1 错误码

| 错误码 | 说明 | 处理建议 |
|--------|------|----------|
| `E1000` | 未知错误 | 查看日志 |
| `E1001` | 无效请求 | 检查客户端协议 |
| `E2001` | 音频解码错误 | 检查音频格式 |
| `E3001` | 模型未找到 | 检查模型配置 |
| `E3002` | 模型加载失败 | 检查模型文件 |
| `E5001` | 服务端内部错误 | 查看服务端日志 |

### 9.2 客户端重试逻辑

```python
async def connect_with_retry(self, max_retries=3):
    for attempt in range(max_retries):
        try:
            await self.connect()
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(self.retry_delay * (attempt + 1))
            else:
                raise
```

---

## 十、部署

### 10.1 服务端部署

```bash
# macOS
./deploy/daemon.sh start

# Linux (systemd)
sudo cp deploy/voice-input-framework.service /etc/systemd/system/
sudo systemctl enable voice-input-framework
sudo systemctl start voice-input-framework
```

### 10.2 客户端使用

```bash
# CLI
uv run run_cli.py

# GUI (需要 PySide6 或 PyQt6)
uv run run_gui_qt.py
```

### 10.3 远程访问

服务端部署后，通过 Tailscale 或 VPN 实现远程访问：

```python
# 客户端配置远程地址
server_url = "http://100.124.8.85:6543"  # Tailscale IP
```

---

## 十一、已知问题与限制

1. **Windows 音频 DLL**：sounddevice 需要 Visual C++ Redistributable
2. **macOS GUI**：PySide6 可能需要额外配置
3. **Qwen 模型大小**：约 14GB，需要足够内存
4. **WebSocket 超时**：config 消息超时 30 秒

---

## 十二、未来扩展

1. **移动端客户端**：iOS/Android 原生应用
2. **更多模型**：支持更多 ASR 模型
3. **认证授权**：JWT/OAuth2 认证
4. **流量控制**：限制客户端并发数
5. **监控指标**：Prometheus 指标导出
