# Voice Input Framework

基于大模型的语音识别框架，支持实时音频采集与离线转写、LLM 后处理。

## ✨ 特性

- 🎤 **实时音频采集**：支持麦克风实时录音，6种采样格式自动适配
- 🚀 **流式上传**：录音期间音频分块上传，录音结束后统一转写（当前模型非流式推理）
- 🤖 **多模型支持**：
  - **Qwen3-ASR-1.7B** (推荐) - 52种语言/方言，加载快 (~27秒)
  - **Qwen3-ASR-0.6B** - 更快，适合实时场景
  - **Whisper-large-v3** - OpenAI 经典模型
  - **MLX 加速** - Apple Silicon 原生优化
- 🧠 **LLM 后处理** - 自动优化识别结果（去噪、加标点、格式化）
- 🔌 **分离架构**：STT 和 LLM 独立服务，解决 transformers 版本冲突
- 🖥️ **跨平台客户端**：Python + Tauri GUI（Windows/macOS/Linux）

## 📦 支持的模型

### STT 模型

| 模型 | 参数量 | 加载时间 | 特点 |
|------|--------|----------|------|
| qwen_asr | 1.7B | ~27秒 | **推荐**，52种语言/方言 |
| qwen_asr_small | 0.6B | ~10秒 | 更快，实时场景 |
| whisper | 1.5B | ~3秒 | OpenAI 经典 |
| whisper-small | 0.4B | ~1秒 | 轻量级 |

### LLM 后处理模型

| 模型 | 参数量 | 内存占用 | 特点 |
|------|--------|----------|------|
| Qwen3.5-4B-OptiQ | 4B | ~2.5GB | **默认**，中文能力强 |
| Qwen3.5-2B-OptiQ | 2B | ~1.5GB | 速度更快 |
| Gemma-4-E4B-DECKARD | 4B | ~2.5GB | Google 模型 |

## 🚀 快速开始

### 服务端

```bash
git clone https://github.com/3F3Feng/voice-input-framework.git
cd voice-input-framework

# 分离架构:STT 与 LLM 依赖分环境安装(避免 transformers 版本冲突)
pip install -r requirements-stt.txt   # STT 服务依赖
pip install -r requirements-llm.txt   # LLM 服务依赖(Apple Silicon + MLX)

# 启动 STT 服务 (端口 6544)
python -m services.stt_server

# 启动 LLM 服务 (端口 6545，可选)
python -m services.llm_server
```

> 非 Apple Silicon 机器会自动回落 `whisper_turbo`(transformers),无需 MLX 依赖。

### Python 客户端

```bash
python run_client.py
```

### Tauri GUI 客户端

```bash
cd gui
npm install
npm run tauri dev
```

## 🖥️ Tauri GUI 客户端

跨平台原生桌面客户端，基于 Tauri 2 + Vue 3 + TypeScript。

### 功能

- **按住说话**：支持鼠标按钮和全局快捷键录音
- **音频上传**：录音结束后将音频发送到服务器转写（分块上传，非真流式推理）
- **LLM 后处理**：可选开启，录音后自动优化识别结果
- **悬浮胶囊**：录音时显示计时器和音量条，处理中显示状态
- **系统托盘**：支持最小化到托盘，快捷键全局可用
- **自动更新**：检测 GitHub Releases 新版本，一键更新
- **调试日志**：内置日志面板，方便排查问题
- **音频设备选择**：支持选择系统中任意输入设备

### 架构

```
┌─ Tauri GUI (Vue 3) ──────────────────────┐
│  App.vue (UI + 事件监听)                   │
│  indicator.html (悬浮胶囊，独立窗口)        │
└───────────────────────────────────────────┘
           │ invoke / emit
┌─ Rust 后端 ───────────────────────────────┐
│  lib.rs    (命令注册 + 应用生命周期)        │
│  audio.rs  (cpal 音频采集 + 流式通道)       │
│  stt.rs    (WebSocket 流式转写)            │
│  hotkey.rs (rdev 全局快捷键)               │
│  indicator.rs (悬浮胶囊窗口管理)           │
│  update.rs (GitHub Releases 更新检查)      │
│  log.rs    (全局日志，emit 到前端)          │
└───────────────────────────────────────────┘
           │ WebSocket
┌─ STT Server (6544) ──────────────────────┐
│  Qwen3-ASR / Whisper + LLM 后处理         │
└───────────────────────────────────────────┘
```

