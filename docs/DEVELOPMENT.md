# Voice Input Framework - 开发任务

## 任务：修复 CLI 客户端录音交互逻辑

### 问题描述

CLI 客户端 (`run_cli.py`) 的录音交互逻辑存在以下问题：

1. **主循环与录音函数之间的 input 冲突**
2. **用户无法正确停止录音**
3. **WebSocket 连接后立即断开**

### 当前错误代码

**主循环中的问题 (`run_cli.py` 第 580-582 行)：**
```python
if choice == "1":
    asyncio.run_coroutine_threadsafe(self.record_and_transcribe(), self._loop)
    input("\n按 Enter 停止录音...")  # ❌ 这里又调用了一次 input！
    self.is_recording = False
```

**`record_and_transcribe` 函数内部也有自己的 input 调用，导致冲突。**

### 正确实现

根据架构文档，CLI 录音流程应该是：

```
开始录音
    │
    ▼
显示录音面板（"🔴 正在录音... 按 Enter 停止"）
    │
    ▼
启动 sounddevice 音频采集
    │
    ▼
等待用户按 Enter（使用 run_in_executor，不阻塞事件循环）
    │
    ▼
用户按 Enter → 停止采集
    │
    ▼
发送音频到服务器
    │
    ▼
等待识别结果
    │
    ▼
显示结果 → 返回菜单
```

### 实现要求

1. **录音函数 `record_and_transcribe` 必须：**
   - 显示录音面板
   - 使用 `sd.InputStream` 采集音频到缓冲区
   - 使用 `await asyncio.get_event_loop().run_in_executor(None, input, "")` 等待 Enter
   - 用户按 Enter 后停止采集
   - 发送音频到服务器
   - 接收并显示识别结果

2. **主循环只需要：**
   - 启动协程
   - 等待 `is_recording` 标志变为 False

3. **不要在主循环中调用 `input()`！**

### 参考实现框架

```python
async def record_and_transcribe(self):
    """录音并识别"""
    import sounddevice as sd
    
    if self.is_recording:
        return
    
    self.is_recording = True
    audio_buffer = []
    
    # 显示录音面板
    self.console.print(Panel("[bold red]🔴 正在录音...[/bold red]\n[dim]按 Enter 停止[/dim]"))
    
    # 音频回调
    def callback(indata, frames, time, status):
        audio_buffer.append(indata.tobytes())
    
    try:
        # 启动音频采集
        with sd.InputStream(callback=callback, ...):
            # 等待用户按 Enter（不阻塞事件循环）
            await asyncio.get_event_loop().run_in_executor(None, input, "")
    except:
        pass
    
    # 停止录音
    self.is_recording = False
    
    if not audio_buffer:
        return
    
    # 合并音频并发送到服务器
    full_audio = b"".join(audio_buffer)
    # ... WebSocket 通信 ...
```

### 验收标准

1. 用户选择 "1" 开始录音
2. 显示 "🔴 正在录音..." 面板
3. 用户按 Enter 停止录音
4. 识别结果显示在面板中
5. 没有重复的 input 提示
6. WebSocket 正常通信

### 文件位置

- CLI 客户端：`run_cli.py`
- 协议文档：`docs/ARCHITECTURE.md`
- 服务端 API：`server/api.py`
