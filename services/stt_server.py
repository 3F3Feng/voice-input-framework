#!/usr/bin/env python3
"""
Voice Input Framework - STT Service

独立的 STT 服务器，使用 qwen_asr 进行语音识别。
运行在独立的 conda 环境: vif-stt (transformers 4.x)

Port: 6544
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# 添加项目路径
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 配置日志
_log_level = os.getenv("VIF_LOG_LEVEL", "INFO").upper()
_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=_log_level, format=_log_format)
logger = logging.getLogger("stt-server")

# ============== Data Models ==============

class TranscriptionResult(BaseModel):
    text: str
    confidence: float = 1.0
    language: str = "auto"
    is_final: bool = True
    stt_latency_ms: float = 0.0
    model: str = ""

class ModelInfo(BaseModel):
    name: str
    description: str = ""
    is_loaded: bool = False
    is_default: bool = False

class HealthStatus(BaseModel):
    status: str
    version: str = "1.0.0"
    uptime_seconds: float
    current_model: str
    loaded_models: List[str]
    active_connections: int = 0

# ============== STT Engine ==============

class STTEngine:
    """STT 引擎管理器"""
    
    AVAILABLE_MODELS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 3.5,
        },
        "qwen_asr_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.5,
        },
    }
    
    def __init__(self, default_model: str = "qwen_asr"):
        self.default_model = default_model
        self.current_model_name = default_model
        self._model = None
        self._is_loaded = False
        self._loading = False
        self._load_lock = asyncio.Lock()
        self._model_info = self.AVAILABLE_MODELS.get(default_model, self.AVAILABLE_MODELS["qwen_asr"])
        self.start_time = time.time()
        
    async def load(self) -> bool:
        """加载模型"""
        async with self._load_lock:
            if self._is_loaded:
                return True
            if self._loading:
                logger.info("Model is loading, waiting...")
                while self._loading:
                    await asyncio.sleep(0.5)
                return self._is_loaded
            
            self._loading = True
            try:
                logger.info(f"Loading STT model: {self._model_info['model_id']}")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_sync)
                self._is_loaded = True
                logger.info(f"STT model loaded successfully")
                return True
            except Exception as e:
                logger.error(f"Failed to load STT model: {e}")
                return False
            finally:
                self._loading = False
    
    def _load_sync(self):
        """同步加载模型"""
        import torch
        from qwen_asr import Qwen3ASRModel
        
        model_id = self._model_info["model_id"]
        
        # 检测设备
        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"
        
        # Qwen3-ASR 使用 bfloat16 效果更好
        dtype = torch.bfloat16 if device != "cpu" else torch.float32
        logger.info(f"Loading model on {device} with {dtype}")
        
        self._model = Qwen3ASRModel.from_pretrained(
            model_id,
            dtype=dtype,
            device_map=device,
            max_new_tokens=256,
        )
    
    async def transcribe(self, audio_data: bytes, language: str = "auto") -> TranscriptionResult:
        """转写音频"""
        if not self._is_loaded:
            await self.load()
        
        import numpy as np
        
        # 转换音频
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0
        sample_rate = 16000
        
        # 执行转写
        loop = asyncio.get_event_loop()
        
        def _do():
            lang = None if language == "auto" else language
            results = self._model.transcribe(
                audio=(audio_array, sample_rate),
                language=lang,
            )
            if results and len(results) > 0:
                return results[0].text, results[0].language
            return "", language
        
        start_time = time.time()
        text, detected_lang = await loop.run_in_executor(None, _do)
        latency = (time.time() - start_time) * 1000
        
        return TranscriptionResult(
            text=text.strip(),
            confidence=1.0,
            language=detected_lang or language,
            is_final=True,
            stt_latency_ms=latency,
            model=self.current_model_name,
        )
    
    def is_loading(self) -> bool:
        return self._loading
    
    def is_model_loaded(self) -> bool:
        return self._is_loaded

# ============== FastAPI App ==============

# 配置
STT_HOST = os.getenv("VIF_STT_HOST", "0.0.0.0")
STT_PORT = int(os.getenv("VIF_STT_PORT", "6544"))
STT_MODEL = os.getenv("VIF_STT_MODEL", "qwen_asr")

# 初始化引擎
engine = STTEngine(default_model=STT_MODEL)

app = FastAPI(
    title="Voice Input Framework - STT Service",
    description="独立的语音识别服务，使用 Qwen3-ASR",
    version="1.0.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """启动时加载模型"""
    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")
    logger.info(f"Default model: {STT_MODEL}")
    # 后台加载模型（非阻塞）
    asyncio.create_task(engine.load())

@app.on_event("shutdown")
async def shutdown_event():
    """关闭时清理"""
    logger.info("STT Service shutting down")

@app.get("/health", response_model=HealthStatus)
async def health_check():
    """健康检查"""
    return HealthStatus(
        status="ok" if engine.is_model_loaded() else "loading",
        version="1.0.0",
        uptime_seconds=time.time() - engine.start_time,
        current_model=engine.current_model_name,
        loaded_models=[engine.current_model_name] if engine.is_model_loaded() else [],
        active_connections=0,
    )

@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """获取可用模型列表"""
    models = []
    for name, info in STTEngine.AVAILABLE_MODELS.items():
        models.append(ModelInfo(
            name=name,
            description=f"STT model: {info['model_id']}",
            is_loaded=(name == engine.current_model_name and engine.is_model_loaded()),
            is_default=(name == engine.default_model),
        ))
    return models

@app.post("/transcribe", response_model=TranscriptionResult)
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("auto"),
):
    """转写音频文件"""
    try:
        audio_content = await file.read()
        result = await engine.transcribe(audio_content, language=language)
        return result
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """WebSocket 流式识别"""
    await websocket.accept()
    logger.info("WebSocket connection accepted")
    
    # 发送就绪消息
    await websocket.send_text(json.dumps({
        "type": "ready",
        "model": engine.current_model_name,
        "is_loading": engine.is_loading(),
    }))
    
    audio_buffer = bytearray()
    
    try:
        while True:
            message = await asyncio.wait_for(websocket.receive_text(), timeout=120.0)
            data = json.loads(message)
            msg_type = data.get("type")
            
            if msg_type == "audio":
                audio_b64 = data.get("data", "")
                if audio_b64:
                    audio_chunk = base64.b64decode(audio_b64)
                    audio_buffer.extend(audio_chunk)
                    
            elif msg_type in ("end", "stop"):
                if audio_buffer:
                    try:
                        result = await asyncio.wait_for(
                            engine.transcribe(bytes(audio_buffer)),
                            timeout=300.0
                        )
                        await websocket.send_text(json.dumps({
                            "type": "result",
                            "text": result.text,
                            "confidence": result.confidence,
                            "language": result.language,
                            "is_final": True,
                            "stt_latency_ms": result.stt_latency_ms,
                            "model": result.model,
                        }))
                    except asyncio.TimeoutError:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "error_code": "E5002",
                            "error_message": "Transcription timeout",
                        }))
                
                await websocket.send_text(json.dumps({"type": "done"}))
                break
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "error_code": "E5001",
                "error_message": str(e),
            }))
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass

def main():
    """主函数"""
    logger.info(f"Starting STT Service on {STT_HOST}:{STT_PORT}")
    uvicorn.run(
        app,
        host=STT_HOST,
        port=STT_PORT,
        log_level=_log_level.lower(),
    )

if __name__ == "__main__":
    main()