### 构建

```bash
# 前端
cd gui
npm install
npm run build

# 后端
cd gui/src-tauri
cargo build --release

# 完整打包
npm run tauri build
```

## 📡 API

### STT Service (Port 6544)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/select` | POST | 切换模型 |
| `/models/status/{model}` | GET | 查询模型加载状态 |
| `/transcribe` | POST | 转写音频文件 |
| `/ws/stream` | WebSocket | 流式上传（录音结束后统一转写） |
| `/llm/models` | GET | 获取 LLM 模型列表 |
| `/llm/models/select` | POST | 切换 LLM 模型 |
| `/llm/prompt` | GET/PUT | 获取/保存 LLM 提示词 |
| `/llm/enabled` | GET/PUT | 获取/设置 LLM 开关 |

### LLM Service (Port 6545)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/models` | GET | 获取可用模型列表 |
| `/models/select` | POST | 切换模型 |
| `/process` | POST | 处理文本 |

## 🔧 配置

### 服务端环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_STT_PORT` | 6544 | STT 服务端口 |
| `VIF_STT_HOST` | 0.0.0.0 | STT 服务监听地址 |
| `VIF_STT_MODEL` | 平台默认 | 默认 STT 模型(Apple Silicon 为 qwen_asr_mlx_native,否则 whisper_turbo) |
| `VIF_LLM_PORT` | 6545 | LLM 服务端口 |
| `VIF_LLM_HOST` | localhost | LLM 服务地址(STT 转发用) |
| `VIF_LLM_ENABLED` | true | 是否启用 LLM 后处理 |
| `VIF_LLM_MODEL` | Qwen3.5-4B-OptiQ | 默认 LLM 模型 |
| `VIF_REQUEST_TIMEOUT` | 300.0 | 请求超时(秒) |
| `VIF_LOG_LEVEL` | INFO | 日志级别 |

> 完整模型元数据见 `shared/model_registry.py`(单一来源)。
>
> **注意**:客户端与服务端默认使用 `127.0.0.1` 而非 `localhost`——Windows 上
> `localhost` 会优先解析到 IPv6(`::1`),每次连接先尝试 IPv6 超时再回落 IPv4,
> 造成数秒延迟。如确需 IPv6 连接,可用环境变量/配置文件显式指定 host。

### 客户端配置

客户端配置保存在 `~/.voice_input_config.json`，支持：
- 服务器地址和端口
- 快捷键设置
- LLM 启用/禁用
- UI 设置（透明度、最小化等）
- LLM 直连端口(默认 6545)可用 `VIF_LLM_HOST`/`VIF_LLM_PORT` 环境变量覆盖

## 🧪 测试

### 1. 单测 + 端点契约(无需模型,CI 运行)

```bash
pip install pytest pytest-asyncio fastapi uvicorn pydantic httpx websockets python-multipart numpy
pytest -m "not integration"
# 94 passed / 26 skipped / 25 deselected(含 9 个端点契约测试 tests/test_contract.py)
```

`tests/test_contract.py` 用 TestClient 断言 HTTP/WS 端点契约,与修复前基线等价(见 `docs/ARCHITECTURE_REVIEW.md` §7)。

### 2. 端点等价性对比(基线 vs 当前)

```bash
git worktree add /tmp/vif-baseline <修复前commit>
python scripts/compare_endpoints.py --baseline /tmp/vif-baseline
# 输出差异清单:应全部为已知有意变更(H4/M7),无意外回归
```

### 3. 真实模型集成测试(需模型/GPU 环境)

```bash
bash scripts/run_integration.sh   # 启动 STT+LLM,跑 tests/test_e2e.py + test_api_endpoints.py
```

### 4. Rust 客户端

```bash
# 逻辑测试(任意平台,无需 tauri 系统库)
cd gui/stt-logic-tests && cargo test          # 9 passed

# 完整 Tauri crate(Linux 需 webkit2gtk/gtk/alsa/xdo dev 包)
cd gui/src-tauri && cargo check && cargo test
```

Rust 客户端人工验证清单见 `docs/rust-client-verification.md`。

## 📄 许可证

MIT License
