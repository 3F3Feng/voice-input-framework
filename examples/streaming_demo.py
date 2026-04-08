"""
Voice Input Framework - 流式识别演示

演示如何使用流式 API 进行实时语音识别。

运行方式：
    python -m voice_input_framework.examples.streaming_demo --server ws://localhost:8765/ws/stream
"""

import argparse
import asyncio
import logging
import sys

from voice_input_framework.client.audio_capture import AudioCapture, AudioCaptureConfig
from voice_input_framework.client.stt_client import STTClient, STTClientConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class StreamingDemo:
    """
    流式识别演示类

    演示完整的流式语音识别流程。
    """

    def __init__(self, server_url: str):
        """
        初始化演示

        Args:
            server_url: 服务端 WebSocket URL
        """
        self.server_url = server_url
        self.client = STTClient(STTClientConfig(server_url=server_url))
        self.capture = AudioCapture(AudioCaptureConfig())

    async def run(self):
        """
        运行演示

        1. 连接服务端
        2. 实时采集音频
        3. 发送并接收转写结果
        4. 打印结果
        """
        print("=" * 50)
        print("Voice Input Framework - 流式识别演示")
        print("=" * 50)
        print(f"服务端: {self.server_url}")
        print("按 Ctrl+C 退出")
        print("=" * 50)

        try:
            # 连接服务端
            print("[1/3] 正在连接服务端...")
            await self.client.connect()
            print("[✓] 已连接")

            # 创建音频流生成器
            print("[2/3] 正在启动音频采集...")
            print("[✓] 开始说话...\n")

            audio_stream = self.capture.capture()

            # 流式转写
            print("[3/3] 正在识别...")
            sentence_count = 0

            async for result in self.client.stream_transcribe(
                audio_stream,
                language="auto",
                sample_rate=16000
            ):
                if result.text:
                    sentence_count += 1
                    prefix = "[最终]" if result.is_final else "[实时]"
                    print(f"{prefix} {result.text}")

        except KeyboardInterrupt:
            print("\n[退出] 用户中断")
        except Exception as e:
            print(f"\n[错误] {e}")
            logger.error(e, exc_info=True)
        finally:
            await self.cleanup()

    async def cleanup(self):
        """清理资源"""
        print("\n[清理] 正在停止...")
        self.capture.stop()
        await self.client.disconnect()
        print("[完成]")


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="流式语音识别演示")
    parser.add_argument(
        "--server",
        default="ws://localhost:8765/ws/stream",
        help="服务端 WebSocket URL"
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=16000,
        help="采样率"
    )
    parser.add_argument(
        "--vad",
        action="store_true",
        default=True,
        help="启用语音活动检测"
    )
    args = parser.parse_args()

    demo = StreamingDemo(args.server)
    await demo.run()


if __name__ == "__main__":
    asyncio.run(main())
