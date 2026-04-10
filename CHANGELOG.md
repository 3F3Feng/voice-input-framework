# Changelog

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
