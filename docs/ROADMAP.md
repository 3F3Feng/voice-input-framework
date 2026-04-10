# Voice Input Framework - 开发路线图

本文档描述 Voice Input Framework 的未来开发计划，目标是将本项目打造成类似 Typeless 输入法的跨平台语音输入解决方案。

## 🎯 愿景

打造一个**开箱即用**的跨平台语音输入工具：
- 无需安装依赖，点开即用
- 支持所有主流桌面和移动平台
- 智能后处理，输出更准确的文字
- 高度可定制的快捷键和界面

---

## 📋 开发阶段

### 阶段一：桌面客户端增强 (v1.1)

**目标**：优化 Windows/macOS/Linux 桌面客户端体验

#### 1.1 精细化快捷键设置

**当前状态**：
- 仅支持组合键，无法区分左右修饰键
- 快捷键配置通过 GUI 文本框输入

**改进目标**：
- [ ] 支持区分左右修饰键：
  - 左 Shift / 右 Shift
  - 左 Alt / 右 Alt (Option on macOS)
  - 左 Control / 右 Control
  - 左 Command / 右 Command (macOS)
- [ ] 支持单键快捷键（如 F13-F24）
- [ ] 快捷键录制功能：按下按键自动识别并填充
- [ ] 快捷键冲突检测和提示
- [ ] 多套快捷键配置方案（快速切换）

**技术方案**：
```python
# 使用 pynput 的 keycode 区分左右修饰键
from pynput import keyboard

# 左右修饰键的 keycode
LEFT_SHIFT = 0x38
RIGHT_SHIFT = 0x3C
LEFT_ALT = 0x3A
RIGHT_ALT = 0x3D
LEFT_CTRL = 0x3B
RIGHT_CTRL = 0x3E
LEFT_CMD = 0x37   # macOS
RIGHT_CMD = 0x36  # macOS
```

**UI 设计**：
```
┌─────────────────────────────────────────┐
│ 快捷键设置                               │
├─────────────────────────────────────────┤
│ 开始录音：                               │
│   ┌─────────────────┐ [录制] [清除]     │
│   │ Right Alt + V   │                    │
│   └─────────────────┘                    │
│                                          │
│ ☑ 区分左右修饰键                         │
│ ☑ 按住录音，松开停止                     │
│                                          │
│ 预设方案：                                │
│ ○ 默认 (Right Alt + V)                   │
│ ○ 游戏模式 (F13)                         │
│ ○ 自定义                                 │
└─────────────────────────────────────────┘
```

#### 1.2 系统托盘支持

**目标**：
- [ ] 最小化到系统托盘
- [ ] 托盘菜单快速操作：
  - 显示/隐藏主窗口
  - 快速切换模型
  - 开始/停止录音
  - 退出程序
- [ ] 托盘图标状态指示：
  - 就绪（灰色）
  - 录音中（红色脉冲）
  - 处理中（旋转动画）
  - 错误（感叹号）

**技术方案**：
- Windows: `pystray` + `inflect` 
- macOS: 原生 `NSStatusItem` (通过 `rumps` 或 `pyobjc`)
- Linux: `pystray` + AppIndicator

```python
# 托盘菜单示例
import pystray
from PIL import Image

def create_tray_icon():
    menu = pystray.Menu(
        pystray.MenuItem("显示窗口", show_window),
        pystray.MenuItem("开始录音", start_recording, enabled=can_record),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("模型", pystray.Menu(
            pystray.MenuItem("Qwen3-ASR", switch_model, checked=lambda: current_model == "qwen_asr"),
            pystray.MenuItem("Whisper", switch_model, checked=lambda: current_model == "whisper"),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )
    return pystray.Icon("voice_input", icon_image, "Voice Input", menu)
```

#### 1.3 界面隐藏与窗口管理

**目标**：
- [ ] 启动时最小化选项
- [ ] 录音时隐藏窗口（仅显示悬浮提示）
- [ ] 记住窗口位置和大小
- [ ] 置顶/非置顶切换
- [ ] 悬浮录音指示器（小型、半透明）

**悬浮指示器设计**：
```
┌────────────┐
│  🔴 录音中  │  ← 半透明、可拖动、自动消失
│   00:03    │
└────────────┘
```

---

### 阶段一点五：系统集成 (v1.1.5)

**目标**：提升桌面端用户体验的收尾功能

#### 1.5.1 启动后托盘弹窗通知

**目标**：程序启动后在托盘显示欢迎通知

**功能**：
- [ ] 启动成功后显示托盘通知："Voice Input Framework 已就绪"
- [ ] 显示当前快捷键信息
- [ ] 可选：显示服务器连接状态
- [ ] 通知可点击打开主窗口

