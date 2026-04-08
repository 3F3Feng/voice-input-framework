# Voice Input Framework - Client

A simple and reliable voice input client for Windows.

## Installation

```bash
pip install PySimpleGUI sounddevice websockets pyperclip
```

## Usage

### GUI Client

```bash
python -m client.gui
```

With custom server:
```bash
python -m client.gui localhost 6543
```

### Programmatic Usage

```python
from client.gui import VoiceInputClient

client = VoiceInputClient(host="localhost", port=6543)
client.run()
```

## Building Windows Executable

```bash
pip install pyinstaller
pyinstaller --name VoiceInputFramework --windowed --onefile client/gui.py
```

## Download Pre-built Executable

Download the latest release from:
https://github.com/3F3Feng/voice-input-framework/releases

## Keyboard Shortcuts

- **Enter** - Stop recording (when recording)
- **Escape** - Exit application

## Features

- 🎤 Real-time audio recording
- 🔊 WebSocket streaming to server
- 📋 One-click copy to clipboard
- 📝 Activity logging
- 🖥️ Dark theme UI
