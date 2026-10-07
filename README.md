# Voice Input Framework

[English](README.en.md) | 中文

按住快捷键说话，松开后文字出现在光标处。语音识别和 LLM 整理都跑在你自己的电脑上。

## ✨ 特性

- 🎤 **按住说话，松手出字**：全局快捷键（默认 `Ctrl+Alt`），结果直接输入到当前光标处。录音期间就分段转写，长录音松手后只等最后一段。
- 🤖 **本地模型，按硬件自动选**：Apple 芯片走 MLX，NVIDIA 走 CUDA，AMD / Intel Arc 走 Vulkan，没有显卡也能跑。识别默认用 Qwen3-ASR（52 种语言和方言，中文和中英混说明显好于 Whisper）。
- 🧠 **LLM 后处理**（可选）：去掉口头禅和改口、加标点、把口述的列举整理成列表。提示词可以改，带几个预设。
- 🧰 **不用碰命令行**：客户端里一键下载服务端代码、建 Python 环境，启停和更新服务也在界面上。
- 📝 **词表**：热词，以及「识别成 A 就改成 B」的替换。
- 📱 **手机输入法**（初版）：Android 输入法和 iOS 键盘，连你电脑上的服务，见 [mobile/README.md](mobile/README.md)。
- 🔒 **数据不出你的设备**：没有云端后端，没有遥测，见下文「隐私」。

客户端支持 macOS（Apple 芯片）、Windows、Linux，界面有中文和英文。

## 📸 界面

**桌面客户端**（窗口默认 400×500；截图里的地址、令牌和识别记录都是示例数据）：

| 主界面 | 设置 → 服务 |
|:---:|:---:|
| <img src="docs/images/desktop-main.jpg" width="340" alt="桌面客户端:主界面"> | <img src="docs/images/desktop-settings-service.jpg" width="340" alt="桌面客户端:设置 → 服务"> |
| **设置 → 常规** | **手机直连（局域网）** |
| <img src="docs/images/desktop-settings-general.jpg" width="340" alt="桌面客户端:设置 → 常规"> | <img src="docs/images/desktop-lan-share.jpg" width="340" alt="桌面客户端:手机直连"> |
| **配对手机** | |
| <img src="docs/images/desktop-pair-phone.jpg" width="340" alt="桌面客户端:配对手机二维码"> | |

**iOS 键盘**（顶部选识别语言，点一下说话、再点一下结束）：

| 待机 | 录音中 |
|:---:|:---:|
| <img src="docs/images/ios-keyboard-idle.jpg" width="300" alt="iOS 键盘:待机"> | <img src="docs/images/ios-keyboard-recording.jpg" width="300" alt="iOS 键盘:录音中"> |

## 🚀 快速开始

分两部分：**客户端**（桌面应用）和**服务端**（跑模型的 Python 服务）。客户端能替你把服务端装好。