**技术方案**：
```python
# 使用 pystray 或系统通知 API
import pystray
from PIL import Image, ImageDraw

def show_startup_notification():
    icon = pystray.Icon("voice_input")
    icon.notify("Voice Input Framework 已就绪！", "快捷键: Right Alt+V")
```

#### 1.5.2 更新和版本管理

**目标**：内置版本检查和自动更新功能

**功能**：
- [ ] 启动时检查 GitHub 最新版本
- [ ] 托盘菜单显示"检查更新"选项
- [ ] 发现新版本时显示下载链接
- [ ] 支持手动下载安装

**API 设计**：
```python
def check_for_updates():
    """检查最新版本"""
    response = requests.get(
        "https://api.github.com/repos/3F3Feng/voice-input-framework/releases/latest"
    )
    latest_version = response.json()["tag_name"]
    return latest_version
```

#### 1.5.3 开机自启动注册

**目标**：用户可一键启用开机自启动

**功能**：
- [ ] 托盘菜单添加"开机自启动"选项
- [ ] Windows: 注册表 `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`
- [ ] macOS: `~/Library/LaunchAgents/com.voice-input-framework.plist`
- [ ] Linux: `~/.config/autostart/voice-input-framework.desktop`

**技术方案**：
```python
import platform
import os

def enable_auto_start(enable: bool):
    system = platform.system()
    if system == "Windows":
        # 写入注册表
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        if enable:
            winreg.SetValueEx(key, "VoiceInputFramework", 0, winreg.REG_SZ, exe_path)
        else:
            winreg.DeleteValue(key, "VoiceInputFramework")
    elif system == "Darwin":
        # 创建/删除 LaunchAgent plist
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.voice-input-framework.plist")
        # ...
```

---

### 阶段二：智能后处理 (v1.2)

**目标**：使用小型 LLM 对 ASR 输出进行智能规整

#### 2.1 LLM 后处理架构

**流程**：
```
音频 → ASR模型 → 原始文字 → LLM后处理 → 规整文字 → 输入
                              ↑
                        用户可配置规则
```

**后处理功能**：
- [ ] 标点符号自动添加
- [ ] 口语化表达规整（"那个" "就是" "然后" 等填充词移除）
- [ ] 专业术语纠正
- [ ] 格式化（数字、日期、时间、金额）
- [ ] 分段和断句

**模型选择**：
| 模型 | 参数量 | 延迟 | 适用场景 |
|------|--------|------|----------|
| Qwen3-0.6B | 0.6B | <100ms | 实时后处理（推荐） |
| Qwen3-1.7B | 1.7B | ~200ms | 高质量后处理 |
| 本地量化模型 | 1-2B | <50ms | 离线低延迟 |

**配置选项**：
```json
{
  "postprocess": {
    "enabled": true,
    "model": "qwen3-0.6b",
    "rules": {
      "remove_fillers": true,      // 移除填充词
      "add_punctuation": true,      // 添加标点
      "format_numbers": true,       // 格式化数字
      "format_dates": true,         // 格式化日期
      "professional_mode": false    // 专业模式（保守修改）
    },
    "custom_prompt": "请将以下语音识别结果整理为规范的书面语..."
  }
}
```

#### 2.2 提示词模板

**默认模板**：
```
你是一个文字编辑助手。请对以下语音识别结果进行处理：
1. 移除口语填充词（如"那个"、"就是"、"然后"等）
2. 添加适当的标点符号
3. 保持原意不变，不要过度修改

原文：{asr_output}

整理后的文字：
```

**专业模式模板**：
```
请校对以下语音识别结果，仅修正明显的错误，保持原文风格：

原文：{asr_output}

校对后：
```

---

### 阶段三：跨平台客户端 (v2.0)

**目标**：开发真正的跨平台客户端，支持一键安装

#### 3.1 桌面平台支持

| 平台 | 技术方案 | 打包格式 |
|------|----------|----------|
| Windows | PyInstaller / Nuitka | .exe (单文件) |
| macOS | PyInstaller / py2app | .app / .dmg |
| Linux | PyInstaller / AppImage | AppImage / .deb / .rpm |

**统一技术栈**：
- GUI: PyQt6 或 PySide6（比 PySimpleGUI 更现代）
- 打包: GitHub Actions 自动构建
- 更新: 内置自动更新功能

