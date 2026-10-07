# Voice Input Framework

English | [中文](README.md)

Hold a hotkey, speak, release — the text appears at your cursor. Speech recognition and LLM clean-up both run on your own computer.

## ✨ Features

- 🎤 **Hold to talk, release to type**: a global hotkey (`Ctrl+Alt` by default) sends the result straight to the cursor. Audio is transcribed in segments while you are still speaking, so after a long recording you only wait for the last segment.
- 🤖 **Local models, picked for your hardware**: MLX on Apple Silicon, CUDA on NVIDIA, Vulkan on AMD / Intel Arc, and it still runs without a GPU. Recognition defaults to Qwen3-ASR (52 languages and dialects; clearly better than Whisper on Chinese and mixed Chinese–English speech).
- 🧠 **LLM post-processing** (optional): removes filler words and self-corrections, adds punctuation, turns dictated lists into real lists. The prompt is editable and comes with a few presets.
- 🧰 **No command line needed**: the client downloads the service code, builds the Python environment, and starts, stops and updates the services for you.
- 📝 **Vocabulary**: hotwords, plus "if you hear A, write B" replacements.
- 📱 **Phone keyboards** (early version): an Android input method and an iOS keyboard that use the services on your computer. See [mobile/README.md](mobile/README.md) (Chinese).
- 🔒 **Your data stays on your devices**: no cloud backend, no telemetry. See [Privacy](#-privacy).

The desktop client runs on macOS (Apple Silicon), Windows and Linux. The interface is available in English and Chinese.

## 📸 Screenshots

**Desktop client** (the window is 400×500 by default; the screenshots show the Chinese interface, and the addresses, tokens and transcripts in them are sample data):

| Main window | Settings → Service |
|:---:|:---:|
| <img src="docs/images/desktop-main.jpg" width="340" alt="Desktop client: main window"> | <img src="docs/images/desktop-settings-service.jpg" width="340" alt="Desktop client: Settings → Service"> |
| **Settings → General** | **Phone access (same network)** |
| <img src="docs/images/desktop-settings-general.jpg" width="340" alt="Desktop client: Settings → General"> | <img src="docs/images/desktop-lan-share.jpg" width="340" alt="Desktop client: phone access"> |
| **Pair a phone** | |
| <img src="docs/images/desktop-pair-phone.jpg" width="340" alt="Desktop client: pairing QR code"> | |

**iOS keyboard** (pick the language at the top; tap once to speak, tap again to finish):

| Idle | Recording |
|:---:|:---:|
| <img src="docs/images/ios-keyboard-idle.jpg" width="300" alt="iOS keyboard: idle"> | <img src="docs/images/ios-keyboard-recording.jpg" width="300" alt="iOS keyboard: recording"> |

## 🚀 Quick start

There are two parts: the **client** (the desktop app) and the **services** (Python processes that run the models). The client can set up the services for you.

1. **Download the client** from [Releases](https://github.com/3F3Feng/voice-input-framework/releases/latest):
   - macOS (Apple Silicon): `GUI-macOS-<version>-aarch64.dmg`
   - Windows: `GUI-Windows-<version>-x64.exe`
   - Linux: `GUI-Linux-<version>-x64.AppImage` or `.deb`
2. **Follow the first-run wizard**: choose "Run models on this computer" and click "Download and install".
   It clones the service code into `~/voice-input-framework` and installs the Python environment and
   dependencies (hundreds of MB to a few GB; a few minutes or more). The only prerequisite is git — if it
   is missing, the wizard shows the install command for your system.
3. **Click "Start service"**. The first start downloads the models (a few GB, depending on your hardware)
   and shows progress. If HuggingFace is slow or blocked where you are, switch "Model download source"
   to the hf-mirror.com mirror.
4. **Hold `Ctrl+Alt` and speak.** Release, and the text is typed at the cursor.

> **macOS**: the release build has no Developer ID signature and is not notarized, so after downloading it
> in a browser macOS may say it is "damaged". The file is fine: run
> `xattr -dr com.apple.quarantine "/Applications/Voice Input.app"` and open it again. Details are in
> [docs/macos-signed-build.md](docs/macos-signed-build.md) (Chinese). The wizard walks you through granting
> the Microphone, Input Monitoring and Accessibility permissions.
>
> **Windows**: the installer is not code-signed, so SmartScreen shows "Windows protected your PC". Click
> "More info" → "Run anyway".

Later on, the client offers to update itself when a new version is out; the services are updated from
Settings → Service → "Update services".

### Setting up the services from a terminal

For running the services on another machine, or if you prefer not to use the wizard:

```bash
git clone https://github.com/3F3Feng/voice-input-framework.git
cd voice-input-framework
scripts/setup-env.sh                   # Windows: powershell -ExecutionPolicy Bypass -File scripts\setup-env.ps1
uv run python -m services.stt_server   # speech recognition, port 6544
uv run python -m services.llm_server   # LLM post-processing, port 6545 (optional)
```

- `setup-env` detects your hardware, picks the matching PyTorch and llama.cpp builds, and installs
  everything into `.venv` in the repository using uv (it installs uv if needed; uv provides Python
  3.11 / 3.12). If detection gets it wrong, use `--backend` and `--llm-backend`; `--no-llm` skips llama.cpp.
  See [docs/models.md](docs/models.md#llamacpp-装哪个版本) (Chinese).
- Client and services on the same machine: Settings → Service → "Run locally" → "Detect" → "Start".
- Client on a different machine: set `VIF_STT_HOST=0.0.0.0` and `VIF_API_TOKEN` on the server, then choose
  "Remote server" in the client and enter the address and token. See
  [docs/configuration.md](docs/configuration.md) (Chinese).
- Do not use `pip install -r requirements-stt.txt`: on Linux it produces a broken environment, and it
  cannot select a GPU build of PyTorch. `pyproject.toml` is the source of truth for dependencies.

## 📦 Which models your machine gets

Unless you choose otherwise, the recognition model and the post-processing model are picked as a matched
pair from the table below, so that both fit in GPU memory with room left for the desktop. Settings →
Service shows which tier your machine landed in, and why, under "STT model".

| OS | Hardware | Tier | Speech recognition | LLM post-processing | Runtime |
|----|----------|------|--------------------|---------------------|---------|
| macOS | Apple Silicon | ≥ 16 GB RAM | Qwen3-ASR-1.7B (MLX 8-bit) | Gemma-4-E4B (MLX) | MLX / Metal |
| macOS | Apple Silicon | < 16 GB RAM | Qwen3-ASR-0.6B (MLX 4-bit) | Gemma-4-E2B (MLX) | MLX / Metal |
| Windows / Linux | NVIDIA | ≥ 11 GB VRAM | Qwen3-ASR-1.7B, quantized | Gemma-4-E4B (GPU) | llama.cpp CUDA |
| Windows / Linux | NVIDIA | 5.5–11 GB VRAM | Qwen3-ASR-0.6B, quantized | Gemma-4-E2B (GPU) | llama.cpp CUDA |
| Windows / Linux | NVIDIA | 2.5–5.5 GB VRAM | Qwen3-ASR-0.6B, quantized | Gemma-4-E2B (CPU) | llama.cpp CUDA |
| Windows / Linux | AMD / Intel Arc | same three tiers, by VRAM | same | same | llama.cpp Vulkan |
| Any | No discrete GPU | ≥ 6 threads and ≥ 8 GB RAM | Qwen3-ASR-0.6B, quantized | Gemma-4-E2B (CPU) | llama.cpp CPU |
| Any | No discrete GPU | smaller machines | Whisper Base / Tiny | Gemma-4-E2B (CPU) | PyTorch CPU + llama.cpp CPU |

- On the CPU, post-processing takes a few seconds per sentence, so it is on by default only on machines
  with ≥ 12 threads and ≥ 16 GB RAM, and off elsewhere (you can turn it on yourself).
- Every model can be changed in Settings. A manual choice is remembered and takes priority over this table.
- The two Apple Silicon tiers and installation on NVIDIA have been tested on real hardware. The VRAM
  thresholds are derived from model sizes, and real AMD / Intel Arc cards have not been tested yet.

The full model list, how the defaults were chosen, and the differences between the llama.cpp builds are in
[docs/models.md](docs/models.md) (Chinese).

## ⌨️ Hotkey

The default is `left_ctrl+left_alt`: hold to talk, release to transcribe. Settings → General →
"Recording mode" switches to press-to-start, press-again-to-stop. Press Esc while recording to discard it.

- **Usable keys**: the Ctrl / Alt / Shift / Cmd (Win) modifiers, letters, digits, Space, Enter, Tab, Esc and
  F1–F20 (F1–F12 on Linux). ⌘ / Win plus a letter or digit is a system shortcut and is rejected when recording a hotkey.
- **Left and right modifiers**: `ctrl+alt` matches either side; only `left_ctrl` is limited to the left key.
  Turn off "Distinguish left/right modifiers" in Settings → General to make a recorded hotkey work on both sides.
- **The Fn (🌐) key on macOS** can be used on its own: choose the Fn option in Settings, and set
  "Press 🌐 key to" to "Do Nothing" in System Settings.

Platform differences (all of them are OS limitations):

| Platform | What to know |
|----------|--------------|
| macOS | Needs the Input Monitoring permission. Caps Lock can only work as a toggle (press to start, press again to stop): the system exposes whether the light is on, not key-down and key-up |
| Windows | Nothing special |
| Linux | X11 only. Global hotkeys do not work in a Wayland session (the app says so in its log): log in with Xorg, or hold the record button in the window. Some distributions need your user in the `input` group |

## 🖥️ What else the client does

- **Input method**: paste (recommended), simulated typing, or copy to the clipboard only.
- **Floating indicator**: shows a timer and input level while recording and the status while processing; on macOS it can float above full-screen apps.
- **Tray / menu bar**: on macOS it is a menu-bar app with no Dock icon. Closing the window only hides it; quit from the tray menu. On Linux, if the tray cannot be created, the close button minimizes instead.
- **Transcription history**, which can be turned off.
- **Local service management**: start and stop the STT / LLM services, check the environment, update the services, switch models and the model download source.
- **Logs and diagnostics**: output from the client and both services in one panel; "Copy diagnostics" is meant for bug reports.
- **macOS permissions**: view and request Microphone, Input Monitoring and Accessibility in Settings. See [docs/macos-permissions.md](docs/macos-permissions.md) (Chinese).

## 📱 Phone keyboards (Android / iOS)

Switch to the Voice Input keyboard on your phone, tap the microphone to speak, and tap again to finish.
Recognition and clean-up happen in the services on your computer.

- **Connecting to your services**: on the same Wi-Fi, turn on Settings → Service → "Phone access (same
  network)" in the desktop client. It generates an access token and shows the address to enter on the phone.
  Away from home, [Tailscale](https://tailscale.com/) is recommended; "Pair a phone" shows a QR code that
  fills in the address when scanned.
- **Android**: download the APK from a `mobile-v*` release on the
  [Releases](https://github.com/3F3Feng/voice-input-framework/releases) page.
- **iOS**: there is no free public distribution channel, so you build it from source and sign it with your
  own (free) Apple ID.

Details are in [mobile/README.md](mobile/README.md) (Chinese).

## 📚 Documentation

The detailed documents are currently in Chinese only.

| I want to… | Read |
|---|---|
| See every model and how the defaults were chosen | [docs/models.md](docs/models.md) |
| Change ports, open the services to my network, see environment variables and the HTTP API | [docs/configuration.md](docs/configuration.md) |
| Use it on a phone | [mobile/README.md](mobile/README.md) |
| Help test (Windows / Android, running your own services) | [docs/testing.md](docs/testing.md) |
| Build, run the tests, change the code | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| See what changed in each version | [CHANGELOG.md](CHANGELOG.md) |

## 💬 Feedback

Found a problem or have a suggestion? Please open an
[issue](https://github.com/3F3Feng/voice-input-framework/issues/new/choose). In the desktop client,
Settings → Logs → "Copy diagnostics" copies the version, system information and recent log lines in one click.

## 🔒 Privacy

- Audio is sent only to services **you run yourself**. Recognition and LLM clean-up happen on your own computer. The project has no cloud backend, and the code contains no telemetry or analytics uploads.
- The services listen on this machine only (`127.0.0.1`) by default. If you open them to your network, **always set `VIF_API_TOKEN`**, and **never** forward the port to the public internet. Away from home, use an encrypted tunnel such as Tailscale.
- The access token is stored in plain text in the app's private directory on your own device (other apps on a phone cannot read it).
- There are only two kinds of outbound requests: downloading a model from HuggingFace (or the mirror you chose) the first time it is used, and the client checking GitHub Releases for updates.

## 📄 License

MIT License. See [LICENSE](LICENSE).
