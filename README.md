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
- 🖥️ **跨平台客户端**：Tauri GUI（Windows/macOS/Linux）；旧的 Python 客户端已不推荐使用，见下文

## 📦 支持的模型

### STT 模型

下表的「注册名」就是 `VIF_STT_MODEL`、设置面板下拉框和 `/models/select` 里用的名字，
唯一来源是 `shared/model_registry.py`。

| 注册名 | 说明 | 内存 | 平台 |
|--------|------|------|------|
| `qwen_asr_mlx_native` | Qwen3-ASR-1.7B MLX 8bit，**推荐**，52 种语言/方言 | ~1GB | Apple Silicon |
| `qwen_asr_mlx_native_small` | Qwen3-ASR-0.6B MLX 4bit，更快 | ~0.5GB | Apple Silicon |
| `whisper_mlx` | MLX Whisper Large V3 | ~3GB | Apple Silicon |
| `whisper_mlx_turbo` | MLX Whisper Large V3 Turbo，快速且准确 | ~2GB | Apple Silicon |
| `whisper_mlx_medium` | MLX Whisper Medium | ~1.5GB | Apple Silicon |
| `whisper_mlx_small` | MLX Whisper Small，最快 | ~0.5GB | Apple Silicon |
| `whisper_tiny` | Whisper Tiny（transformers），最快、精度一般，适合低配 / 纯 CPU | ~0.3GB | 全平台 |
| `whisper_base` | Whisper Base（transformers），纯 CPU 上的推荐起点 | ~0.5GB | 全平台 |
| `whisper_small` | Whisper Small（transformers），速度与精度折中 | ~1GB | 全平台 |
| `whisper_medium` | Whisper Medium（transformers），有独显时适用 | ~2.5GB | 全平台 |
| `whisper_turbo` | Whisper Large V3 Turbo（transformers），精度最好，建议配 GPU | ~3GB | 全平台 |
| `whisper_cpp_base` / `whisper_cpp_large` | Whisper V3 via whisper.cpp | 1GB / 3GB | 需自行编译 `~/whisper.cpp` 并把模型放到 `~/.cache/whisper/` |

不指定时服务端**按硬件挑**（`services/device.py` 的 `recommend_stt_model`）：
Apple Silicon 上内存 ≥16GB 用 `qwen_asr_mlx_native`，否则用 `qwen_asr_mlx_native_small`；
有独显按显存从 `whisper_turbo` 往下挑；纯 CPU 按核数和内存在 `whisper_tiny` /
`whisper_base` / `whisper_small` 里取。启动日志里会写明选了哪个、为什么。

### LLM 后处理模型

**LLM 后处理目前只能在 Apple Silicon 上用**（`services/llm_server.py` 只实现了 MLX 后端）。

| 模型 | 内存占用 | 特点 |
|------|----------|------|
| Qwen3.5-4B-OptiQ | ~3GB | **默认**，中文能力强，速度与精度平衡 |
| Qwen3.5-2B-OptiQ | ~2GB | 速度更快 |
| Qwen3.5-4B-MLX | ~4GB | 4B 标准量化 |
| Qwen3-0.6B / Qwen3-1.7B | ~0.5GB / ~1.5GB | 更小更快 |
| Gemma-4-E4B-DECKARD | — | Google 模型，中文较弱 |

## 🚀 快速开始

分两部分：**客户端**（桌面应用）和**服务端**（跑模型的 Python 服务）。
服务端可以和客户端在同一台机器上，也可以放在另一台有显卡的机器上。

### 从 Releases 安装客户端 + 本地建服务端（推荐）

