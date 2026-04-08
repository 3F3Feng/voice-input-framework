"""
Voice Input Framework - 命令行界面

提供实时语音识别的 CLI 工具。
"""

import asyncio
import logging
import sys
import time
from typing import Optional

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from voice_input_framework.client.audio_capture import AudioCapture, AudioCaptureConfig
from voice_input_framework.client.audio_processor import AudioProcessor, AudioProcessorConfig
from voice_input_framework.client.stt_client import STTClient, STTClientConfig

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

console = Console()


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """Voice Input Framework - 基于大模型的语音输入法"""
    pass


@cli.command()
@click.option("--mode", type=click.Choice(["stream", "file"]), default="stream", help="识别模式")
@click.option("--server", default="http://localhost:8765", help="服务端地址")
@click.option("--sample-rate", type=int, default=16000, help="采样率")
@click.option("--vad/--no-vad", default=True, help="启用语音活动检测")
@click.option("--denoise/--no-denoise", default=False, help="启用降噪")
@click.option("--language", default="auto", help="语言")
@click.argument("file", required=False)
def voice(mode, server, sample_rate, vad, denoise, language, file):
    """
    启动语音识别

    示例：

        # 流式识别（实时录音）
        voice stream --server ws://localhost:8765/ws/stream

        # 文件转写
        voice file audio.wav --server http://localhost:8765
    """
    if mode == "stream":
        asyncio.run(stream_mode(server, sample_rate, vad, denoise, language))
    else:
        if not file:
            console.print("[red]错误: 文件转写模式需要指定音频文件[/red]")
            sys.exit(1)
        asyncio.run(file_mode(file, server, language))


async def stream_mode(
    server_url: str,
    sample_rate: int,
    vad_enabled: bool,
    denoise: bool,
    language: str
):
    """
    流式识别模式

    实时采集麦克风音频并发送到服务端进行识别。
    """
    # 初始化组件
    client_config = STTClientConfig(server_url=server_url)
    capture_config = AudioCaptureConfig(
        sample_rate=sample_rate,
        vad_enabled=vad_enabled,
    )
    processor_config = AudioProcessorConfig(enable_denoise=denoise)

    client = STTClient(client_config)
    capture = AudioCapture(capture_config)
    processor = AudioProcessor(processor_config)

    # 显示状态
    console.print(Panel("[bold green]Voice Input Framework[/bold green]\n\n实时语音识别中... (Ctrl+C 退出)"))

    try:
        # 连接服务端
        await client.connect()
        console.print(f"[green]已连接到 {server_url}[/green]")

        # 创建音频流
        async def audio_stream():
            async for chunk in capture.capture():
                yield chunk

        # 创建显示
        current_text = Text("")
        is_recording = True

        with Live(console=console, refresh_per_second=10) as live:
            # 发送音频并显示结果
            async for result in client.stream_transcribe(
                audio_stream(),
                language=language,
                sample_rate=sample_rate
            ):
                if result.text:
                    if result.is_final:
                        current_text = Text(result.text, style="bold green")
                    else:
                        current_text = Text(result.text, style="yellow")

                    live.update(Panel(
                        f"[bold]识别结果:[/bold]\n{current_text}\n\n[dim]等待下一句...[/dim]",
                        title="实时识别"
                    ))

    except KeyboardInterrupt:
        console.print("\n[yellow]正在停止...[/yellow]")
    except Exception as e:
        console.print(f"[red]错误: {e}[/red]")
        logger.error(e, exc_info=True)
    finally:
        capture.stop()
        await client.disconnect()
        console.print("[green]已退出[/green]")


async def file_mode(file_path: str, server_url: str, language: str):
    """
    文件转写模式

    将音频文件发送到服务端进行转写。
    """
    console.print(f"[yellow]正在转写文件: {file_path}[/yellow]")

    client_config = STTClientConfig(server_url=server_url)
    client = STTClient(client_config)

    try:
        # 确认服务端健康
        if not await client.health_check():
            console.print(f"[red]错误: 无法连接到 {server_url}[/red]")
            return

        # 转写文件
        start_time = time.time()
        result = await client.transcribe_file(file_path, language=language)
        elapsed = time.time() - start_time

        # 显示结果
        console.print(Panel(
            f"[bold]转写结果:[/bold]\n{result.text}\n\n"
            f"[dim]语言: {result.language} | 置信度: {result.confidence:.2f} | 耗时: {elapsed:.2f}s[/dim]",
            title="文件转写"
        ))

    except Exception as e:
        console.print(f"[red]错误: {e}[/red]")
        logger.error(e, exc_info=True)
    finally:
        await client.disconnect()


@cli.command()
@click.option("--server", default="http://localhost:8765", help="服务端地址")
def models(server):
    """列出可用的模型"""
    client_config = STTClientConfig(server_url=server)
    client = STTClient(client_config)

    async def _list_models():
        try:
            models = await client.get_models()
            console.print("[bold]可用模型:[/bold]\n")
            for model in models:
                status = "[green]已加载[/green]" if model.get("is_loaded") else "[dim]未加载[/dim]"
                default = " [bold](默认)[/bold]" if model.get("is_default") else ""
                console.print(f"  • {model['name']} - {model.get('description', '')} {status}{default}")
        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")

    asyncio.run(_list_models())


@cli.command()
@click.option("--server", default="http://localhost:8765", help="服务端地址")
def check(server):
    """检查服务端状态"""
    client_config = STTClientConfig(server_url=server)
    client = STTClient(client_config)

    async def _check():
        try:
            if await client.health_check():
                console.print(f"[green]✓ 服务端正常: {server}[/green]")
            else:
                console.print(f"[red]✗ 服务端无响应: {server}[/red]")
        except Exception as e:
            console.print(f"[red]✗ 连接失败: {e}[/red]")

    asyncio.run(_check())


def main():
    """主入口"""
    cli()


if __name__ == "__main__":
    main()
