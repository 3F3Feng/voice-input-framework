# Changelog

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