1. 到 [Releases](https://github.com/3F3Feng/voice-input-framework/releases/latest) 下载对应平台的客户端：
   - macOS（Apple Silicon）：`GUI-macOS-<版本>-aarch64.dmg`
   - Windows：`GUI-Windows-<版本>-x64.exe`
   - Linux：`GUI-Linux-<版本>-x64.AppImage` 或 `.deb`

   > macOS 上的发布包没有 Developer ID 签名，浏览器下载后打开可能提示「已损坏」。
   > 文件本身是好的，执行 `xattr -dr com.apple.quarantine "/Applications/Voice Input.app"`
   > 后再打开即可，详见 [docs/macos-signed-build.md](docs/macos-signed-build.md)。

2. 克隆仓库，一键建服务端环境（见下面「服务端」）。

3. 打开客户端 →「设置 → 服务」选「本地管理」，点「自动探测」找到仓库和
   `.venv` 里的解释器，再点「启动」。也可以勾上「随应用启动」。

### 服务端

```bash
git clone https://github.com/3F3Feng/voice-input-framework.git
cd voice-input-framework

# 一键建环境:探测硬件(Apple Silicon / NVIDIA / AMD / Intel / 纯 CPU),
# 挑对应的 PyTorch 后端,用 uv 装进仓库下的 .venv(没有 uv 会先自动装上)
scripts/setup-env.sh
# 连 LLM 后处理的依赖一起装:scripts/setup-env.sh --llm

# 启动 STT 服务 (端口 6544)
uv run python -m services.stt_server

# 启动 LLM 服务 (端口 6545，可选，仅 Apple Silicon)
uv run python -m services.llm_server
```

Windows 上用 PowerShell 版脚本（参数与 bash 版对应：`-Backend cpu|cuda|xpu`、`-Llm`、`-Dev`；
有 NVIDIA 显卡会自动选 CUDA，否则用 CPU）：

```powershell
cd voice-input-framework
powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1
uv run python -m services.stt_server
```

- **LLM 后处理目前只能在 Apple Silicon 上用。** Apple Silicon 上 MLX 相关依赖默认就会装上；
  `--llm` 在其它平台会额外装 llama.cpp 的依赖，但 LLM 服务还只实现了 MLX 后端，装了也跑不起来。
  不开 LLM 后处理不影响语音识别本身。
- 自动探测不准时可以手动指定后端：`scripts/setup-env.sh --backend cpu|cuda|rocm|xpu|mlx`。
- 建完环境后可以在客户端「设置 → 服务」里点「环境体检」：逐项检查 Python 版本、关键依赖能否导入、
  加速后端和 uv，有问题会给出修复命令。
- 需要 Python 3.11 或 3.12（`uv` 会自己准备合适的解释器）。
- **不要再用 `pip install -r requirements-stt.txt`**：那份清单无条件装 mlx，而 Linux 上
  的 mlx wheel 装得上、一 import 就报 `libmlx.so` 找不到，建出来的环境是坏的；
  它也选不了 CUDA / ROCm 版的 PyTorch。依赖以 `pyproject.toml` 为准。

### 从源码运行客户端

```bash
cd gui
npm install
npm run tauri dev
```

### Python 客户端（legacy，不推荐）

`client/` 和 `run_client.py` 是早期的 Python 客户端，**已不再维护新功能**（本地服务管理、
权限引导、悬浮胶囊、自动更新都只在 Tauri 客户端里有）。新用户请直接用上面的 Tauri 客户端。

## 🖥️ Tauri GUI 客户端

跨平台原生桌面客户端，基于 Tauri 2 + Vue 3 + TypeScript。

### 功能

- **按住说话**：支持鼠标按钮和全局快捷键录音（快捷键行为见下方「快捷键」一节）
- **音频上传**：录音结束后将音频发送到服务器转写（分块上传，非真流式推理）
- **LLM 后处理**：可选开启，录音后自动优化识别结果
- **悬浮胶囊**：录音时显示计时器和音量条，处理中显示状态；macOS 上可浮于全屏应用之上
- **系统托盘**：macOS 以菜单栏应用（accessory）身份运行，**不占用 Dock 图标**，主窗口从托盘菜单打开
  （在启动台 / Finder 里再点一次应用也会把窗口叫出来）。托盘菜单里有连接状态、复制最近一条结果、
  设置、检查更新和退出；窗口的关闭按钮只收起界面，退出走托盘菜单或「设置 → 关于 → 退出应用」。
  托盘建不成时（如 Linux 缺 AppIndicator）不会隐藏主窗口，关闭按钮改为最小化
- **单实例**：重复打开不会起第二个进程（那样会有两套快捷键监听、一句话录两遍），而是把已有窗口调出来
- **本地服务管理**：设置面板内启停本地 STT / LLM 服务，也可切到「远程连接」只连不管
- **自动更新**：检测 GitHub Releases 新版本，一键更新
- **日志面板**：客户端日志与 STT / LLM 子进程输出共用一个面板，按来源切换。客户端日志包括启动阶段的诊断
  （快捷键监听失败、配置文件损坏、自动启动失败等），同时写入应用日志目录（macOS 是
  `~/Library/Logs/com.voiceinput.app/voice-input.log`，超过 1 MB 轮转一份）；日志页可一键打开日志目录，
  或「复制诊断信息」（版本、系统、最近的日志）用于反馈问题
- **音频设备选择**：支持选择系统中任意输入设备
- **macOS 权限**：设置面板内查看/申请麦克风、输入监控、辅助功能三项权限，详见 `docs/macos-permissions.md`

设置面板按「服务 / 常规 / 权限 / 日志 / 关于」分页。「关于」里能看到版本号、
构建 ID 和构建时间 —— **版本号唯一的来源是 `gui/src-tauri/Cargo.toml`**
（`tauri.conf.json` 不再写 `version`，Tauri 缺省回落到它），而构建 ID 是每次
构建现生成的 UUID，用来分辨本地反复构建出来的产物（版本号在两次发版之间是不动的）。

### 快捷键

默认 `left_ctrl+left_alt`，按住说话、松开转写；「设置 → 常规 → 录音方式」可改成按一下开始、
再按一下结束。录音中按 Esc 放弃这一段。

可用的键：Ctrl / Alt / Shift / Cmd(Win) 修饰键，字母、数字、空格、回车、Tab、Esc、
F1–F20（Linux 只到 F12）；macOS 上还能单独用 Fn(🌐) 键（设置里点「用 Fn 键」，并把系统设置
里「按下 🌐 键时」改成「不执行任何操作」）。⌘ / Win 加字母数字是系统快捷键，录制时会拒绝。

**不写左右就两边都认。** 写 `ctrl+alt` 时左右两侧都能触发；只有明确写
`left_ctrl` 才只认左边。想让录制出来的 `left_ctrl+left_alt` 也左右通用，
在「设置 → 常规」里关掉「区分左右修饰键」即可。

平台差异（都是系统限制，不是实现偷懒）：

| 平台 | 实现 | 需要注意的 |
|------|------|-----------|
| Windows | `GetAsyncKeyState` 轮询 | 不受窗口最小化影响 |
| macOS | CGEventTap | 需要「输入监控」权限；**Caps Lock 是按一下开始、再按一下结束**，见下 |
| Linux | rdev（X11） | **Wayland 会话下不工作**，见下 |

- **macOS 的 Caps Lock**：系统只暴露「灯亮着没有」这一个状态，没有物理按下/抬起
  事件，所以用它当快捷键只能是切换式（按一下开始录，再按一下停），不像别的键
  那样按住说话。Windows 上 Caps Lock 是正常的按住说话。
- **Linux 的 Wayland**：全局按键监听走的是 X11，而 Wayland 按设计就不让普通客户端
  窥探别的窗口的输入。在 Wayland 会话里快捷键不会工作，应用启动时会在日志面板里
  明确说明。解决办法是改用 Xorg 会话登录，或者直接在界面上按住录音按钮说话。
  另外某些发行版需要当前用户在 `input` 组里：
  `sudo usermod -aG input $USER`，然后重新登录。

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
│  hotkey.rs (全局快捷键；macOS 用 CGEventTap)│
│  permissions.rs (macOS 权限查询/申请)      │
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

**macOS 推荐走脚本**，它会用本机证书签名、校验产物、并安装到 `/Applications`：

```bash
scripts/build-macos.sh --install
```

签名不是可选项：macOS 的隐私权限（TCC）按**代码签名身份**记录授权，
而不带证书构建时 Tauri 只做 ad-hoc 签名、身份每次构建都变 ——
后果是麦克风、输入监控、辅助功能三项权限**每构建一次就要重新授予一遍**。
脚本还会校验 hardened runtime 所需的 entitlement 确实进了产物（缺了麦克风会静默失效）。

完整说明见 [docs/macos-signed-build.md](docs/macos-signed-build.md)。

其它平台（或只想编译不签名）：

```bash
cd gui
npm install
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
| `VIF_STT_HOST` | 127.0.0.1 | STT 服务监听地址 |
| `VIF_STT_MODEL` | 按硬件推荐 | 启动时加载的 STT 模型(注册名见上方模型表)。不设时按硬件挑:Apple Silicon 按内存在 `qwen_asr_mlx_native` / `qwen_asr_mlx_native_small` 之间选,其它平台按显存 / 核数 / 内存在 Whisper 系列里选;探测失败时退回 `qwen_asr_mlx_native_small`(Apple Silicon)或 `whisper_base` |
| `VIF_LLM_PORT` | 6545 | LLM 服务端口 |
| `VIF_LLM_HOST` | 127.0.0.1 | LLM 服务监听地址;在 STT 服务里是转发目标地址 |
| `VIF_LLM_ENABLED` | true | 是否启用 LLM 后处理 |
| `VIF_LLM_MODEL` | Qwen3.5-4B-OptiQ | 默认 LLM 模型 |
| `VIF_API_TOKEN` | 未设置 | 访问令牌。设了之后除 `/health` 外的请求都要带 `Authorization: Bearer <令牌>`(WebSocket 也可用 `?token=`);客户端在「远程连接」里填。**暴露到局域网时务必设置** |
| `VIF_CORS_ORIGINS` | 本地 GUI 的几个来源 | 允许的跨域来源,逗号分隔;见 `shared/constants.py` 的 `DEFAULT_CORS_ORIGINS` |
| `VIF_REQUEST_TIMEOUT` | 300.0 | 请求超时(秒) |
| `VIF_LOG_LEVEL` | INFO | 日志级别 |

> **两个服务默认只绑回环地址 `127.0.0.1`,不是 `0.0.0.0`。** 它们都没有鉴权,
> 默认就不该暴露到局域网。所以**要从别的机器连过来,必须显式设置**
> `VIF_STT_HOST=0.0.0.0`(LLM 服务同理用 `VIF_LLM_HOST`),并且相应地把
> `VIF_CORS_ORIGINS` 设成客户端的来源 —— 不设的话请求会被 CORS 挡掉。
> 暴露到局域网时请同时设置 `VIF_API_TOKEN`(STT 和 LLM 两个服务用同一个值),
> 并在客户端「远程连接」里填上同一个令牌。
>
> 完整模型元数据见 `shared/model_registry.py`(单一来源)。
>
> **注意**:客户端与服务端默认使用 `127.0.0.1` 而非 `localhost`——Windows 上
> `localhost` 会优先解析到 IPv6(`::1`),每次连接先尝试 IPv6 超时再回落 IPv4,
> 造成数秒延迟。如确需 IPv6 连接,可用环境变量/配置文件显式指定 host。

### 客户端配置

Tauri 客户端的配置是应用数据目录下的 `config.json`，一般不需要手改，设置面板里都能改：
- macOS：`~/Library/Application Support/com.voiceinput.app/config.json`
- Windows：`%APPDATA%\com.voiceinput.app\config.json`
- Linux：`~/.local/share/com.voiceinput.app/config.json`

内容包括：服务模式（本地管理 / 远程）与地址端口、快捷键、识别语言、麦克风、
LLM 开关、启动选项。首次启动时会把旧 Python 客户端的 `~/.voice_input_config.json` 迁移过来。

## 🧪 测试

### 1. 单测 + 端点契约(无需模型,CI 运行)

```bash
uv run --with pytest --with pytest-asyncio python -m pytest -m "not integration" -q
# 193 passed / 7 skipped / 31 deselected(含端点契约测试 tests/test_contract.py)
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
cd gui/stt-logic-tests && cargo test          # 18 passed

# 完整 Tauri crate(Linux 需 webkit2gtk/gtk/alsa/xdo dev 包)
cd gui/src-tauri && cargo check && cargo test
```

Rust 客户端人工验证清单见 `docs/rust-client-verification.md`。

## 📄 许可证

MIT License
