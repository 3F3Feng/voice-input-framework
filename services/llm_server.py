#!/usr/bin/env python3
"""
Voice Input Framework - LLM Service
独立的 LLM 后处理服务器，支持多后端:
- MLX: Apple Silicon (mlx-lm)
- CUDA: NVIDIA GPU (transformers + bitsandbytes)

自动检测平台并选择最优后端。
Port: 6545
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import List

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 添加项目路径
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from shared.platform_detector import (
    detect_platform,
    get_startup_banner,
    check_resource_requirements,
)
from server.llm_engine import LLMEngine

# 配置日志
_log_level = os.getenv("VIF_LOG_LEVEL", "INFO").upper()
_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=_log_level, format=_log_format)
logger = logging.getLogger("llm-server")

# ============== Prompt Configuration ==============
PROMPT_FILE = Path.home() / ".config" / "voice-input-framework" / "llm_prompt.json"
PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)

# 默认提示词
DEFAULT_PROMPT = """你是一个语音输入后处理助手。

将用户的语音转文字进行优化：
1. 移除填充词（"那个啥"、"就是吧"等）
2. 保持原意
3. 添加标点符号
4. 输出简洁版本

只返回优化后的文本，不要额外解释。"""


def load_prompt() -> str:
    """加载提示词"""
    if PROMPT_FILE.exists():
        try:
            return PROMPT_FILE.read_text()
        except Exception as e:
            logger.warning(f"Failed to load prompt file: {e}")
    return DEFAULT_PROMPT


def save_prompt(prompt: str) -> bool:
    """保存提示词"""
    try:
        PROMPT_FILE.write_text(prompt)
        return True
    except Exception as e:
        logger.error(f"Failed to save prompt file: {e}")
        return False


# ============== Data Models ==============


class ProcessRequest(BaseModel):
    text: str
    options: dict = {}


class ProcessResult(BaseModel):
    text: str
    original_text: str
    llm_latency_ms: float
    model: str
    success: bool = True


class ModelInfo(BaseModel):
    name: str
    description: str = ""
    is_loaded: bool = False
    is_current: bool = False
    backend: str = ""
    memory_gb: float = 0.0


class HealthStatus(BaseModel):
    status: str
    version: str = "2.0.0"
    uptime_seconds: float
    current_model: str
    loaded_models: List[str]
    active_connections: int = 0
    is_processing: bool = False
    platform: dict = {}


# ============== FastAPI App ==============

# 配置
LLM_HOST = os.getenv("VIF_LLM_HOST", "0.0.0.0")
LLM_PORT = int(os.getenv("VIF_LLM_PORT", "6545"))
LLM_MODEL = os.getenv("VIF_LLM_MODEL", "")  # 空字符串表示使用默认模型

# 平台检测
platform_info = detect_platform()

# 初始化引擎
engine = LLMEngine(platform_info)

# 如果指定了模型，使用指定的模型；否则使用默认模型
if LLM_MODEL:
    default_model = LLM_MODEL
else:
    default_model = engine.get_default_model()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    extra_info = {
        "Default LLM Model": default_model or "(none - no GPU)",
        "Backend": platform_info.best_backend,
    }
    banner = get_startup_banner("LLM Service", extra_info)
    logger.info(banner)

    # 资源检查
    if default_model:
        from server.llm_engine import LLM_MODELS_CONFIG

        model_config = LLM_MODELS_CONFIG.get(default_model, {})
        required_memory = model_config.get("memory_gb", 0)
        resource_result = check_resource_requirements(default_model, required_memory)

        if resource_result["passed"]:
            logger.info("✓ Resource check passed")
        else:
            for warning in resource_result["warnings"]:
                logger.warning(f"⚠️  {warning}")

    # 预加载配置
    preload = os.getenv("VIF_PRELOAD_MODELS", "stt").lower()
    if preload == "none" or preload == "stt":
        logger.info(f"LLM preload skipped (VIF_PRELOAD_MODELS={preload})")
        logger.info("LLM will load on first request")
    elif preload == "all":
        if default_model:
            logger.info("Preloading LLM model...")
            asyncio.create_task(engine.load_model(default_model))
        else:
            logger.warning("No default LLM model available for this platform")
    else:
        logger.info(f"Unknown preload option: {preload}, skipping LLM preload")

    yield

    # Shutdown
    logger.info("LLM Service shutting down")
    await engine.unload()


app = FastAPI(
    title="Voice Input Framework - LLM Service",
    description="独立的文本后处理服务，支持 MLX (Apple Silicon) 和 CUDA (NVIDIA) 后端",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthStatus)
async def health_check():
    """健康检查"""
    return HealthStatus(
        status="ok" if engine.is_loaded else "loading",
        version="2.0.0",
        uptime_seconds=time.time() - engine.start_time,
        current_model=engine.current_model,
        loaded_models=[engine.current_model] if engine.is_loaded else [],
        active_connections=0,
        is_processing=engine.is_processing,
        platform={
            "system": platform_info.system,
            "arch": platform_info.arch,
            "backend": platform_info.best_backend,
            "gpu": platform_info.gpu_info,
        },
    )


@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """获取可用模型列表"""
    available = engine.get_available_models()
    models = []
    for name, config in available.items():
        models.append(
            ModelInfo(
                name=name,
                description=config.get("description", ""),
                is_loaded=(name == engine.current_model and engine.is_loaded),
                is_current=(name == engine.current_model),
                backend=config.get("backend", ""),
                memory_gb=config.get("memory_gb", 0.0),
            )
        )
    return models


@app.post("/models/select")
async def select_model(model_name: str = Form(...)):
    """切换模型"""
    try:
        logger.info(f"Switching to model: {model_name}")
        success = await engine.load_model(model_name)
        return {
            "status": "success" if success else "failed",
            "current_model": engine.current_model,
            "is_loaded": engine.is_loaded,
        }
    except Exception as e:
        logger.error(f"Error switching model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/process", response_model=ProcessResult)
async def process_text(request: ProcessRequest):
    """处理文本"""
    try:
        if not engine.is_loaded:
            # 尝试加载
            if default_model:
                loaded = await engine.load_model(default_model)
                if not loaded:
                    raise HTTPException(status_code=503, detail="LLM model not loaded")
            else:
                raise HTTPException(
                    status_code=503, detail="No LLM model available for this platform"
                )

        # 加载提示词
        system_prompt = load_prompt()

        # 处理文本
        result_text, latency_ms = await engine.process(request.text, system_prompt)

        return ProcessResult(
            text=result_text,
            original_text=request.text,
            llm_latency_ms=latency_ms,
            model=engine.current_model,
            success=True,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Process error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============== Prompt Management API ==============
@app.get("/prompt")
async def get_prompt():
    """获取当前提示词"""
    return {"prompt": load_prompt()}


@app.put("/prompt")
async def update_prompt(request: Request):
    """更新提示词"""
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    if save_prompt(prompt):
        logger.info(f"Prompt updated successfully ({len(prompt)} chars)")
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to save prompt")


# ============== Platform Info API ==============
@app.get("/platform")
async def get_platform():
    """获取平台信息"""
    return {
        "system": platform_info.system,
        "arch": platform_info.arch,
        "python_version": platform_info.python_version,
        "backend": platform_info.best_backend,
        "gpu": {
            "name": platform_info.cuda_device.name if platform_info.cuda_device else None,
            "memory_gb": platform_info.cuda_device.memory_gb if platform_info.cuda_device else 0,
            "driver": platform_info.cuda_device.driver_version
            if platform_info.cuda_device
            else None,
            "cuda_version": platform_info.cuda_device.cuda_version
            if platform_info.cuda_device
            else None,
        }
        if platform_info.has_cuda
        else None,
        "cpu": {
            "cores": platform_info.cpu_cores,
            "ram_gb": round(platform_info.ram_gb, 1),
        },
        "capabilities": {
            "mlx": platform_info.has_mlx,
            "cuda": platform_info.has_cuda,
            "mps": platform_info.has_mps,
        },
        "recommended_model": engine.get_default_model(),
        "available_models": list(engine.get_available_models().keys()),
    }


def main():
    """主函数"""
    logger.info(f"Starting LLM Service on {LLM_HOST}:{LLM_PORT}")
    uvicorn.run(
        app,
        host=LLM_HOST,
        port=LLM_PORT,
        log_level=_log_level.lower(),
    )


if __name__ == "__main__":
    main()
