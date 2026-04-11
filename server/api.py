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

from server.config import get_default_config, LLMConfig
from server.stt_engine import STTEngineManager
from server.llm_engine import LLMManager
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

# 初始化 LLM 管理器
llm_config = config.llm if hasattr(config, 'llm') else LLMConfig()
llm_manager = LLMManager.get_instance(llm_config)

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

    # 初始化 LLM
    llm_engine = llm_manager.get_engine()
    if llm_engine and llm_engine.is_loaded:
        logger.info(f"LLM initialized: {llm_engine.current_model}")
    else:
        logger.warning("LLM not initialized or failed to load")


@app.on_event("shutdown")
async def shutdown_event():
    """服务停止回调"""
    await engine_manager.shutdown()
    llm_manager.unload()
    logger.info("Service shutdown and models unloaded")


@app.get("/health", response_model=HealthStatus)
async def health_check():
    """健康检查"""
    llm_engine = llm_manager.get_engine()
    return HealthStatus(
        status="ok",
        version="1.0.0",
        uptime_seconds=time.time() - start_time,
        current_model=engine_manager.current_model_name,
        loaded_models=list(engine_manager.engines.keys()),
        active_connections=0,
        llm_model=llm_engine.current_model if llm_engine else "",
        llm_enabled=llm_config.enabled,
    )


@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """获取可用模型列表"""
    return await engine_manager.list_models()


@app.get("/llm/models")
async def list_llm_models():
    """获取可用LLM模型列表"""
    models = llm_manager.get_available_models()
    current = llm_manager.get_current_model()
    return {
        "models": models,
        "current_model": current,
        "enabled": llm_config.enabled,
    }


@app.post("/llm/models/select")
async def select_llm_model(model_name: str = Form(...)):
    """选择当前使用的LLM模型"""
    try:
        logger.info(f"Switching LLM model to: {model_name}")

        success = llm_manager.switch_model(model_name)
        current = llm_manager.get_current_model()

        return {
            "status": "success" if success else "failed",
            "message": f"Switched to {model_name}" if success else f"Failed to switch to {model_name}",
            "current_model": current,
            "is_loaded": llm_manager.get_engine().is_loaded if llm_manager.get_engine() else False,
        }
    except Exception as e:
        logger.error(f"Error switching LLM model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/models/select")
async def select_model(model_name: str = Form(...)):
    """选择当前使用的模型（立即返回，后台加载）"""
    try:
        logger.info(f"Attempting to switch to model: {model_name}")

        # 立即返回，不等待模型加载
        await engine_manager.switch_model(model_name)

        is_loading = engine_manager.is_model_loading(model_name)
        is_loaded = model_name in engine_manager.engines

        return {
            "status": "success",
            "message": f"Switched to {model_name}",
            "current_model": engine_manager.current_model_name,
            "is_loading": is_loading,
            "is_loaded": is_loaded,
            "note": "Model is loading in background if not already loaded"
        }
    except ValueError as e:
        logger.error(f"ValueError switching model: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error switching model: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")