**项目结构**：
```
client/
├── core/                  # 核心逻辑（平台无关）
│   ├── audio_capture.py
│   ├── websocket_client.py
│   ├── hotkey_manager.py
│   └── postprocessor.py
├── platform/              # 平台特定实现
│   ├── windows/
│   │   └── tray.py
│   ├── macos/
│   │   └── tray.py
│   └── linux/
│       └── tray.py
├── ui/                    # 统一 UI
│   ├── main_window.py
│   ├── settings_dialog.py
│   └── floating_indicator.py
└── main.py
```

#### 3.2 移动平台支持

**iOS 客户端**：
- 技术方案：Swift + SwiftUI
- 音频采集：AVAudioEngine
- WebSocket：URLSessionWebSocketTask
- 发布：App Store

**Android 客户端**：
- 技术方案：Kotlin + Jetpack Compose
- 音频采集：AudioRecord
- WebSocket：OkHttp WebSocket
- 发布：Google Play Store

**移动端特有功能**：
- [ ] 与系统键盘集成
- [ ] 后台录音支持
- [ ] 低功耗优化
- [ ] 离线模式（设备端 ASR）

#### 3.3 打包与分发

**GitHub Actions 自动构建**：
```yaml
# .github/workflows/release.yml
name: Build Release

on:
  push:
    tags:
      - 'v*'

jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: |
          pip install pyinstaller
          pyinstaller --onefile --windowed client/main.py
      - name: Upload
        uses: actions/upload-artifact@v4
        with:
          name: windows-build
          path: dist/*.exe

  build-macos:
    runs-on: macos-latest
    # ... 类似配置

  build-linux:
    runs-on: ubuntu-latest
    # ... 类似配置
```

**自动更新机制**：
```python
class Updater:
    def check_update(self):
        response = requests.get("https://api.github.com/repos/3F3Feng/voice-input-framework/releases/latest")
        latest_version = response.json()["tag_name"]
        if self.current_version < latest_version:
            return UpdateInfo(latest_version, download_url)
    
    def download_and_install(self, update_info):
        # 下载更新包
        # 校验签名
        # 静默安装
```

---

### 阶段四：高级功能 (v2.x+)

#### 4.1 多语言界面

**目标**：
- [ ] 中文界面
- [ ] 英文界面
- [ ] 日文界面（可选）

**技术方案**：使用 gettext 或 JSON 语言包

#### 4.2 云端服务

**可选的云端服务**：
- [ ] 云端 ASR 服务（更高的识别准确率）
- [ ] 云端 LLM 后处理（更强大的规整能力）
- [ ] 同步配置和词库

#### 4.3 专业版功能

**可选的付费功能**：
- [ ] 医疗/法律等专业词汇库
- [ ] 团队共享词库
- [ ] API 接口开放
- [ ] 批量文件转写

---

## 🛠️ 技术债务清理

### 当前需要改进的代码

1. **client/gui.py**
   - 重构为模块化结构
   - 分离 UI 和业务逻辑
   - 支持多窗口管理

2. **server/api.py**
   - 添加 API 版本控制
   - 改进错误处理
   - 添加请求日志

3. **文档**
   - API 文档（OpenAPI/Swagger）
   - 用户手册
   - 开发者文档

---

## 📅 里程碑时间线

| 版本 | 功能 | 预计完成 |
|------|------|----------|
| v1.0 | 基础功能（当前） | ✅ 已完成 |
| v1.1 | 精细快捷键 + 托盘 + 界面优化 | Q2 2026 |
| v1.2 | LLM 后处理 | Q3 2026 |
| v2.0 | 跨平台桌面客户端 | Q4 2026 |
| v2.1 | iOS 客户端 | 2027 Q1 |
| v2.2 | Android 客户端 | 2027 Q2 |

---

## 📖 参考

### 竞品分析

**Typeless 输入法**：
- 特点：跨平台、开箱即用、智能后处理
- 优势：用户体验好，无需配置
- 我们可以学习：简洁的 UI、智能规整功能

**Whisper 官方客户端**：
- 特点：高准确率、多语言支持
- 优势：模型质量高
- 我们可以学习：音频处理优化

### 技术参考

- PyInstaller 文档: https://pyinstaller.org/
- PyQt6 文档: https://www.riverbankcomputing.com/static/Docs/PyQt6/
- pystray 文档: https://github.com/bsmithson/pystray
- Qwen3-ASR: https://huggingface.co/Qwen/Qwen3-ASR-1.7B

---

## 🤝 贡献指南

欢迎贡献代码！请遵循以下流程：

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

### 代码规范

- Python: PEP 8
- 提交信息: Conventional Commits
- 文档: 中文 + 英文双语

---

*最后更新: 2026-04-09*
