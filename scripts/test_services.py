#!/usr/bin/env python3
"""
Voice Input Framework - Service Integration Test

测试 STT 和 LLM 服务的集成。
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp


# 服务地址
STT_URL = "http://localhost:6544"
LLM_URL = "http://localhost:6545"


async def test_stt_health():
    """测试 STT 服务健康检查"""
    print("\n[TEST] STT Health Check")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{STT_URL}/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"  ✅ STT Service: {data['status']}")
                    print(f"  Current model: {data['current_model']}")
                    print(f"  Uptime: {data['uptime_seconds']:.1f}s")
                    return True
                else:
                    print(f"  ❌ STT Health failed: {resp.status}")
                    return False
        except Exception as e:
            print(f"  ❌ STT Service not reachable: {e}")
            return False


async def test_llm_health():
    """测试 LLM 服务健康检查"""
    print("\n[TEST] LLM Health Check")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{LLM_URL}/health") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"  ✅ LLM Service: {data['status']}")
                    print(f"  Current model: {data['current_model']}")
                    print(f"  Uptime: {data['uptime_seconds']:.1f}s")
                    print(f"  Processing: {data['is_processing']}")
                    return True
                else:
                    print(f"  ❌ LLM Health failed: {resp.status}")
                    return False
        except Exception as e:
            print(f"  ❌ LLM Service not reachable: {e}")
            return False


async def test_llm_process():
    """测试 LLM 文本处理"""
    print("\n[TEST] LLM Process")
    
    test_text = "嗯今天天气不错啊就是有点冷吧"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{LLM_URL}/process",
                json={"text": test_text},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"  ✅ Process successful")
                    print(f"  Input: {test_text}")
                    print(f"  Output: {data['text']}")
                    print(f"  Latency: {data['llm_latency_ms']:.0f}ms")
                    print(f"  Model: {data['model']}")
                    return True
                elif resp.status == 503:
                    print(f"  ⏳ Model still loading...")
                    return False
                else:
                    print(f"  ❌ Process failed: {resp.status}")
                    return False
        except Exception as e:
            print(f"  ❌ Process error: {e}")
            return False


async def test_stt_models():
    """测试 STT 模型列表"""
    print("\n[TEST] STT Models List")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{STT_URL}/models") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"  ✅ Available models:")
                    for model in data:
                        status = " (loaded)" if model['is_loaded'] else ""
                        default = " [default]" if model['is_default'] else ""
                        print(f"    - {model['name']}{status}{default}")
                    return True
                else:
                    print(f"  ❌ Failed to list models: {resp.status}")
                    return False
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False


async def test_llm_models():
    """测试 LLM 模型列表"""
    print("\n[TEST] LLM Models List")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{LLM_URL}/models") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"  ✅ Available models:")
                    for model in data:
                        status = " (loaded)" if model['is_loaded'] else ""
                        current = " [current]" if model['is_current'] else ""
                        print(f"    - {model['name']}{status}{current}")
                    return True
                else:
                    print(f"  ❌ Failed to list models: {resp.status}")
                    return False
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return False


async def test_integration():
    """测试 STT + LLM 集成流程"""
    print("\n[TEST] Integration (STT → LLM)")
    
    # 模拟 STT 输出
    stt_output = "嗯今天我去超市买了点苹果和香蕉"
    
    print(f"  1. STT Output: {stt_output}")
    
    # 发送到 LLM 处理
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{LLM_URL}/process",
                json={"text": stt_output},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"  2. LLM Output: {data['text']}")
                    print(f"  3. Latency: {data['llm_latency_ms']:.0f}ms")
                    return True
                elif resp.status == 503:
                    print(f"  ⏳ Model still loading, retry in 5s...")
                    await asyncio.sleep(5)
                    return False
                else:
                    print(f"  ❌ Integration failed: {resp.status}")
                    return False
        except Exception as e:
            print(f"  ❌ Integration error: {e}")
            return False


async def wait_for_services(timeout: int = 60):
    """等待服务启动"""
    print(f"\n[WAIT] Waiting for services to start (timeout: {timeout}s)")
    
    start_time = time.time()
    stt_ready = False
    llm_ready = False
    
    async with aiohttp.ClientSession() as session:
        while time.time() - start_time < timeout:
            # 检查 STT
            if not stt_ready:
                try:
                    async with session.get(f"{STT_URL}/health") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data['status'] == 'ok':
                                stt_ready = True
                                print(f"  ✅ STT Service ready")
                except:
                    pass
            
            # 检查 LLM
            if not llm_ready:
                try:
                    async with session.get(f"{LLM_URL}/health") as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data['status'] == 'ok':
                                llm_ready = True
                                print(f"  ✅ LLM Service ready")
                except:
                    pass
            
            if stt_ready and llm_ready:
                return True
            
            await asyncio.sleep(2)
    
    print(f"  ❌ Timeout waiting for services")
    return False


async def main():
    """主测试流程"""
    print("=" * 60)
    print("Voice Input Framework - Service Integration Test")
    print("=" * 60)
    
    # 等待服务启动
    if not await wait_for_services():
        print("\n❌ Services not ready, aborting tests")
        return 1
    
    # 运行测试
    results = []
    
    # 基础健康检查
    results.append(await test_stt_health())
    results.append(await test_llm_health())
    
    # 模型列表
    results.append(await test_stt_models())
    results.append(await test_llm_models())
    
    # LLM 处理测试
    results.append(await test_llm_process())
    
    # 集成测试
    results.append(await test_integration())
    
    # 汇总
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 60)
    
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
