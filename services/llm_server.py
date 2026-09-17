#!/usr/bin/env python3
"""
Voice Input Framework - LLM Service
独立的 LLM 后处理服务器,使用 mlx-lm 进行文本优化。
运行在现有的 mlx-test conda 环境 (transformers 5.x)
Port: 6545
"""

import asyncio
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 添加项目路径
project_dir = Path(__file__).parent.parent
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from shared.constants import (  # noqa: E402
    DEFAULT_BIND_HOST,
    DEFAULT_CORS_ORIGINS,
    MAX_PROCESS_TEXT_LENGTH,
)

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
    logger.info(f"Loading prompt from {PROMPT_FILE}")
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


class HealthStatus(BaseModel):
    status: str
    version: str = "1.0.0"
    uptime_seconds: float
    current_model: str
    loaded_models: list[str]
    active_connections: int = 0
    is_processing: bool = False


# ============== LLM Engine ==============


class LLMEngine:
    """LLM 引擎管理器"""

    AVAILABLE_MODELS = [
        # Qwen3.5 MLX 量化模型 (推荐，中文最强)
        "Qwen3.5-4B-OptiQ",  # 4B OptiQ 量化，~3GB 内存 (⭐ 推荐，平衡速度和精度)
        "Qwen3.5-2B-OptiQ",  # 速度与质量平衡，~2GB 内存
        "Qwen3.5-4B-MLX",  # 4B MLX 标准量化，~4GB 内存
        # Qwen3 MLX 量化模型 (成熟稳定)
        "Qwen3-0.6B",  # 最小 Qwen3 模型，~0.5GB 内存
        "Qwen3-1.7B",  # Qwen3 中等模型，~1.5GB 内存
        # Gemma 4 MLX (Google, 中文较弱)
        "Gemma-4-E4B-DECKARD",  # Gemma 4 4B 定制版
    ]

    MODEL_IDS = {
        # Qwen3.5 MLX 量化
        "Qwen3.5-4B-OptiQ": "mlx-community/Qwen3.5-4B-OptiQ-4bit",
        "Qwen3.5-2B-OptiQ": "mlx-community/Qwen3.5-2B-OptiQ-4bit",
        "Qwen3.5-4B-MLX": "mlx-community/Qwen3.5-4B-MLX-4bit",
        # Qwen3 MLX 量化
        "Qwen3-0.6B": "mlx-community/Qwen3-0.6B-4bit",
        "Qwen3-1.7B": "mlx-community/Qwen3-1.7B-4bit",
        # Gemma 4
        "Gemma-4-E4B-DECKARD": "nightmedia/gemma-4-E4B-it-The-DECKARD-V2-Strong-HERETIC-UNCENSORED-Instruct-mxfp8-mlx",
    }

    def __init__(self, default_model: str = "Qwen3.5-4B-OptiQ"):
        self.default_model = default_model
        self.current_model_name = default_model
        self._model = None
        self._tokenizer = None
        self._is_loaded = False
        self._loading = False
        self._load_lock = asyncio.Lock()
        self._processing = False
        # 生成在线程池执行,需用线程锁(而非 asyncio.Lock)串行化
        self._process_lock = threading.Lock()
        self.start_time = time.time()

    async def load(self, model_name: str | None = None) -> bool:
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
                # 切换模型前先释放旧模型内存(与 STT 侧一致,否则每次切换都泄漏一份权重)
                if self._model is not None:
                    self._release_model()
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

    def _release_model(self):
        """释放当前已加载的模型内存"""
        import gc

        self._is_loaded = False
        self._model = None
        self._tokenizer = None
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception as e:  # mlx 不可用时静默跳过
            logger.debug(f"MLX cache clear skipped: {e}")
        gc.collect()
        logger.info("Old LLM model memory released")

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

        # /process 在默认线程池执行,并发请求会同时命中同一个 MLX 模型实例
        # (生成状态非线程安全)并互相覆盖 _processing 标志 —— 这里串行化。
        with self._process_lock:
            self._processing = True
            try:
                return self._generate(text)
            finally:
                self._processing = False

    def _generate(self, text: str) -> ProcessResult:
        """实际的生成 + 输出清洗(调用方必须已持有 _process_lock)"""
        start_time = time.time()

        # 取本地引用:并发的模型切换会把 self._model 置空,
        # 本地引用可保证本次生成用完整的旧实例跑完。
        model, tokenizer = self._model, self._tokenizer
        if model is None or tokenizer is None:
            return ProcessResult(
                text=text,
                original_text=text,
                llm_latency_ms=0,
                model="",
                success=False,
            )

        try:
            import mlx_lm

            # 加载提示词
            system_prompt = load_prompt()
            logger.info(f"Using prompt: {system_prompt[:200]}...")

            # 构建消息
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ]

            prompt = tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

            # 移除可能触发思考的特殊标记
            prompt = prompt.replace("<think>", "")
            prompt = prompt.replace("</think>", "")

            # 生成
            response = mlx_lm.generate(
                model=model,
                tokenizer=tokenizer,
                prompt=prompt,
                max_tokens=256,
            )

            # 清理响应 - 移除思考标签
            import re

            # 移除 <think>...</think> 标签(DOTALL:思考块通常跨多行)
            cleaned = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
            # 移除单独的 <think> 或 </think> 标签
            cleaned = re.sub(r"</?think>", "", cleaned)
            # 处理 Thinking Process 或分析输出
            # 检查是否包含分析标记
            has_analysis = (
                "Thinking Process:" in cleaned
                or "Analyze the Request:" in cleaned
                or "Process the Input:" in cleaned
            )

            if has_analysis:
                # 方法1: 尝试找 "输出：" 或 "Output:" 后的内容
                for marker in ["输出：", "Output:", "Construct Output:", "Final Output:"]:
                    if marker in cleaned:
                        parts = cleaned.split(marker)
                        if len(parts) > 1:
                            # 取最后一个标记后的内容
                            potential = parts[-1].strip()
                            # 如果内容太长，可能还包含分析，继续分割
                            lines = potential.split("\n")
                            for line in lines:
                                line = line.strip()
                                # 找第一个实际的中文输出行（不是英文分析）
                                if (
                                    line
                                    and len(line) < 100
                                    and not line.startswith(
                                        ("1.", "2.", "3.", "4.", "5.", "*", "-")
                                    )
                                ):
                                    if not any(
                                        en in line
                                        for en in [
                                            "Analyze",
                                            "Process",
                                            "Rules:",
                                            "Task:",
                                            "Role:",
                                            "Input",
                                        ]
                                    ):
                                        cleaned = line
                                        break
                            break
                else:
                    # 方法2: 如果没有找到输出标记，从后往前找第一行中文
                    lines = cleaned.split("\n")
                    for line in reversed(lines):
                        line = line.strip()
                        # 找包含中文的行，且不是分析内容
                        if line and len(line) < 100:
                            if not line.startswith(("1.", "2.", "3.", "4.", "5.", "*", "-", "**")):
                                if not any(
                                    en in line
                                    for en in [
                                        "Analyze",
                                        "Process",
                                        "Rules:",
                                        "Task:",
                                        "Role:",
                                        "Input",
                                        "Thinking",
                                        "Construct",
                                    ]
                                ):
                                    cleaned = line
                                    break

            # 移除 markdown 标记和引号
            cleaned = cleaned.replace("**", "")
            cleaned = cleaned.replace('"', "")
            cleaned = cleaned.replace("'", "")
            cleaned = cleaned.strip()

            # 处理重复内容：如果有多行相同内容，只保留一行
            lines = cleaned.split("\n")
            unique_lines = []
            for line in lines:
                line = line.strip()
                if line and line not in unique_lines:
                    unique_lines.append(line)
            cleaned = " ".join(unique_lines)
            # 移除开头的 \nquirer 或 thinker
            cleaned = re.sub(r"^\s*(?:quirer|thinker)\s*", "", cleaned)
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
# 默认只绑定回环地址:本服务无鉴权,不应默认暴露到局域网。
LLM_HOST = os.getenv("VIF_LLM_HOST", DEFAULT_BIND_HOST)
LLM_PORT = int(os.getenv("VIF_LLM_PORT", "6545"))
LLM_MODEL = os.getenv("VIF_LLM_MODEL", "Qwen3.5-4B-OptiQ")
CORS_ORIGINS = [
    o.strip() for o in os.getenv("VIF_CORS_ORIGINS", "").split(",") if o.strip()
] or DEFAULT_CORS_ORIGINS

