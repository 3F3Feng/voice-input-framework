#!/usr/bin/env python3
"""
Voice Input Framework - FastAPI 服务

提供 REST API 和 WebSocket 接口。
支持常驻运行和开机自启动。
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List

# 添加项目路径
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from server.config import get_default_config
from server.stt_engine import STTEngineManager
from shared.protocol import (
    ErrorCode,
    MessageType,
    StreamRequest,
    StreamResponse,
)
from shared.data_types import HealthStatus, ModelInfo

# 配置日志
_log_level = os.getenv("VIF_LOG_LEVEL", "INFO").upper()
_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# 支持日志输出到文件
_log_file = os.getenv("VIF_LOG_FILE")
if _log_file:
    _log_dir = Path(_log_file).parent
    _log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=_log_level, format=_log_format, handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler(sys.stdout)
    ])
else:
    logging.basicConfig(level=_log_level, format=_log_format)

logger = logging.getLogger(__name__)

# 初始化配置和管理器
config = get_default_config()
engine_manager = STTEngineManager(config)

app = FastAPI(title="Voice Input Framework API")

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

start_time = time.time()


@app.on_event("startup")
async def startup_event():
    """服务启动回调"""
    await engine_manager.initialize()
    logger.info("Service started and default model initialized")


@app.on_event("shutdown")
async def shutdown_event():
    """服务停止回调"""
    await engine_manager.shutdown()
    logger.info("Service shutdown and models unloaded")


@app.get("/health", response_model=HealthStatus)
async def health_check():
    """健康检查"""
    return HealthStatus(
        status="ok",
        version="1.0.0",
        uptime_seconds=time.time() - start_time,
        current_model=engine_manager.current_model_name,
        loaded_models=list(engine_manager.engines.keys()),
        active_connections=0,
    )


@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """获取可用模型列表"""
    return await engine_manager.list_models()


@app.post("/models/select")
async def select_model(model_name: str = Form(...)):
    """选择当前使用的模型"""
    try:
        await engine_manager.switch_model(model_name)
        return {"status": "success", "current_model": model_name}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(None),
    language: str = Form("auto")
):
    """文件上传转写"""
    try:
        audio_content = await file.read()
        result = await engine_manager.transcribe(audio_content, model_name=model)
        return result.to_dict()
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/stream")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 流式识别接口"""
    import base64
    
    await websocket.accept()
    logger.info("WebSocket connection accepted")
    
    engine = await engine_manager.get_current_engine()
    
    # 发送就绪消息
    await websocket.send_text(json.dumps({
        "type": "ready",
        "model": engine.model_name if engine else "unknown"
    }))
    
    try:
        # 首先接收配置消息
        try:
            config_msg = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            config_data = json.loads(config_msg)
            logger.info(f"Received config: {config_data}")
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for config message")
            await websocket.close()
            return
        
        # 音频缓冲区
        audio_buffer = bytearray()
        
        # 主循环：接收音频块
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                data = json.loads(message)
                msg_type = data.get("type")
                
                logger.info(f"Received message type: {msg_type}")
                
                if msg_type == "audio":
                    audio_b64 = data.get("data", "")
                    if audio_b64:
                        audio_chunk = base64.b64decode(audio_b64)
                        audio_buffer.extend(audio_chunk)
                        logger.info(f"Received audio chunk: {len(audio_chunk)} bytes, total buffer: {len(audio_buffer)} bytes")
                        
                elif msg_type in ("end", "stop"):
                    logger.info(f"Received end signal, processing {len(audio_buffer)} bytes of audio")
                    if audio_buffer:
                        result = await engine.transcribe(bytes(audio_buffer))
                        logger.info(f"Transcription result: {result.text}")
                        await websocket.send_text(json.dumps({
                            "type": "result",
                            "text": result.text,
                            "confidence": result.confidence,
                            "language": result.language,
                            "is_final": True,
                        }))
                    
                    await websocket.send_text(json.dumps({"type": "done"}))
                    logger.info("Sent done, closing connection")
                    break
                    
            except asyncio.TimeoutError:
                logger.error("Timeout waiting for message")
                break
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received: {e}")
            except Exception as e:
                logger.error(f"Error in websocket loop: {e}")
                try:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "error_code": "E5001",
                        "error_message": str(e)
                    }))
                except:
                    pass
                break
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "error_code": "E5001",
                "error_message": str(e)
            }))
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass


def main():
    """主函数 - 支持常驻运行"""
    # 从环境变量读取配置
    host = os.getenv("VIF_HOST", config.host)
    port = int(os.getenv("VIF_PORT", config.port))
    
    logger.info(f"Starting Voice Input Framework server on {host}:{port}")
    logger.info(f"Default model: {config.default_model}")
    logger.info(f"Log level: {_log_level}")
    
    # uvicorn 配置
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=_log_level.lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
