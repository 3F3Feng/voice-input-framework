"""
Voice Input Framework - FastAPI 服务

提供 REST API 和 WebSocket 接口。
"""

import asyncio
import logging
import time
from typing import List

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from voice_input_framework.server.config import get_default_config
from voice_input_framework.server.stt_engine import STTEngineManager
from voice_input_framework.shared.protocol import (
    ErrorCode,
    MessageType,
    StreamRequest,
    StreamResponse,
)
from voice_input_framework.shared.types import HealthStatus, ModelInfo

# 配置日志
logging.basicConfig(level=logging.INFO)
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
        active_connections=0,  # 简化处理
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
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    engine = await engine_manager.get_current_engine()

    async def audio_generator():
        """从 WebSocket 接收音频数据的生成器"""
        try:
            while True:
                message = await websocket.receive_text()
                request = StreamRequest.from_json(message)

                if request.type == MessageType.AUDIO_CHUNK:
                    if request.data:
                        yield request.data
                elif request.type == MessageType.CONTROL:
                    if request.control == "stop":
                        break
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            logger.error(f"Error in audio generator: {e}")

    try:
        # 使用流式接口
        async for result in engine.transcribe_stream(audio_generator()):
            response = StreamResponse(
                type=MessageType.TRANSCRIPTION,
                text=result.text,
                confidence=result.confidence,
                language=result.language,
                is_final=result.is_final
            )
            await websocket.send_text(response.to_json())

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        error_response = StreamResponse(
            type=MessageType.ERROR,
            error_code=ErrorCode.INTERNAL_ERROR.value,
            error_message=str(e)
        )
        await websocket.send_text(error_response.to_json())
    finally:
        try:
            await websocket.close()
        except:
            pass


def main():
    """主函数"""
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
