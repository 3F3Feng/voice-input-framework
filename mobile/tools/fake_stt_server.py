#!/usr/bin/env python3
"""不装模型也能起的 STT 服务,给手机端开发和测试用。

跑的是真的 services/stt_server.py(`/ws/stream`、令牌、分段、心跳都是真的),只把
两处换成假的:

- 转写:不跑模型,回一句「heard 3.2 seconds」;全是静音时回空串(客户端应当报
  「没有听到说话」);
- LLM 后处理:不连 LLM 服务,在原文后面加「 [polished]」。`--no-llm` 关掉它。

用法(仓库根目录):

    uv run --no-project --with fastapi --with 'uvicorn[standard]' --with httpx \\
        --with python-multipart --with 'numpy<2' --with scipy \\
        python mobile/tools/fake_stt_server.py --host 0.0.0.0 --token secret

手机填 `http://<这台机器的 IP>:6544`、令牌填 `secret` 就能把整条链路走通。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6544)
    parser.add_argument("--token", default=None, help="要求的访问令牌(即 VIF_API_TOKEN)")
    parser.add_argument("--no-llm", action="store_true", help="不模拟 LLM 后处理")
    args = parser.parse_args()

    # 令牌在 shared/auth.py 里每次请求现读环境变量,import 前后设都行。
    if args.token:
        os.environ["VIF_API_TOKEN"] = args.token

    import uvicorn

    import services.stt_server as stt
    from shared.data_types import TranscriptionResult

    engine = stt.engine

    async def fake_load(*_args, **_kwargs) -> bool:
        engine._is_loaded = True
        engine._loading = False
        return True

    async def fake_transcribe(audio: bytes, language: str = "auto", context: str | None = None):
        seconds = len(audio) / 32000
        silent = not any(audio)
        return TranscriptionResult(
            text="" if silent else f"heard {seconds:.1f} seconds",
            language="en" if language == "auto" else language,
            is_final=True,
        )

    async def fake_llm(text: str, **_kwargs):
        return f"{text} [polished]", 5.0, None

    engine.load = fake_load
    engine.transcribe = fake_transcribe
    stt.call_llm_server = fake_llm
    stt.LLM_ENABLED = not args.no_llm
    stt.LLM_SUPPORTED = True

    uvicorn.run(stt.app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
