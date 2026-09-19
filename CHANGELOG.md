# Changelog

## [2.2.0] - 2026-09-19

> 版本号从这一版起**只有一个来源**:`gui/src-tauri/Cargo.toml` 的 `package.version`。
> `tauri.conf.json` 不再写 `version`(Tauri 缺省回落到 Cargo.toml),`gui/package.json`
> 的 `version` 字段也删掉了 —— 那两处从来没人读,却总和真版本号对不上;界面底栏
> 一度显示的还是配置文件的 schema 版本「2.0」。

### Added

- **客户端内管理本地服务**:设置面板里直接启停本地 STT / LLM 服务,不用再开终端。
  保留「远程连接」模式,行为与改动前逐字相同。
  进程归属分三档:本应用启动的、外部但确认是本项目的(按模块名 + 工作目录认)、
  认不出来源的。**只有前两档允许从界面停止** —— 认不出来源的进程本应用只连接、绝不停它。
- **LLM 服务跟着「LLM 后处理」开关起停**:开关关着就不占着几个 G 的内存。
  开关的权威在 STT 服务的 `/llm/enabled`;客户端配置里的 `llm.enabled` 只是一份
  缓存,用来回答「启动那一刻 STT 还没起来、问不到」的那个问题,STT 健康后立刻对账。
- **每次构建打唯一 ID**:版本号只在发版时才动,两次发版之间可能有几十次本地构建。
  构建 ID 出现在启动日志、设置面板的「关于」和底栏(前 8 位,悬停看全串)。
  `scripts/build-macos.sh` 每次现生成一个,并在构建后校验它真的编进了产物。

### Changed

- **设置面板分页**:十个分段堆在 400×500 的窗口里一路往下滚,找一个开关要滚三屏。
  改为「服务 / 常规 / 权限(仅 macOS)/ 日志 / 关于」五页。
- **两个日志框合并成一个**:客户端自己的日志原本在面板最底下,STT / LLM 子进程的
  输出在「服务器」一段里,并排摆着没人分得清哪个是哪个。现在共用一个框,按来源切换。
  不做时间线交织 —— 三路日志的时间戳格式各不相同,按猜测排到一起只会造出一条
  看着可信、其实是编的时间线。

### Fixed

- **关闭按钮直接退掉整个应用**:`CloseRequested` 里先 `hide()` 了窗口却没有
  `prevent_close()`,窗口照样被真正关掉,主窗口一关应用就跟着退了 —— 用户点 ✕
  只想收起界面,结果全局快捷键一起没了。现在退出只有托盘菜单一条路。
- **打开 LLM 后处理后模型下拉框是空的**:LLM 模型列表只在连接时跟着 STT 的列表拉过
  一次,而后处理默认关着时 LLM 服务没在跑,那一次必然失败,之后再没人补拉。
  现在开关打开成功后、以及服务器面板看到 LLM 进入「运行中」而列表还空着时各补一次。
- **从界面启动服务后卡在「未连接」**:本地模式下客户端该连 `127.0.0.1:<本地端口>`,
  前端却拿远程模式那对字段自己拼地址,于是服务在本地跑着、客户端一直去敲用户填的远程地址。
- **accessory 应用启动时主窗口不前置**,表现为「没勾『启动时最小化』却自己最小化了」。
- **快捷键路径静默失败**:快捷键触发的转录出错时不说话,用户只看到什么都没发生。
- **转录结果被客户端篡改**:后处理里会去掉引号、撇号并去重行,把正文改坏。

## [2.1.0] - 2026-09-18

### Changed

- **macOS 以菜单栏应用（accessory）身份运行**：不再占用 Dock 图标，主窗口从托盘菜单打开。
  这不只是外观取舍 —— 常规（regular）应用的窗口在 macOS 上**无法加入其它应用的全屏 Space**，
  悬浮胶囊因此在全屏应用里完全不可见。实测即使 `collectionBehavior` 设为
  `CanJoinAllSpaces|FullScreenAuxiliary` 且层级提到 `NSStatusWindowLevel`，
  `isOnActiveSpace` 在全屏场景下仍为 `false`；accessory 应用不受此限制。

### Added

- **macOS 权限申请**：设置面板内可查看/申请麦克风、输入监控、辅助功能三项权限，
  并在缺失时给出跳转系统设置的入口。详见 `docs/macos-permissions.md`。
- **本地签名构建脚本** `scripts/build-macos.sh`：用本机证书签名、校验产物、安装到
  `/Applications`。详见 `docs/macos-signed-build.md`。

### Fixed

- **LLM 后处理返回整段推理过程**：推理模型（如 Qwen3.5-4B-OptiQ）会把思考过程当正文输出，
  而 `max_tokens=256` 全部消耗在思考上，根本走不到答案 —— 结果是一大段英文分析被
  敲进用户文档，接口却返回 `success=true`。改为在 `apply_chat_template` 传
  `enable_thinking=False`，延迟同时从 ~3900ms 降到 ~600ms。
- **麦克风在 hardened runtime 下被直接拒绝**：用真证书签名会自动启用 hardened runtime，
  而包内未声明 `com.apple.security.device.audio-input`，导致系统直接拒绝、不弹窗、
  也不出现在隐私设置列表里。新增 `Entitlements.plist`。
