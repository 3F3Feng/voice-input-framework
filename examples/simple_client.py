#!/usr/bin/env python3
"""
Voice Input Framework - 简单 Python 客户端示例

pip install websockets sounddevice
python simple_client.py
"""

import asyncio
import base64
import json
import sounddevice as sd

SERVER_URL = "ws://100.124.8.85:6543/ws/stream"


async def record_and_send():
    """录音并发送到服务器识别"""
    import websockets
    
    audio_buffer = []
    
    def callback(indata, frames, time, status):
        if status:
            print(f"Audio status: {status}")
        audio_buffer.append(indata.tobytes())
    
    print("按 Enter 开始录音...")
    input()
    
    print("🔴 正在录音... 按 Enter 停止")
    
    stream = sd.InputStream(
        samplerate=16000,
        channels=1,
        dtype='int16',
        blocksize=1024,
        callback=callback
    )
    
    with stream:
        input()  # 等待用户按 Enter 停止
    
    if not audio_buffer:
        print("未检测到音频")
        return
    
    full_audio = b"".join(audio_buffer)
    print(f"已采集 {len(full_audio)} 字节")
    
    # 连接服务器
    async with websockets.connect(SERVER_URL) as ws:
        # 发送配置
        await ws.send(json.dumps({"type": "config", "language": "auto"}))
        
        # 等待就绪
        resp = await ws.recv()
        data = json.loads(resp)
        print(f"服务器就绪: {data}")
        
        # 发送音频
        await ws.send(json.dumps({
            "type": "audio",
            "data": base64.b64encode(full_audio).decode()
        }))
        
        # 发送结束
        await ws.send(json.dumps({"type": "end"}))
        
        # 接收结果
        while True:
            resp = await asyncio.wait_for(ws.recv(), timeout=30)
            data = json.loads(resp)
            
            if data.get("type") == "result":
                print(f"\n识别结果: {data.get('text')}")
            elif data.get("type") == "done":
                break


if __name__ == "__main__":
    asyncio.run(record_and_send())
