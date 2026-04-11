#!/usr/bin/env python3
"""
Voice Input Framework - LLM Service

独立的 LLM 后处理服务器，使用 mlx-lm 进行文本优化。
运行在现有的 mlx-test conda 环境 (transformers 5.x)

Port: 6545
"""

import asyncio
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
from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 配置日志
_log_level = os.getenv("VIF_LOG_LEVEL", "INFO").upper()
_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=_log_level, format=_log_format)
logger = logging.getLogger("llm-server")

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

class HealthStatus(BaseModel):
    status: str
    version: str = "1.0.0"
    uptime_seconds: float
    current_model: str
    loaded_models: List[str]
    active_connections: int = 0
    is_processing: bool = False

# ============== LLM Engine ==============

class LLMEngine:
    """LLM 引擎管理器"""
    
    AVAILABLE_MODELS = [
        "Qwen3.5-0.8B-OptiQ",
        "Qwen3.5-2B-OptiQ",
        "Qwen3-0.6B",
        "Qwen3-1.7B",
    ]
    
    MODEL_IDS = {
        "Qwen3.5-0.8B-OptiQ": "mlx-community/Qwen3.5-0.8B-OptiQ-4bit",
        "Qwen3.5-2B-OptiQ": "mlx-community/Qwen3.5-2B-OptiQ-4bit",
        "Qwen3-0.6B": "mlx-community/Qwen3-0.6B-4bit",
        "Qwen3-1.7B": "mlx-community/Qwen3-1.7B-4bit",
    }
    
    def __init__(self, default_model: str = "Qwen3.5-0.8B-OptiQ"):
        self.default_model = default_model
        self.current_model_name = default_model
        self._model = None
        self._tokenizer = None
        self._is_loaded = False
        self._loading = False
        self._load_lock = asyncio.Lock()
        self._processing = False
        self.start_time = time.time()
        
    async def load(self, model_name: Optional[str] = None) -> bool:
        """加载模型"""
        target_model = model_name or self.default_model
        model_id = self.MODEL_IDS.get(target_model)
        
        if not model_id:
            logger.error(f"Unknown model: {target_model}")
            return False
        
        async with self._load_lock:
            if self._is_loaded and self.current_model_name == target_model:
                return True
            
            if self._loading:
                logger.info("Model is loading, waiting...")
                while self._loading:
                    await asyncio.sleep(0.5)
                return self._is_loaded and self.current_model_name == target_model
            
            self._loading = True
            try:
                logger.info(f"Loading LLM model: {model_id}")
                loop = asyncio.get_event_loop()
                success = await loop.run_in_executor(None, self._load_sync, model_id)
                if success:
                    self.current_model_name = target_model
                    self._is_loaded = True
                    logger.info(f"LLM model loaded successfully: {target_model}")
                return success
            except Exception as e:
                logger.error(f"Failed to load LLM model: {e}")
                return False
            finally:
                self._loading = False
    
    def _load_sync(self, model_id: str) -> bool:
        """同步加载模型"""
        try:
            import mlx_lm
            self._model, self._tokenizer = mlx_lm.load(model_id)
            return True
        except Exception as e:
            logger.error(f"Load error: {e}")
            return False
    
    def process(self, text: str) -> ProcessResult:
        """处理文本"""
        if not self._is_loaded:
            return ProcessResult(
                text=text,
                original_text=text,
                llm_latency_ms=0,
                model="",
                success=False,
            )
        
        self._processing = True
        start_time = time.time()
        
        try:
            import mlx_lm
            
            # 构建提示词
            messages = [
                {"role": "user", "content": f"/no_think 优化STT识别结果，转为简洁的对话文案：移除填充词，保持原意，输出清晰准确的文本：{text}"}
            ]
            
            prompt = self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )
            
            # 移除 Qwen3.5 模板默认添加的思考标签
            mlx_think_end = chr(0x0a) + chr(0x3c) + 'think' + chr(0x3e) + chr(0x0a)
            if prompt.endswith(mlx_think_end):
                prompt = prompt[:-len(mlx_think_end)]
            
            # 生成
            response = mlx_lm.generate(
                model=self._model,
                tokenizer=self._tokenizer,
                prompt=prompt,
                max_tokens=128,
            )
            
            # 清理响应 - 移除思考标签
            import re
            # 移除 <think>...</think> 标签
            cleaned = re.sub(r'<think>[\\s\\S]*?</think>', '', response)
            # 移除单独的 <think> 或 </think> 标签
            cleaned = re.sub(r'</?think>', '', cleaned)
            # 移除开头的 \nquirer 或 thinker
            cleaned = re.sub(r'^\\s*(?:quirer|thinker)\\s*', '', cleaned)
            cleaned = cleaned.strip()
            
            latency = (time.time() - start_time) * 1000
            
            return ProcessResult(
                text=cleaned,
                original_text=text,
                llm_latency_ms=latency,
                model=self.current_model_name,
                success=True,
            )
            
        except Exception as e:
            logger.error(f"Process error: {e}")
            return ProcessResult(
                text=text,
                original_text=text,
                llm_latency_ms=-1,
                model=self.current_model_name,
                success=False,
            )
        finally:
            self._processing = False
    
    async def process_async(self, text: str) -> ProcessResult:
        """异步处理文本"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.process, text)
    
    def is_loading(self) -> bool:
        return self._loading
    
    def is_model_loaded(self) -> bool:
        return self._is_loaded
    
    def is_processing(self) -> bool:
        return self._processing

# ============== FastAPI App ==============

# 配置
LLM_HOST = os.getenv("VIF_LLM_HOST", "0.0.0.0")
LLM_PORT = int(os.getenv("VIF_LLM_PORT", "6545"))
LLM_MODEL = os.getenv("VIF_LLM_MODEL", "Qwen3.5-0.8B-OptiQ")

# 初始化引擎
engine = LLMEngine(default_model=LLM_MODEL)

app = FastAPI(
    title="Voice Input Framework - LLM Service",
    description="独立的文本后处理服务，使用 MLX-LM",
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
    logger.info(f"Starting LLM Service on {LLM_HOST}:{LLM_PORT}")
    logger.info(f"Default model: {LLM_MODEL}")
    # 后台加载模型（非阻塞）
    asyncio.create_task(engine.load())

@app.on_event("shutdown")
async def shutdown_event():
    """关闭时清理"""
    logger.info("LLM Service shutting down")

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
        is_processing=engine.is_processing(),
    )

@app.get("/models", response_model=List[ModelInfo])
async def list_models():
    """获取可用模型列表"""
    models = []
    for name in LLMEngine.AVAILABLE_MODELS:
        models.append(ModelInfo(
            name=name,
            description=f"LLM model: {LLMEngine.MODEL_IDS.get(name, name)}",
            is_loaded=(name == engine.current_model_name and engine.is_model_loaded()),
            is_current=(name == engine.current_model_name),
        ))
    return models

@app.post("/models/select")
async def select_model(model_name: str = Form(...)):
    """切换模型"""
    try:
        logger.info(f"Switching to model: {model_name}")
        success = await engine.load(model_name)
        return {
            "status": "success" if success else "failed",
            "current_model": engine.current_model_name,
            "is_loaded": engine.is_model_loaded(),
        }
    except Exception as e:
        logger.error(f"Error switching model: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/process", response_model=ProcessResult)
async def process_text(request: ProcessRequest):
    """处理文本"""
    try:
        if not engine.is_model_loaded():
            # 尝试加载
            loaded = await engine.load()
            if not loaded:
                raise HTTPException(status_code=503, detail="LLM model not loaded")
        
        result = await engine.process_async(request.text)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Process error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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