- **悬浮胶囊在 macOS 上不可见**：`screen_center_bottom` 把 `Monitor::size()` 的
  物理像素当逻辑像素用，Retina（scale=2）下算出的 y 坐标落到屏幕之外。
  改用 `work_area()` 并按 `scale_factor` 换算，底边对齐到 Dock 上方。
- **悬浮胶囊周围的黑色方块**：改回透明窗口。此前"透明窗口不渲染"的判断是被上述
  定位缺陷误导的 —— macOS 对完全离屏的窗口会推迟 WebView 渲染。
- **录音时抢走焦点**：移除 `indicator::show()` 中的 `set_focus()` 调用。
  语音输入的前提是把文字打进用户当前的应用，录音一开始就抢焦点会破坏这个前提。
- **托盘图标是绿色方块**：`tray.rs` 取的是 bundle 图标列表首项 `icons/32x32.png`，
  而那是 Tauri 的占位图（`icon.icns` 里一直是正确图标，所以 Finder 显示正常）。
  已从 1024×1024 原始 logo 重新生成全套图标。
- **服务端默认绑定 `0.0.0.0` 且 CORS 全开**：改为默认 `127.0.0.1`（env 可覆盖）、
  CORS 走显式列表，并对上传大小与 `/process` 文本长度施加上限。
- **并发与资源**：LLM 生成加锁串行化（`/process` 运行在线程池，原先并发请求会
  在同一模型实例上互相踩）；切换模型时释放旧模型；STT 的模型拆卸移入 `_load_lock`。
- **GUI 错误信息永远显示 "Unknown error"**：服务端发送的是 `error_message`，
  而 `stt.rs` 读的是 `message`。
- **音频二次重采样**：采集回调推入的已是 16 kHz，但 `stop()` 返回设备原生采样率，
  批处理回退路径据此再次重采样。
- **macOS 快捷键**：重复注册会泄漏旧的 event tap；非 Windows 路径缺少
  `MAX_RECORD_SECS` 安全停止；`<100ms` 的快按会吞掉 Release 事件导致录音卡在开启状态。

### Removed

- `gui/src-tauri/src/text.rs`（从未被 `lib.rs` 声明为模块，不参与编译）。
- `LLMEngine.load()` 中不可达的 `if self._loading:` 等待分支（若可达反而会自死锁）。

## [2.0.10] - 2026-05-30

### Fixed

- **Hotkey release detection after minimize (Windows)**: completely rewrote hotkey
  detection to use `GetAsyncKeyState` polling instead of `rdev` `WH_KEYBOARD_LL`
  hook. The hook stops firing KeyRelease events when the Tauri webview is minimized
  to tray, causing recordings to never stop. Pure Win32 API polling works
  regardless of window state.

## [2.0.1] - 2026-05-26

### Fixed

- **Hotkey debounce**: prevent double-trigger when mouse side button is mapped to modifier key
  - State guard, time debounce (150ms), min press duration (50ms, keep recording)
  - Fixes stuck recording state with mouse button bounce

## [1.1.6] - 2026-05-26

### Fixed

- **Hotkey debounce**: prevent double-trigger when mouse side button is mapped to modifier key
  - State guard: skip PRESS if already recording, skip RELEASE if not recording
  - Time debounce: ignore PRESS events within 150ms of last RELEASE
  - Min duration guard: ignore RELEASE events within 50ms of PRESS (noise spike),
    keeps recording active until the real release
  - Fixes issue where mouse button bounce caused stuck recording state and
    "already recording" popup with persistent floating indicator

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.5] - 2026-04-10

### Added
- **System Integration Features** (v1.1.5 milestone)
  - Startup tray notification - Shows "Ready" notification with hotkey info on launch
  - Update checker - Check for updates via GitHub releases, accessible from tray menu
  - Auto-start registration - One-click enable/disable开机自启动 from tray menu
    - Windows: Registry `HKEY_CURRENT_USER\...\Run`
    - macOS: LaunchAgents plist
    - Linux: `~/.config/autostart/` .desktop file

### Changed
- Updated ROADMAP to mark v1.1.5 features as completed

## [1.1.0] - 2026-04-08

### Added
- GitHub CI for unit tests
- Build configuration for Windows exe

### Fixed
- Build exe dependencies (numpy)
- Ubuntu CI with xvfb system package
- HotkeyVoiceInput import recovery
- Separated Ubuntu/macOS/Windows test workflows

## [1.0.2] - 2026-04-07

### Fixed
- Processing time logging and statistics output

## [1.0.1] - 2026-04-06

### Fixed
- Removed duplicate result processing calls

## [1.0.0] - 2026-04-05

### Added
- Initial release
- Real-time audio capture from microphone
- Streaming ASR with low latency
- Multiple model support:
  - Qwen3-ASR-1.7B (recommended, 52 languages/dialects)
  - Qwen3-ASR-0.6B (faster, real-time scenarios)
  - Whisper-large-v3 (OpenAI classic)
  - Whisper-small (lightweight)
- Client/server architecture for remote deployment
- Cross-platform GUI client (Windows/macOS/Linux)
- WebSocket streaming API
- System tray integration
- Hotkey-based recording control

[1.1.5]: https://github.com/3F3Feng/voice-input-framework/compare/v1.1.0...v1.1.5
[1.1.0]: https://github.com/3F3Feng/voice-input-framework/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/3F3Feng/voice-input-framework/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/3F3Feng/voice-input-framework/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/3F3Feng/voice-input-framework/releases/tag/v1.0.0