# 初始化引擎
engine = LLMEngine(default_model=LLM_MODEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期:启动时后台加载模型,关闭时清理(FastAPI 推荐用法)"""
    logger.info(f"Starting LLM Service on {LLM_HOST}:{LLM_PORT}")
    logger.info(f"Default model: {LLM_MODEL}")
    # 后台加载模型(非阻塞)
    asyncio.create_task(engine.load())
    yield
    logger.info("LLM Service shutting down")


app = FastAPI(
    title="Voice Input Framework - LLM Service",
    description="独立的文本后处理服务,使用 MLX-LM",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/models", response_model=list[ModelInfo])
async def list_models():
    """获取可用模型列表"""
    models = []
    for name in LLMEngine.AVAILABLE_MODELS:
        models.append(
            ModelInfo(
                name=name,
                description=f"LLM model: {LLMEngine.MODEL_IDS.get(name, name)}",
                is_loaded=(name == engine.current_model_name and engine.is_model_loaded()),
                is_current=(name == engine.current_model_name),
            )
        )
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
        if len(request.text) > MAX_PROCESS_TEXT_LENGTH:
            raise HTTPException(
                status_code=413,
                detail=f"Text too long: {len(request.text)} > {MAX_PROCESS_TEXT_LENGTH} chars",
            )
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


def main():
    """主函数"""
    from shared.version_check import check_python_version

    check_python_version()
    logger.info(f"Starting LLM Service on {LLM_HOST}:{LLM_PORT}")
    uvicorn.run(
        app,
        host=LLM_HOST,
        port=LLM_PORT,
        log_level=_log_level.lower(),
    )


if __name__ == "__main__":
    main()
