# Voice Input Framework - GUI Client

官方的快捷键驱动语音输入客户端。

## 功能特性

✨ **快捷键控制** - 按住快捷键说话，松开后自动输入
🎙️ **实时录音** - 高质量音频捕获
🤖 **自动识别** - WebSocket 流式识别
📋 **自动输入** - 识别结果自动输入到活跃窗口
🔄 **模型切换** - 动态切换 STT 模型
🎧 **麦克风选择** - 支持多麦克风选择
🚨 **错误提示** - 所有 API 错误实时显示

## 安装依赖

```bash
pip install pynput sounddevice websockets httpx pyautogui pyperclip PySimpleGUI
```

## 使用方法

### 作为脚本直接运行

```bash
# Windows
python client/gui.py

# Linux / macOS
python3 client/gui.py
```

### 作为模块导入使用

```python
from client.gui import HotkeyVoiceInput

# 创建客户端
client = HotkeyVoiceInput(server_host="100.124.8.85", server_port=6543)

# 运行 GUI
client.run()
```

### 指定服务器地址

通过环境变量指定：

```bash
# Windows PowerShell
$env:VIF_SERVER_HOST = "your.server.com"
$env:VIF_SERVER_PORT = "6543"
python client/gui.py

# Linux / macOS
export VIF_SERVER_HOST="your.server.com"
export VIF_SERVER_PORT="6543"
python3 client/gui.py
```

## UI 界面说明

### 快捷键设置
- **开始/停止录音**: 默认 `alt+v`，可自定义任何组合 (ctrl+alt+v, shift+f12 等)
- **更新**: 修改快捷键后点击更新

### 麦克风设置
- **麦克风**: 从下拉菜单选择要使用的麦克风
- 如果看不到你的麦克风，运行 `python examples/test_audio_devices.py` 诊断

### 服务器配置
- **主机**: 服务器地址（默认 100.124.8.85）
- **端口**: 服务器端口（默认 6543）
- **连接**: 点击按钮连接到服务器

### 模型设置
- **选择模型**: 从下拉菜单选择要使用的 STT 模型
- **🔄 刷新**: 重新获取服务器上的模型列表
- **切换**: 切换到选择的模型

### 识别结果
- 显示服务器返回的识别结果
- **📋 复制**: 复制结果到剪贴板
- **🗑️ 清空**: 清空结果显示
- **✏️ 输入（自动）**: 手动将结果输入到活跃窗口

### 日志和错误
- **日志**: 实时显示所有操作日志
- **错误信息**: 用红色显示任何 API 错误或异常

## 工作流程

1. **启动应用** - `python client/gui.py`
2. **配置服务器** - 设置主机和端口，点击"连接"
3. **等待连接** - 应该看到"已连接 (模型名)"
4. **选择麦克风** - 从下拉菜单选择设备
5. **配置快捷键** - 修改快捷键（可选）
6. **开始使用**:
   - 按住快捷键说话
   - 松开快捷键后自动录音并上传
   - 服务器返回识别结果
   - 结果自动输入到活跃窗口

## 故障排除

### 连接失败

如果看到"连接失败"错误：

1. 检查服务器地址和端口是否正确
2. 确保服务器在运行：`python server/api.py`
3. 检查防火墙是否允许连接
4. 查看错误信息区域的具体错误

### 麦克风没有声音

1. 检查麦克风是否被静音（物理开关或 Windows 设置）
2. 在 Windows 设置 → 声音 → 输入 中启用麦克风
3. 运行诊断：`python examples/test_audio_devices.py`
4. 尝试选择不同的麦克风

### 模型列表为空

如果模型列表显示为空，运行诊断脚本：

```bash
python examples/test_models.py
```

这个脚本会检查：
- 服务器模型定义是否存在
- STT 引擎管理器是否正确初始化
- 模型数据是否能正确解析

详细排查步骤见：[诊断指南](../examples/DIAGNOSE_MODELS.md)

### 识别结果为空

1. 检查日志中是否有错误信息
2. 确保当前模型已加载
3. 尝试刷新模型列表
4. 检查音频是否被正确录制

### 自动输入不工作

1. 确保目标窗口已获得焦点
2. 尝试点击"✏️ 输入（自动）"按钮手动输入
3. 如果需要逐字输入，应用会自动回退
4. 某些应用可能不支持粘贴，需要管理员权限

## 配置文件

### 默认配置

```python
DEFAULT_SERVER_HOST = "100.124.8.85"
DEFAULT_SERVER_PORT = 6543
DEFAULT_HOTKEY = "alt+v"

# 音频参数
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_CHUNK_SIZE = 1024
```

修改 `client/gui.py` 中的这些常量来改变默认设置。

## 打包为可执行文件

### Windows

```bash
pip install pyinstaller

# 简单打包
pyinstaller --onefile --windowed --icon=icon.ico client/gui.py

# 输出在 dist/ 目录下
```

## 快捷键参考

支持的快捷键格式：

- **单键**: `v`, `f12`, etc.
- **修饰符**:
  - `ctrl+v` - Ctrl + V
  - `alt+v` - Alt + V
  - `shift+v` - Shift + V
  - `ctrl+alt+v` - Ctrl + Alt + V
  - `shift+f12` - Shift + F12

## 环境变量

- `VIF_SERVER_HOST` - 服务器地址
- `VIF_SERVER_PORT` - 服务器端口
- `VIF_LOG_LEVEL` - 日志级别 (INFO, DEBUG, WARNING等)

## 开发

### 从源代码运行

```bash
cd e:\voice-input-framework

# 安装依赖
pip install -r requirements.txt

# 运行客户端
python -m client.gui
```

### 导入为库

```python
from client import HotkeyVoiceInput

client = HotkeyVoiceInput()
client.run()
```

## 许可证

见项目根目录的 LICENSE 文件

- 🎤 Real-time audio recording
- 🔊 WebSocket streaming to server
- 📋 One-click copy to clipboard
- 📝 Activity logging
- 🖥️ Dark theme UI
