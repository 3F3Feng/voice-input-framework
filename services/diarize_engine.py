#!/usr/bin/env python3
"""
Voice Input Framework - Speaker Diarization Engine

使用 pyannote.audio 进行说话人分离。
依赖: pip install pyannote.audio==3.3.3
        huggingface-cli login  (模型 gated, 需接受协议)

运行环境: Apple Silicon (MPS) 优先, 回退 CPU
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

logger = logging.getLogger("diarize-engine")

# ── 配置 ──
DIARIZE_MODEL_ID = os.getenv(
    "VIF_DIARIZE_MODEL", "pyannote/speaker-diarization-3.1"
)
DIARIZE_ENABLED = os.getenv("VIF_DIARIZE_ENABLED", "true").lower() == "true"


class DiarizationEngine:
    """说话人分离引擎 - 封装 pyannote.audio pipeline"""

    def __init__(self):
        self._pipeline = None
        self._is_loaded = False
        self._loading = False
        self._load_lock = None  # asyncio.Lock, 动态创建
        self._model_id = DIARIZE_MODEL_ID
        self._device = "cpu"
        self._stats = {
            "total_requests": 0,
            "total_duration_seconds": 0.0,
            "failed_requests": 0,
            "total_inference_seconds": 0.0,
        }
        self.start_time = time.time()

    # ── 属性 ──
    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def device(self) -> str:
        return self._device

    @property
    def stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    # ── 加载 ──
    async def load(self) -> bool:
        """异步加载 pyannote pipeline"""
        if self._is_loaded:
            return True

        if self._load_lock is None:
            self._load_lock = asyncio.Lock()

        async with self._load_lock:
            if self._is_loaded:
                return True
            if self._loading:
                logger.info("Diarization model is loading, waiting...")
                while self._loading:
                    await asyncio.sleep(0.5)
                return self._is_loaded

            self._loading = True
            try:
                logger.info(f"Loading diarization model: {self._model_id}")

                def _load_sync():
                    from pyannote.audio import Pipeline
                    import torch

                    # 检测设备
                    if torch.backends.mps.is_available():
                        self._device = "mps"
                    elif torch.cuda.is_available():
                        self._device = "cuda"
                    else:
                        self._device = "cpu"

                    logger.info(f"Creating pipeline (device={self._device})...")
                    pipeline = Pipeline.from_pretrained(self._model_id)
                    if self._device != "cpu":
                        pipeline.to(torch.device(self._device))
                    self._pipeline = pipeline
                    self._is_loaded = True
                    logger.info(f"Diarization model loaded (device={self._device})")

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _load_sync)
                return True
            except Exception as e:
                logger.error(f"Failed to load diarization model: {e}", exc_info=True)
                self._is_loaded = False
                return False
            finally:
                self._loading = False

    def _is_pyannote_available(self) -> bool:
        """检查 pyannote.audio 是否可导入"""
        try:
            import pyannote.audio  # noqa
            return True
        except ImportError:
            return False

    # ── 推理 ──
    async def diarize(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        对音频文件进行说话人分离

        Args:
            audio_path: 音频文件路径 (wav/mp3 等, pyannote 自动解码)
            num_speakers: 已知说话人数 (可选)
            min_speakers: 最少说话人数
            max_speakers: 最多说话人数

        Returns:
            {
                "segments": [
                    {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.2, "confidence": 0.95},
                    ...
                ],
                "num_speakers": 2,
                "duration": 5940.0,
                "inference_latency_ms": 12345.0,
                "device": "mps",
                "model": "pyannote/speaker-diarization-3.1"
            }
        """
        self._stats["total_requests"] += 1

        if not self._is_loaded:
            success = await self.load()
            if not success:
                raise RuntimeError("Diarization model not loaded")

        loop = asyncio.get_running_loop()
        t0 = time.time()

        try:
            def _run_diarize():
                kwargs = {}
                if num_speakers is not None:
                    kwargs["num_speakers"] = num_speakers
                if min_speakers is not None:
                    kwargs["min_speakers"] = min_speakers
                if max_speakers is not None:
                    kwargs["max_speakers"] = max_speakers
                return self._pipeline(audio_path, **kwargs)

            diarization = await loop.run_in_executor(None, _run_diarize)
            inference_time = (time.time() - t0) * 1000

            # 转换为列表格式
            segments = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segments.append({
                    "speaker": speaker,
                    "start": round(turn.start, 3),
                    "end": round(turn.end, 3),
                    "duration": round(turn.end - turn.start, 3),
                })

            # 获取音频时长
            duration = diarization.get_timeline().extent().end if diarization.get_timeline() else 0.0

            # 获取说话人数量
            speakers = set(s["speaker"] for s in segments)

            # 更新统计
            self._stats["total_duration_seconds"] += duration
            self._stats["total_inference_seconds"] += inference_time / 1000

            return {
                "segments": segments,
                "num_speakers": len(speakers),
                "speakers": sorted(list(speakers)),
                "duration": round(duration, 3),
                "inference_latency_ms": round(inference_time, 1),
                "device": self._device,
                "model": self._model_id,
            }

        except Exception as e:
            self._stats["failed_requests"] += 1
            logger.error(f"Diarization error: {e}", exc_info=True)
            raise

    # ── 工具 ──
    def get_health(self) -> Dict[str, Any]:
        """健康检查信息"""
        return {
            "status": "ok" if self._is_loaded else ("loading" if self._loading else "unloaded"),
            "model": self._model_id,
            "device": self._device,
            "is_loaded": self._is_loaded,
            "is_loading": self._loading,
            "pyannote_installed": self._is_pyannote_available(),
            "uptime_seconds": round(time.time() - self.start_time, 1),
            "stats": self._stats,
        }

    def unload(self):
        """释放模型内存"""
        import gc
        import torch
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
        self._is_loaded = False
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        gc.collect()
        logger.info("Diarization model unloaded")
