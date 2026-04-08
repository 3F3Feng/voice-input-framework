#!/usr/bin/env python3
"""
Voice Input Framework - 文件转写演示

运行方式：
    python file_transcribe.py audio.wav --server http://localhost:6543
"""

import argparse
import asyncio
import base64
import logging
import sys
import wave

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def transcribe_file(filename: str, server_url: str, language: str = "auto"):
    """上传音频文件到服务器进行转写"""
    
    # 读取音频文件
    with open(filename, "rb") as f:
        audio_data = f.read()
    
    logger.info(f"读取文件: {filename}, 大小: {len(audio_data)} bytes")
    
    # 转换为 base64
    audio_b64 = base64.b64encode(audio_data).decode()
    
    # 发送请求
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{server_url}/transcribe",
            json={
                "audio": audio_b64,
                "language": language,
            }
        )
        
    if response.status_code == 200:
        result = response.json()
        print(f"\n识别结果: {result.get('text', '')}")
        print(f"置信度: {result.get('confidence', 0):.2%}")
        print(f"语言: {result.get('language', 'unknown')}")
    else:
        print(f"错误: {response.status_code} - {response.text}")


def main():
    parser = argparse.ArgumentParser(description="音频文件转写")
    parser.add_argument("file", help="音频文件路径 (支持 WAV 格式)")
    parser.add_argument("--server", default="http://localhost:6543", help="服务器地址")
    parser.add_argument("--language", default="auto", help="语言 (auto/en/zh)")
    
    args = parser.parse_args()
    
    asyncio.run(transcribe_file(args.file, args.server, args.language))


if __name__ == "__main__":
    main()