@app.get("/models/status/{model_name}")
async def model_status(model_name: str):
    """获取指定模型的状态"""
    is_loaded = model_name in engine_manager.engines
    is_loading = engine_manager.is_model_loading(model_name)
    loading_time = engine_manager.get_model_loading_time(model_name)
    is_current = model_name == engine_manager.current_model_name

    return {
        "model_name": model_name,
        "is_loaded": is_loaded,
        "is_loading": is_loading,
        "loading_since": loading_time,
        "is_current": is_current
    }


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

        # LLM 后处理
        if llm_config.enabled and result.text:
            llm_engine = llm_manager.get_engine()
            if llm_engine and llm_engine.is_loaded:
                processed_text, llm_latency = llm_engine.process(result.text)
                result.text = processed_text

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

    # 发送就绪消息（不立即获取 engine，因为模型可能还在加载）
    current_model = engine_manager.current_model_name
    is_loading = engine_manager.is_model_loading(current_model)
    llm_engine = llm_manager.get_engine()

    await websocket.send_text(json.dumps({
        "type": "ready",
        "model": current_model,
        "is_loading": is_loading,
        "llm_model": llm_engine.current_model if llm_engine else "",
        "llm_enabled": llm_config.enabled,
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
                message = await asyncio.wait_for(websocket.receive_text(), timeout=120.0)  # 增加超时时间到 120 秒
                data = json.loads(message)
                msg_type = data.get("type")

                logger.info(f"Received message type: {msg_type}")

                if msg_type == "audio":
                    audio_b64 = data.get("data", "")
                    if audio_b64:
                        audio_chunk = base64.b64decode(audio_b64)
                        audio_buffer.extend(audio_chunk)
                        logger.info(f"Received audio chunk: {len(audio_chunk)} bytes, total buffer: {len(audio_buffer)} bytes")

                elif msg_type == "llm_status":
                    # 客户端请求 LLM 状态
                    llm_eng = llm_manager.get_engine()
                    await websocket.send_text(json.dumps({
                        "type": "llm_status",
                        "llm_model": llm_eng.current_model if llm_eng else "",
                        "llm_enabled": llm_config.enabled,
                        "llm_processing": llm_eng.is_processing if llm_eng else False,
                    }))

                elif msg_type == "llm_switch":
                    # 客户端切换 LLM 模型
                    model_name = data.get("model", "")
                    if model_name:
                        success = llm_manager.switch_model(model_name)
                        llm_eng = llm_manager.get_engine()
                        await websocket.send_text(json.dumps({
                            "type": "llm_switch_result",
                            "success": success,
                            "llm_model": llm_eng.current_model if llm_eng else "",
                        }))

                elif msg_type in ("end", "stop"):
                    logger.info(f"Received end signal, processing {len(audio_buffer)} bytes of audio")
                    if audio_buffer:
                        # 每次处理音频时获取当前 engine（支持动态切换）
                        engine = await engine_manager.get_current_engine()
                        if not engine:
                            raise RuntimeError("No engine available")

                        try:
                            # qwen_asr 模型很大，增加处理超时时间
                            stt_start = time.time()
                            result = await asyncio.wait_for(
                                engine.transcribe(bytes(audio_buffer)),
                                timeout=300.0  # 5 分钟超时
                            )
                            stt_latency = (time.time() - stt_start) * 1000
                            logger.info(f"STT result: {result.text}, latency: {stt_latency:.0f}ms")

                            # LLM 后处理
                            final_text = result.text
                            llm_latency = 0
                            if llm_config.enabled and result.text:
                                llm_eng = llm_manager.get_engine()
                                if llm_eng and llm_eng.is_loaded:
                                    # 通知客户端 LLM 开始处理
                                    await websocket.send_text(json.dumps({
                                        "type": "llm_start",
                                        "text": result.text,
                                    }))

                                    llm_start = time.time()
                                    processed_text, llm_latency = llm_eng.process(result.text)
                                    final_text = processed_text
                                    total_llm_time = (time.time() - llm_start) * 1000

                                    logger.info(f"LLM processed: {final_text}, llm_latency: {llm_latency:.0f}ms, total_llm_time: {total_llm_time:.0f}ms")

                            await websocket.send_text(json.dumps({
                                "type": "result",
                                "text": final_text,
                                "confidence": result.confidence,
                                "language": result.language,
                                "is_final": True,
                                "stt_latency_ms": stt_latency,
                                "llm_latency_ms": llm_latency if llm_latency > 0 else None,
                                "llm_model": llm_eng.current_model if llm_eng and llm_eng.is_loaded else None,
                            }))
                        except asyncio.TimeoutError:
                            logger.error("Transcription timeout")
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "error_code": "E5002",
                                "error_message": "Transcription timeout - model may still be loading"
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
    logger.info(f"LLM enabled: {llm_config.enabled}, default LLM: {llm_config.default_model}")
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