1. **下载客户端**：到 [Releases](https://github.com/3F3Feng/voice-input-framework/releases/latest) 下载对应平台的安装包。
   - macOS（Apple 芯片）：`GUI-macOS-<版本>-aarch64.dmg`
   - Windows：`GUI-Windows-<版本>-x64.exe`
   - Linux：`GUI-Linux-<版本>-x64.AppImage` 或 `.deb`
2. **跟着首次启动的向导走**：选「在本机跑模型」，点「下载并安装」。它会把服务端代码克隆到
   `~/voice-input-framework`，再装好 Python 环境和依赖（几百 MB 到几 GB，要几分钟到十几分钟）。
   唯一的前提是装了 git；没有的话向导会给出这个系统的安装命令。
3. **点「启动服务」**。第一次要下载模型（几个 GB，看硬件），界面上有进度。国内网络可以把
   「模型下载源」换成 hf-mirror.com。
4. **按住 `Ctrl+Alt` 说话**，松开后文字输入到光标处。

> **macOS**：发布包没有 Developer ID 签名和公证，浏览器下载后打开可能提示「已损坏」。文件本身是好的，
> 执行 `xattr -dr com.apple.quarantine "/Applications/Voice Input.app"` 后再打开，详见
> [docs/macos-signed-build.md](docs/macos-signed-build.md)。向导会带你授予麦克风、输入监控、辅助功能三项权限。
>
> **Windows**：没有代码签名，会弹 SmartScreen「已保护你的电脑」：点「更多信息」→「仍要运行」。

之后有新版本时，客户端会提示更新自己；服务端在「设置 → 服务 → 更新服务」里更新。

### 自己在终端里建服务端

想把服务端放在另一台机器上，或者不想用向导：

```bash
git clone https://github.com/3F3Feng/voice-input-framework.git
cd voice-input-framework
scripts/setup-env.sh                   # Windows: powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1
uv run python -m services.stt_server   # 语音识别,端口 6544
uv run python -m services.llm_server   # LLM 后处理,端口 6545(可选)
```

- `setup-env` 会探测硬件、挑对应的 PyTorch 和 llama.cpp 版本，用 uv 装进仓库下的 `.venv`
  （没有 uv 会先装上，Python 3.11 / 3.12 由 uv 准备）。探测不准时用 `--backend`、`--llm-backend`
  手动指定，`--no-llm` 不装 llama.cpp，详见 [docs/models.md](docs/models.md#llamacpp-装哪个版本)。
- 客户端连本机的服务：「设置 → 服务」选「本地管理」→「自动探测」→「启动」。
- 客户端连另一台机器：服务端要设 `VIF_STT_HOST=0.0.0.0` 和 `VIF_API_TOKEN`，客户端选「远程连接」
  填地址和令牌，见 [docs/configuration.md](docs/configuration.md)。
- 不要用 `pip install -r requirements-stt.txt`：那份清单在 Linux 上会建出坏的环境，也选不了显卡版的
  PyTorch。依赖以 `pyproject.toml` 为准。

## 📦 这台机器会用哪套模型

不指定模型时，识别模型和后处理模型按下面这张表成套地选：两个加起来放得进显存，还给桌面留一截。
「设置 → 服务 → STT 模型」下面会写明这台机器被分到了哪一套、为什么。

| 系统 | 硬件 | 档位 | 语音识别 | LLM 后处理 | 运行环境 |
|------|------|------|----------|------------|----------|
| macOS | Apple 芯片 | 内存 ≥ 16 GB | Qwen3-ASR-1.7B（MLX 8bit） | Gemma-4-E4B（MLX） | MLX / Metal |
| macOS | Apple 芯片 | 内存 < 16 GB | Qwen3-ASR-0.6B（MLX 4bit） | Gemma-4-E2B（MLX） | MLX / Metal |
| Windows / Linux | NVIDIA | 显存 ≥ 11 GB | Qwen3-ASR-1.7B 量化版 | Gemma-4-E4B（显卡） | llama.cpp CUDA |
| Windows / Linux | NVIDIA | 显存 5.5–11 GB | Qwen3-ASR-0.6B 量化版 | Gemma-4-E2B（显卡） | llama.cpp CUDA |
| Windows / Linux | NVIDIA | 显存 2.5–5.5 GB | Qwen3-ASR-0.6B 量化版 | Gemma-4-E2B（CPU） | llama.cpp CUDA |
| Windows / Linux | AMD / Intel Arc | 同上三档，按显存 | 同上 | 同上 | llama.cpp Vulkan |
| 任何系统 | 没有独立显卡 | ≥ 6 线程且内存 ≥ 8 GB | Qwen3-ASR-0.6B 量化版 | Gemma-4-E2B（CPU） | llama.cpp CPU |
| 任何系统 | 没有独立显卡 | 更小的机器 | Whisper Base / Tiny | Gemma-4-E2B（CPU） | PyTorch CPU + llama.cpp CPU |

- 后处理跑在 CPU 上时一句话要等几秒，所以只有 ≥ 12 线程且内存 ≥ 16 GB 的机器默认开着，其余默认关（可以自己打开）。
- 模型都可以在设置里手动换，换过的会被记住，优先于这张表。
- Apple 芯片两档和 NVIDIA 上的安装是实测过的；显存门槛是按模型大小算的，AMD / Intel Arc 的真卡还没测过。

全部可选的模型、选型依据、llama.cpp 各版本的区别，见 [docs/models.md](docs/models.md)。

## ⌨️ 快捷键

默认 `left_ctrl+left_alt`，按住说话、松开转写。「设置 → 常规 → 录音方式」可以改成按一下开始、
再按一下结束。录音中按 Esc 放弃这一段。

- **可用的键**：Ctrl / Alt / Shift / Cmd（Win）修饰键，字母、数字、空格、回车、Tab、Esc、F1–F20
  （Linux 只到 F12）。⌘ / Win 加字母数字是系统快捷键，录制时会拒绝。
- **左右修饰键**：写 `ctrl+alt` 时两侧都能触发，写 `left_ctrl` 才只认左边。「设置 → 常规」里关掉
  「区分左右修饰键」可以让录制出来的快捷键左右通用。
- **macOS 的 Fn（🌐）键**可以单独当快捷键：设置里点「用 Fn 键」，并把系统设置里「按下 🌐 键时」
  改成「不执行任何操作」。

平台差异（都是系统限制）：

| 平台 | 需要注意的 |
|------|-----------|
| macOS | 需要「输入监控」权限。Caps Lock 只能是切换式（按一下开始、再按一下结束）：系统只暴露灯亮没亮，没有按下和抬起 |
| Windows | 没有特别限制 |
| Linux | 只支持 X11。Wayland 会话下全局快捷键不工作（应用会在日志里说明）：改用 Xorg 登录，或者在界面上按住录音按钮。某些发行版要把用户加进 `input` 组 |

## 🖥️ 客户端的其它功能

- **输入方式**：粘贴（推荐）、模拟打字，或者只复制到剪贴板。
- **悬浮胶囊**：录音时显示计时和音量，处理中显示状态；macOS 上能浮在全屏应用上面。
- **托盘 / 菜单栏**：macOS 上是菜单栏应用，不占 Dock；关闭窗口只是收起，退出走托盘菜单。
  Linux 上托盘建不成时关闭按钮改为最小化。
- **识别历史**：可以关掉。
- **本地服务管理**：启停 STT / LLM 服务、环境体检、更新服务、切换模型和模型下载源。
- **日志与诊断**：客户端和两个服务的输出在同一个面板里；「复制诊断信息」用于反馈问题。
- **macOS 权限**：设置里查看和申请麦克风、输入监控、辅助功能，见 [docs/macos-permissions.md](docs/macos-permissions.md)。

## 📱 手机输入法（Android / iOS）

在手机上切到「语音输入」键盘，点一下麦克风说话、再点一下结束，识别和整理都在你电脑上的服务里做。

- **连上你的服务**：同一个 Wi-Fi 下，桌面客户端「设置 → 服务 → 手机直连（局域网）」打开开关，
  它会生成访问令牌并显示手机里要填的地址。出门在外推荐用 [Tailscale](https://tailscale.com/)，
  「配对手机」能显示二维码，手机扫一下就填好。
- **Android**：到 [Releases](https://github.com/3F3Feng/voice-input-framework/releases) 下载 `mobile-v*` 版本里的 APK。
- **iOS**：没有免费的公开分发渠道，要自己从源码构建，用自己的（免费）Apple ID 签名。

细节见 [mobile/README.md](mobile/README.md)。

## 📚 文档

**使用**

| 我想…… | 看这里 |
|---|---|
| 了解全部模型和选型依据 | [docs/models.md](docs/models.md) |
| 改端口、开放到局域网、看环境变量和 HTTP 接口 | [docs/configuration.md](docs/configuration.md) |
| 在手机上用 | [mobile/README.md](mobile/README.md) |
| 弄明白 macOS 上的三项权限为什么要、什么时候要 | [docs/macos-permissions.md](docs/macos-permissions.md) |
| 帮忙测试（Windows / Android，自己搭服务自己连） | [docs/testing.md](docs/testing.md) |
| 看每个版本改了什么 | [CHANGELOG.md](CHANGELOG.md) |

**开发**

| 我想…… | 看这里 |
|---|---|
| 了解各部分怎么配合、为什么这样分 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 自己编译、跑测试、改代码 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| 在 macOS 上构建带签名的客户端，了解发版和签名 | [docs/macos-signed-build.md](docs/macos-signed-build.md) |
| 发版前在真应用里手工验证一遍 | [docs/rust-client-verification.md](docs/rust-client-verification.md) |
| 知道接下来要做什么 | [docs/ROADMAP.md](docs/ROADMAP.md) |
| 看 UX 审查的结论、哪些还没在真机上验证 | [docs/UX_REVIEW.md](docs/UX_REVIEW.md) |
| 翻 2026 年 8 月那次架构审查的记录（历史） | [docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md) |

## 💬 反馈

遇到问题或想提建议，请到 [Issues](https://github.com/3F3Feng/voice-input-framework/issues/new/choose) 新建一个。
桌面客户端的「设置 → 日志 → 复制诊断信息」能一键复制版本、系统和最近的日志。

## 🔒 隐私

- 音频只会发到**你自己运行的**服务，识别和 LLM 整理都在你的电脑上完成。本项目没有云端后端，代码里也没有遥测、统计之类的上传。
- 服务默认只监听本机（`127.0.0.1`）。对局域网开放时**务必设置 `VIF_API_TOKEN`**，并且**不要**把端口映射到公网；出门在外用 Tailscale 这类加密隧道。
- 访问令牌以明文保存在你自己设备的应用私有目录里（手机上其它应用读不到）。
- 对外的网络请求只有两类：模型第一次使用时从 HuggingFace（或你选的镜像）下载；客户端检查 GitHub Releases 上的更新。

## 📄 许可证

MIT License，见 [LICENSE](LICENSE)。
