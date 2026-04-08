"""
Voice Input Framework - 文件转写演示

演示如何将音频文件上传到服务端进行转写。

运行方式：
    python -m voice_input_framework.examples.file_transcribe audio.wav --server http://localhost:8765
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path

from voice_input_framework.client.stt_client import STTClient, STTClientConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def transcribe_file(
    file_path: str,
    server_url: str,
    language: str = "auto",
    model: str = None
) -> dict:
    """
    转写音频文件

    Args:
        file_path: 音频文件路径
        server_url: 服务端地址
        language: 语言
        model: 模型名称

    Returns:
        dict: 转写结果
    """
    client = STTClient(STTClientConfig(server_url=server_url))

    # 检查文件是否存在
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 检查服务端健康状态
    print(f"[1/4] 检查服务端连接: {server_url}")
    if not await client.health_check():
        raise ConnectionError(f"无法连接到服务端: {server_url}")
    print(f"[✓] 服务端正常")

    # 上传文件
    print(f"[2/4] 上传文件: {file_path}")
    print(f"[3/4] 转写中...")
    start_time = time.time()

    result = await client.transcribe_file(
        file_path,
        language=language,
        model=model
    )

    elapsed = time.time() - start_time
    print(f"[✓] 转写完成，耗时: {elapsed:.2f}s")

    # 清理
    print(f"[4/4] 关闭连接")
    await client.disconnect()

    return {
        "text": result.text,
        "language": result.language,
        "confidence": result.confidence,
        "processing_time": elapsed,
    }


def print_result(result: dict, file_path: str):
    """打印结果"""
    print("\n" + "=" * 60)
    print("转写结果")
    print("=" * 60)
    print(f"文件: {file_path}")
    print(f"语言: {result['language']}")
    print(f"置信度: {result['confidence']:.2f}")
    print(f"处理时间: {result['processing_time']:.2f}s")
    print("-" * 60)
    print(result["text"])
    print("=" * 60)


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="音频文件转写演示")
    parser.add_argument(
        "file",
        help="音频文件路径"
    )
    parser.add_argument(
        "--server",
        default="http://localhost:8765",
        help="服务端地址"
    )
    parser.add_argument(
        "--language",
        default="auto",
        choices=["auto", "zh", "en", "ja", "ko"],
        help="语言"
    )
    parser.add_argument(
        "--model",
        help="指定模型"
    )
    parser.add_argument(
        "--output",
        "-o",
        help="输出文件路径（保存结果）"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Voice Input Framework - 文件转写演示")
    print("=" * 60)

    try:
        result = await transcribe_file(
            args.file,
            args.server,
            language=args.language,
            model=args.model
        )

        print_result(result, args.file)

        # 保存到文件
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result["text"])
            print(f"\n[保存] 结果已保存到: {args.output}")

    except Exception as e:
        print(f"\n[错误] {e}")
        logger.error(e, exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
