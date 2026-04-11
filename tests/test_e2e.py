#!/usr/bin/env python3
"""
Voice Input Framework - 端到端测试

测试 STT + LLM 分离架构的完整流程。
"""

import asyncio
import json
import base64
import httpx
import websockets
import numpy as np
import time

# 配置
STT_HOST = "localhost"
STT_PORT = 6544
LLM_HOST = "localhost"
LLM_PORT = 6545

def generate_test_audio():
    """生成测试音频数据（静音）"""
    # 生成 1 秒静音，16kHz，16-bit PCM
    sample_rate = 16000
    duration = 1.0
    samples = int(sample_rate * duration)
    # 静音数据
    audio = np.zeros(samples, dtype=np.int16)
    return audio.tobytes()

async def test_llm_server():
    """测试 LLM 服务器"""
    print("\n=== 测试 LLM 服务器 ===")
    
    async with httpx.AsyncClient() as client:
        # 健康检查
        resp = await client.get(f"http://{LLM_HOST}:{LLM_PORT}/health")
        print(f"LLM 健康检查: {resp.status_code}")
        print(f"  响应: {resp.json()}")
        
        # 处理测试
        resp = await client.post(
            f"http://{LLM_HOST}:{LLM_PORT}/process",
            json={"text": "你好世界，这是一个测试"}
        )
        print(f"LLM 处理: {resp.status_code}")
        data = resp.json()
        print(f"  原文: {data.get('original_text')}")
        print(f"  结果: {data.get('text')}")
        print(f"  延迟: {data.get('llm_latency_ms'):.1f}ms")
        
    return True

async def test_stt_server():
    """测试 STT 服务器"""
    print("\n=== 测试 STT 服务器 ===")
    
    async with httpx.AsyncClient() as client:
        # 健康检查
        resp = await client.get(f"http://{STT_HOST}:{STT_PORT}/health")
        print(f"STT 健康检查: {resp.status_code}")
        print(f"  响应: {resp.json()}")
        
        # 模型列表
        resp = await client.get(f"http://{STT_HOST}:{STT_PORT}/models")
        print(f"STT 模型列表: {resp.status_code}")
        models = resp.json()
        for m in models:
            print(f"  - {m['name']} (loaded: {m['is_loaded']})")
    
    return True

async def test_websocket_flow():
    """测试 WebSocket 完整流程"""
    print("\n=== 测试 WebSocket 流程 ===")
    
    uri = f"ws://{STT_HOST}:{STT_PORT}/ws/stream"
    
    try:
        async with websockets.connect(uri, close_timeout=5) as ws:
            # 等待就绪消息
            ready = await asyncio.wait_for(ws.recv(), timeout=10.0)
            ready_data = json.loads(ready)
            print(f"就绪消息: {ready_data['type']}")
            print(f"  模型: {ready_data.get('model')}")
            print(f"  加载中: {ready_data.get('is_loading')}")
            
            # 发送配置
            await ws.send(json.dumps({
                "type": "config",
                "return_timestamps": False,
                "language": "auto"
            }))
            
            # 等待配置确认（可选，服务器可能不发送）
            try:
                config_ack = await asyncio.wait_for(ws.recv(), timeout=2.0)
                print(f"配置确认: {json.loads(config_ack)}")
            except asyncio.TimeoutError:
                print("(跳过配置确认，继续)")
            
            # 发送测试音频
            audio_data = generate_test_audio()
            audio_b64 = base64.b64encode(audio_data).decode('utf-8')
            
            print("发送音频数据...")
            await ws.send(json.dumps({
                "type": "audio",
                "data": audio_b64
            }))
            
            # 发送结束信号
            await ws.send(json.dumps({"type": "end"}))
            
            # 接收结果
            messages_received = []
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    data = json.loads(msg)
                    msg_type = data.get("type")
                    messages_received.append(msg_type)
                    
                    if msg_type == "stt_result":
                        print(f"STT 结果: {data.get('text', '')[:50]}...")
                        print(f"  STT 延迟: {data.get('stt_latency_ms', 0):.1f}ms")
                    elif msg_type == "llm_start":
                        print(f"LLM 开始处理...")
                    elif msg_type == "result":
                        print(f"最终结果: {data.get('text', '')}")
                        print(f"  STT 延迟: {data.get('stt_latency_ms', 0):.1f}ms")
                        print(f"  LLM 延迟: {data.get('llm_latency_ms', 0):.1f}ms")
                    elif msg_type == "done":
                        print("流程完成")
                        break
                    elif msg_type == "error":
                        print(f"错误: {data.get('error_message')}")
                        break
                        
                except asyncio.TimeoutError:
                    print("等待结果超时")
                    break
            
            print(f"\n收到的消息类型: {messages_received}")
            return "result" in messages_received or "stt_result" in messages_received
            
    except Exception as e:
        print(f"WebSocket 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """主测试函数"""
    print("=" * 60)
    print("Voice Input Framework - 端到端测试")
    print("=" * 60)
    
    results = {}
    
    # 测试 LLM 服务器
    try:
        results['llm'] = await test_llm_server()
    except Exception as e:
        print(f"LLM 测试失败: {e}")
        results['llm'] = False
    
    # 测试 STT 服务器
    try:
        results['stt'] = await test_stt_server()
    except Exception as e:
        print(f"STT 测试失败: {e}")
        results['stt'] = False
    
    # 测试 WebSocket 流程
    try:
        results['websocket'] = await test_websocket_flow()
    except Exception as e:
        print(f"WebSocket 测试失败: {e}")
        results['websocket'] = False
    
    # 总结
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name}: {status}")
    
    all_passed = all(results.values())
    print("\n" + ("✅ 所有测试通过" if all_passed else "❌ 部分测试失败"))
    return all_passed

if __name__ == "__main__":
    asyncio.run(main())
