# Windows 4090 服务端迁移方案

> 将当前 Apple Silicon MLX 驱动的 STT + LLM 服务端迁移到 NVIDIA RTX 4090（Windows）

## 背景

当前服务端架构在 `dev/llm-postprocessing-v2` 分支上，采用分离架构：

| 服务 | 端口 | 运行时 |
|------|------|--------|
| STT Service | 6544 | `vif-stt` conda env |
| LLM Service | 6545 | `mlx-test` conda env |

两个服务深度依赖 Apple Silicon 专属库：
- **STT**: `mlx-audio` → 加载 `mlx-community/Qwen3-ASR-1.7B-8bit`
- **LLM**: `mlx-lm` → 加载 `mlx-community/Qwen3.5-4B-OptiQ-4bit`

目标：迁移到 Windows 4090，使用 **CUDA** 加速，保持架构不变，**STT 仍用 Qwen ASR 系列**。

---

## 目录

1. [架构总览](#1-架构总览)
2. [环境搭建](#2-环境搭建)
3. [STT 引擎迁移](#3-stt-引擎迁移)
4. [LLM 引擎迁移](#4-llm-引擎迁移)
5. [模型注册表修改](#5-模型注册表修改)
6. [自启动与守护](#6-自启动与守护)
7. [验证与基准测试](#7-验证与基准测试)
8. [对比预期](#8-对比预期)
9. [迁移路线图](#9-迁移路线图)
10. [附录：依赖汇总](#10-附录依赖汇总)

---

## 1. 架构总览

### 迁移前（Mac Silicon）

```
┌─ 客户端 (任何平台) ─────────────────────┐
│  WebSocket / HTTP  →  服务器地址:6543     │
└──────────────────────────────────────────┘
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
┌─ STT Service (6544) ─┐ ┌─ LLM Service (6545) ─────┐
│  mlx-audio            │ │  mlx-lm                  │
│  mlx-community/       │ │  mlx-community/          │
│  Qwen3-ASR-1.7B-8bit │ │  Qwen3.5-4B-OptiQ-4bit   │
│  qwen_asr_mlx_native  │ │  evaluator.py            │
└───────────────────────┘ └──────────────────────────┘
```

### 迁移后（Windows 4090）

```
┌─ 客户端 (任何平台) ─────────────────────┐
│  WebSocket / HTTP  →  服务器地址:6543     │
└──────────────────────────────────────────┘
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
┌─ STT Service (6544) ─┐ ┌─ LLM Service (6545) ─────┐
│  torch + CUDA         │ │  torch + CUDA            │
│  Flash Attention 2    │ │  Flash Attention 2       │
│  Qwen/Qwen3-ASR-1.7B  │ │  Qwen/Qwen3.5-4B        │
│  qwen3_asr_cuda       │ │  可选项: vLLM / exllamav2│
└───────────────────────┘ └──────────────────────────┘
```

---

## 2. 环境搭建

### 2.1 NVIDIA 驱动

```powershell
# 推荐版本
# NVIDIA 驱动 ≥ 550.0（支持 CUDA 12.4+）
# 验证
nvidia-smi
```

### 2.2 Conda 环境（保持分离架构）

#### STT 环境 (`vif-stt`)

```powershell
conda create -n vif-stt python=3.11 -y
conda activate vif-stt

# CUDA 版 PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# 安装 Qwen ASR 包
pip install qwen_asr>=0.0.6

# 可选优化
pip install flash-attn  # Flash Attention 2（需编译环境）
pip install bitsandbytes  # int8 量化
```

`requirements-stt-win.txt`:
```
# ============== Web 框架 ==============
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
python-multipart>=0.0.6
pydantic>=2.5.0

# ============== HTTP 客户端 ==============
httpx>=0.26.0

# ============== 音频处理 ==============
numpy>=1.26.0

# ============== STT 模型 ==============
qwen_asr>=0.0.6
transformers>=4.37.0,<5.0

# ============== CUDA 优化 ==============
torch>=2.5.0
torchaudio>=2.5.0

# ============== 日志 ==============
rich>=13.7.0

# ============== WebSocket ==============
websockets>=12.0
```

#### LLM 环境 (`vif-llm`)

```powershell
conda create -n vif-llm python=3.11 -y
conda activate vif-llm

# CUDA 版 PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# transformers
pip install transformers>=4.37.0

# 可选
pip install flash-attn
pip install bitsandbytes accelerate
```

`requirements-llm-win.txt`:
```
# ============== Web 框架 ==============
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
pydantic>=2.5.0

# ============== LLM 推理 ==============
transformers>=4.37.0
accelerate>=0.28.0

# ============== CUDA ==============
torch>=2.5.0

# ============== 日志 ==============
rich>=13.7.0
```

### 2.3 验证 CUDA

```powershell
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}, Device: {torch.cuda.get_device_name()}, VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f}GB')"
# 期望输出: CUDA: True, Device: NVIDIA GeForce RTX 4090, VRAM: 24.0GB
```

---

## 3. STT 引擎迁移

### 3.1 新建 CUDA 引擎

创建 `server/models/qwen3_asr_cuda.py`：

```python
#!/usr/bin/env python3
"""
Voice Input Framework - CUDA Qwen3-ASR 引擎 (4090 优化)

替代 MLX 版 qwen3_asr_mlx_native.py。
使用 Qwen ASR 官方包 + PyTorch CUDA 推理。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np
import torch

from server.models.base import BaseSTTEngine, STTEngineError
from shared.data_types import TranscriptionResult

logger = logging.getLogger(__name__)


class Qwen3ASRCudaEngine(BaseSTTEngine):
    """CUDA 版 Qwen3-ASR 引擎（4090 优化）"""

    MODEL_CONFIGS = {
        "qwen_asr": {
            "model_id": "Qwen/Qwen3-ASR-1.7B",
            "memory_gb": 3.5,  # FP16
            "dtype": torch.float16,
            "description": "Qwen3-ASR-1.7B CUDA (推荐)",
        },
        "qwen_asr_small": {
            "model_id": "Qwen/Qwen3-ASR-0.6B",
            "memory_gb": 1.5,
            "dtype": torch.float16,
            "description": "Qwen3-ASR-0.6B CUDA (更快)",
        },
    }

    def __init__(self, model_name: str = "qwen_asr", **kwargs):
        super().__init__(model_name, **kwargs)
        self._model = None
        self._processor = None
        self.model_config = self.MODEL_CONFIGS.get(
            model_name, self.MODEL_CONFIGS["qwen_asr"]
        )
        self._device = None

    async def load(self) -> None:
        if self._is_loaded:
            return
        logger.info(f"Loading CUDA model: {self.model_config['model_id']}")
        try:
            self._load_sync()
            self._is_loaded = True
            logger.info(
                f"Model loaded on {self._device}: {self.model_config['model_id']}"
            )
        except Exception as e:
            raise STTEngineError(f"Failed to load CUDA model: {e}")

    def _load_sync(self):
        from transformers import AutoModelForCausalLM, AutoProcessor

        model_id = self.model_config["model_id"]

        if not torch.cuda.is_available():
            raise STTEngineError("CUDA is not available")

        self._device = torch.device("cuda")

        self._processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)

        self._model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=self.model_config["dtype"],
            device_map="cuda:0",  # 强制加载到 GPU 0
            attn_implementation="flash_attention_2",  # FLASH ATTENTION 2（需要 flash-attn）
            trust_remote_code=True,
        )

        # 验证模型在 GPU 上
        for param in self._model.parameters():
            logger.debug(f"Parameter device: {param.device}")
            break

    async def unload(self) -> None:
        if not self._is_loaded:
            return
        self._model = None
        self._processor = None
        self._device = None
        torch.cuda.empty_cache()
        self._is_loaded = False

    def _convert_audio(self, audio_data: bytes, sample_rate: int = 16000) -> np.ndarray:
        """将音频数据转换为 float32 numpy 数组 (16kHz mono)"""
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        audio_array = audio_array.astype(np.float32) / 32768.0

        # 重采样到 16kHz
        if sample_rate != 16000:
            target_length = int(len(audio_array) * 16000 / sample_rate)
            audio_array = np.interp(
                np.linspace(0, len(audio_array), target_length),
                np.arange(len(audio_array)),
                audio_array,
            )

        return audio_array

    async def transcribe(
        self,
        audio_data: bytes = b"",
        language: str = "zh",
        sample_rate: int = 16000,
        audio: tuple = None,
    ) -> TranscriptionResult:
        """转写音频

        对比 MLX 版本:
        - CUDA 上使用 Qwen ASR 官方 processor + generate
        - 支持 Flash Attention 2 加速
        - 支持 int8 量化（可选）
        """
        if not self._is_loaded:
            await self.load()

        if audio is not None:
            audio_array = audio[0]
        else:
            audio_array = self._convert_audio(audio_data, sample_rate)

        lang_param = language if language != "auto" else None

        try:
            # 使用 Qwen ASR processor 处理音频
            inputs = self._processor(
                audios=audio_array,
                sampling_rate=16000,
                return_tensors="pt",
                language=lang_param,
            ).to(self._device)

            # 推理（使用 torch.no_grad 减少显存占用）
            with torch.no_grad():
                generated_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=256,
                    temperature=0.0,
                    do_sample=False,
                )

            # 解码
            text = self._processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )[0]

            # 清理 ASR 系统返回的前缀
            text = text.strip()
            if text.startswith("ASSISTANT: "):
                text = text[len("ASSISTANT: ") :]
            elif text.startswith("assistant: "):
                text = text[len("assistant: ") :]

            return TranscriptionResult(
                text=text,
                confidence=1.0,
                language=language,
                is_final=True,
            )

        except Exception as e:
            logger.error(f"Transcription error: {e}")
            raise STTEngineError(f"Transcription failed: {e}")

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        language: str = "zh",
        sample_rate: int = 16000,
    ) -> AsyncIterator[TranscriptionResult]:
        """流式转写

        保持与 MLX 版相同的 buffer 策略，后续可优化为更细粒度的流式。
        """
        if not self._is_loaded:
            await self.load()

        buffer = []
        async for chunk in audio_stream:
            buffer.append(chunk)
            if len(buffer) >= 5:
                combined = b"".join(buffer)
                result = await self.transcribe(
                    audio_data=combined,
                    language=language,
                    sample_rate=sample_rate,
                )
                if result.text.strip():
                    yield TranscriptionResult(
                        text=result.text.strip(),
                        confidence=result.confidence,
                        language=result.language,
                        is_final=False,
                    )
                buffer = []

        # 处理剩余 buffer
        if buffer:
            combined = b"".join(buffer)
            result = await self.transcribe(
                audio_data=combined,
                language=language,
                sample_rate=sample_rate,
            )
            if result.text.strip():
                yield TranscriptionResult(
                    text=result.text.strip(),
                    confidence=result.confidence,
                    language=result.language,
                    is_final=True,
                )

    def get_model_info(self) -> dict:
        info = super().get_model_info()
        info.update({
            "model_id": self.model_config.get("model_id", "unknown"),
            "description": self.model_config.get("description", ""),
            "device": str(self._device) if self._device else "unloaded",
        })
        return info
```

### 3.2 注册 CUDA 引擎

修改 `server/models/__init__.py`，添加：

```python
from server.models.qwen3_asr_cuda import Qwen3ASRCudaEngine

_ENGINE_CLASSES = {
    # ... 保留原有引擎
    "qwen_asr_cuda": Qwen3ASRCudaEngine,  # 新增
}
```

### 3.3 STT 性能优化清单

| 优化 | 说明 | 代码 |
|------|------|------|
| Flash Attention 2 | 长序列 attention 加速 ~20-30% | `attn_implementation="flash_attention_2"` |
| torch.compile | 图编译 ~10-20% | `model = torch.compile(model)`（需要 PyTorch 2.5+） |
| int8 量化 | 显存省 50%，速度持平 | `load_in_8bit=True` |
| 半精度推理 | FP16 vs FP32，2x 显存省 | `torch_dtype=torch.float16` |
| 模型常驻 | 不走 unload，消除加载延迟 | 引擎启动时加载，退出时释放 |
| uint8 缓存 | 减少音频数据在 CPU/GPU 间传输 | 可后续优化 |

---

## 4. LLM 引擎迁移

### 4.1 evaluator.py 适配

当前 `llm_postprocessing/evaluator.py` 已有 transformers fallback 路径：

```python
# 现有逻辑（无需大量改动）：
try:
    import mlx_lm
    # ... MLX 路径（Windows 上会 ImportError）
except ImportError:
    return self._load_with_transformers()  # ← 自然的 fallback
```

**需要的修改**：

#### 4.1.1 适配模型列表

LLM 模型注册表在 `llm_postprocessing/model_registry.py`，当前全用 `mlx-community/` 前缀：

```python
# 当前（MLX only）：
"mlx-community/Qwen3.5-4B-OptiQ-4bit"
"mlx-community/Qwen3.5-2B-OptiQ-4bit"
"mlx-community/Qwen3-1.7B-4bit"

# 需要增加 CUDA 版本：
"Qwen/Qwen3.5-4B"              # 官方原版，~8GB FP16
"Qwen/Qwen3.5-2B"              # ~4GB FP16
"Qwen/Qwen3-1.7B"              # ~3.5GB FP16
```

#### 4.1.2 模型选择策略

新增 CUDA 兼容模型条目，与 MLX 条目并存，通过平台检测选择。

建议 **`model_registry.py` 中的 `ModelInfo` 增加 `platform` 字段**或使用包装策略。

简化方案：在 evaluator 中做平台感知的 model_id 映射。

#### 4.1.3 LLM 推理优化

```python
# 优化版 _load_with_transformers()：

def _load_with_transformers(self) -> bool:
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_id = self.model_info.model_id

        self._tokenizer = AutoTokenizer.from_pretrained(
            model_id, trust_remote_code=True
        )

        # CUDA 优化：bf16 + Flash Attention 2
        self._model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,   # bf16 比 fp16 更稳定
            device_map="auto",              # 自动选择 CUDA
            attn_implementation="flash_attention_2",
            trust_remote_code=True,
        )

        # 可选：torch.compile 加速
        # self._model = torch.compile(self._model, mode="reduce-overhead")

        self._use_mlx = False
        return True
    except Exception as e:
        logger.error(f"Failed to load {self.model_name}: {e}")
        return False
```

#### 4.1.4 `<think>` 标签处理

Qwen3.5 的 thinking 模式在 CUDA + transformers 上的表现：
- transformers 的 `model.generate()` 默认带 `<think>` 标签
- 当前 evaluator 已有 `clean_thinking_content()` 处理
- CUDA 上不需要像 mlx-lm 那样裁剪 prompt 末尾的 `\n<think>\n`

**注意**：如果 CUDA 上仍然产生思考内容，`clean_thinking_content()` 方法已经覆盖。若想彻底避免，可以在 generate 参数中禁用思考模式：

```python
# 部分 Qwen 系列支持禁用 thinking
generation_kwargs = {"max_new_tokens": 128}
if "think" in self._model.config.model_type or hasattr(self._model.config, "enable_thinking"):
    generation_kwargs["enable_thinking"] = False  # 如果模型支持
```

#### 4.1.5 可选：替换为 vLLM

vLLM 在 4090 上对 4B 模型也值得考虑：

```python
# vLLM 版本（大幅提高吞吐）：
from vllm import LLM, SamplingParams

llm = LLM(
    model="Qwen/Qwen3.5-4B",
    tensor_parallel_size=1,
    dtype="float16",
    max_model_len=2048,  # LLM 后处理不需要长上下文
)

sampling_params = SamplingParams(
    temperature=0.3,
    max_tokens=128,
)

result = llm.generate(messages, sampling_params)
```

**权衡**：vLLM 内存占用量较大（~4-6GB vs transformers ~2-3GB），但吞吐更高，适合并发场景。单个 4090 上对 4B 模型收益有限。建议先用 transformers，后续视情况升级。

### 4.2 LLM 性能优化清单

| 优化 | 说明 | 推荐 |
|------|------|------|
| Flash Attention 2 | ~20-30% 加速 | ✅ 是 |
| bf16 精度 | FP16 更稳定 | ✅ 是 |
| int8 量化 | 省显存 + 无精度损失 | ✅ 是 |
| GPTQ-Int4 | 极致省显存 | ⚠️ 4B 模型收益有限 |
| torch.compile | ~10-20% 图编译加速 | ⚠️ 首次编译慢 |
| vLLM | 高吞吐框架 | ⚠️ 单请求场景用不到 |
| Continuous batching | 并发请求优化 | ⚠️ 同上 |

---

## 5. 模型注册表修改

### 5.1 shared/model_registry.py

添加 CUDA 兼容的平台检测和模型条目：

```python
import platform
import torch  # 新增

IS_APPLE_SILICON = platform.machine() == "arm64" and platform.system() == "Darwin"
IS_NVIDIA_GPU = False  # 新增
try:
    import torch
    IS_NVIDIA_GPU = torch.cuda.is_available()
except ImportError:
    pass

MODELS_CONFIG = {
    # ... 保留 MLX 条目（Apple Silicon）...

    # ── CUDA STT 模型 (NVIDIA GPU，推荐) ──
    "qwen_asr": {
        "model_id": "Qwen/Qwen3-ASR-1.7B",
        "engine": "qwen_asr_cuda",      # 映射到 qwen3_asr_cuda.py
        "memory_gb": 3.5,
        "description": "Qwen3-ASR-1.7B CUDA (4090 推荐)",
        "requires_nvidia": True,
    },
    "qwen_asr_small": {
        "model_id": "Qwen/Qwen3-ASR-0.6B",
        "engine": "qwen_asr_cuda",
        "memory_gb": 1.5,
        "description": "Qwen3-ASR-0.6B CUDA (更快)",
        "requires_nvidia": True,
    },

    # ── CUDA Whisper 模型 (NVIDIA GPU) ──
    # 已有的 whisper / whisper_turbo 通用路径直接可用
}

def get_default_model() -> str:
    """返回当前平台推荐的默认模型"""
    if IS_NVIDIA_GPU:
        return "qwen_asr"  # CUDA 默认
    if IS_APPLE_SILICON:
        return "qwen_asr_mlx_native_small"
    return "qwen_asr"  # 通用 fallback

def get_nvidia_models() -> list:
    """返回需要 NVIDIA GPU 的模型列表"""
    return [name for name, cfg in MODELS_CONFIG.items()
            if cfg.get("requires_nvidia")]
```

### 5.2 server/models/__init__.py

```python
# 有条件导入
try:
    import torch
    if torch.cuda.is_available():
        from server.models.qwen3_asr_cuda import Qwen3ASRCudaEngine
        _ENGINE_CLASSES["qwen_asr_cuda"] = Qwen3ASRCudaEngine
        logger.info("NVIDIA CUDA detected, CUDA STT engines available")
except ImportError:
    pass
```

### 5.3 LLM 模型注册表（llm_postprocessing/model_registry.py）

```python
# 新增 CUDA 版模型条目
QWEN35_CUDA_MODELS = [
    ModelInfo(
        model_id="Qwen/Qwen3.5-4B",
        name="Qwen3.5-4B-CUDA",
        size="4B",
        is_quantized=False,
        memory_fp16="~8GB",
        memory_int4="N/A",
        chinese_capability=5,
        speed=4,          # CUDA bf16 比 MLX 4bit 略慢但精度无损
        release_date="2026-03-02",
        provider="Alibaba/Qwen",
        repo_type="huggingface",
    ),
    ModelInfo(
        model_id="Qwen/Qwen3.5-2B",
        name="Qwen3.5-2B-CUDA",
        size="2B",
        is_quantized=False,
        memory_fp16="~4GB",
        memory_int4="N/A",
        chinese_capability=5,
        speed=5,
        release_date="2026-03-02",
        provider="Alibaba/Qwen",
        repo_type="huggingface",
    ),
]
```

---

## 6. 自启动与守护

### 6.1 Windows 服务注册

使用 NSSM (Non-Sucking Service Manager) 或 Windows Task Scheduler：

```powershell
# 方案一：NSSM（推荐）
nssm install "VIF-STT" "C:\ProgramData\miniconda3\envs\vif-stt\python.exe" "C:\path\to\server\stt_server.py"
nssm set "VIF-STT" AppDirectory "C:\path\to\voice-input-framework"
nssm set "VIF-STT" AppStdout "C:\path\to\logs\stt.log"
nssm set "VIF-STT" AppStderr "C:\path\to\logs\stt-error.log"
nssm set "VIF-STT" Start SERVICE_AUTO_START
nssm set "VIF-STT" AppEnvironmentExtra "CUDA_VISIBLE_DEVICES=0"
nssm start "VIF-STT"

nssm install "VIF-LLM" ... (同理)
```

```powershell
# 方案二：PowerShell 脚本 + Task Scheduler（已在 client/auto_start.py 中有实现）
# 参考现有 Windows 自动启动代码
```

### 6.2 CUDA 设备选择

通过环境变量控制 GPU：

```powershell
# 4090 独占
set CUDA_VISIBLE_DEVICES=0
```

---

## 7. 验证与基准测试

### 7.1 STT 验证

```powershell
# 环境确认
python -c "
import torch
import qwen_asr
print(f'PyTorch: {torch.__version__}')
print(f'CUDA: {torch.cuda.is_available()}')
print(f'GPU: {torch.cuda.get_device_name()}')
print(f'Flash Attn: {torch.backends.cuda.flash_sdp_enabled()}')
"

# 加载模型
python -c "
from transformers import AutoModelForCausalLM, AutoProcessor
model = AutoModelForCausalLM.from_pretrained(
    'Qwen/Qwen3-ASR-1.7B',
    torch_dtype=torch.float16,
    device_map='cuda:0',
    attn_implementation='flash_attention_2',  # 测试 FA2
    trust_remote_code=True,
)
print(f'Model loaded. Params on GPU')
"

# 端到端测试
curl -X POST http://localhost:6544/health
```

### 7.2 LLM 验证

```powershell
# 加载 LLM
python -c "
from transformers import AutoModelForCausalLM, AutoTokenizer
model = AutoModelForCausalLM.from_pretrained(
    'Qwen/Qwen3.5-4B',
    torch_dtype=torch.bfloat16,
    device_map='auto',
    trust_remote_code=True,
)
print(f'LLM loaded: {model.device}')
"
```

### 7.3 基准测试清单

| 测试项 | 方法 | 对比指标 |
|--------|------|---------|
| STT 1.7B 单次转写延迟 | 10 段 3s 音频，5 段 30s 音频 | 相比 MLX 的 x RTF |
| STT 1.7B 显存占用 | `nvidia-smi` 前后对比 | 相比 MLX 内存占用 |
| STT 流式延迟 | 客户端流式 10s 音频 | 首字延迟、尾字延迟 |
| LLM 单次推理延迟 | evaluator.benchmark() | 相比 MLX 的 ms |
| LLM 显存占用 | `nvidia-smi` | 是否 < 8GB |
| LLM 输出质量 | evaluator 测试集 | clean_thinking 成功率 |
| 端到端延迟 | 客户端录音→ASR→LLM→结果 | 总耗时 < Mac 版 |
| 稳定性 | 连续 100 次请求 | 无 OOM / 无 CUDA error |

---

## 8. 对比预期

| 维度 | Mac (MLX) | Windows 4090 (CUDA) | 预期变化 |
|------|-----------|---------------------|---------|
| **STT 1.7B 推理** | ~100-500ms | **~20-50ms** | ✅ 3-10x 快 |
| **STT 模型范围** | 0.6B-1.7B (mlx-community 量化) | 0.6B-1.7B (原版 FP16) + 更大模型 | ✅ 更灵活 |
| **STT 精度** | 8bit 量化 | FP16 无损 | ✅ 精度更好 |
| **LLM 4B 推理** | ~50-200ms | **~20-80ms** (bf16 + FA2) | ✅ 2-3x 快 |
| **LLM 模型范围** | 0.8B-4B (mlx-community 量化) | 0.6B-72B+ (原版/GPTQ, 受 24GB 限制) | ✅ 质的飞跃 |
| **LLM 精度** | 4bit 量化 | bf16 无损 或 GPTQ-Int4 | ✅ 可选无损 |
| **代码改动量** | — | 新建引擎 + 修改注册表 + 环境配置 | ⚠️ 中等 |
| **部署复杂度** | launchd + conda | NSSM/Task Scheduler + conda | ⚠️ 稍增 |
| **功耗** | ~15-30W (Apple Silicon) | ~150-450W (4090) | ⚠️ 功耗更高 |

---

## 9. 迁移路线图

### Phase 1: 环境就绪（1-2 天）

- [ ] 安装 NVIDIA 驱动 + CUDA 12.4
- [ ] 创建两个 conda 环境 (vif-stt / vif-llm)
- [ ] 验证 PyTorch CUDA 可用
- [ ] 验证 Flash Attention 2 编译安装

### Phase 2: STT 迁移（2-3 天）

- [ ] 创建 `qwen3_asr_cuda.py`
- [ ] 验证单次 transcribe 推理
- [ ] 优化：Flash Attention 2
- [ ] 优化：可选 int8 量化 / torch.compile
- [ ] 验证流式 transcribe 正常
- [ ] 注册到 `server/models/__init__.py`

### Phase 3: LLM 迁移（1-2 天）

- [ ] 修改 evaluator.py 适配 CUDA（transformers 路径已存在）
- [ ] 修改 model_registry.py 添加 CUDA 模型条目
- [ ] 验证 LLM 推理 + clean_thinking
- [ ] 调优 max_tokens / temperature

### Phase 4: 注册表 & 平台检测（1 天）

- [ ] 修改 `shared/model_registry.py` 添加 IS_NVIDIA_GPU
- [ ] 修改 `server/models/__init__.py` 条件导入
- [ ] 修改 `get_default_model()` 返回 CUDA 默认模型
- [ ] 端到端测试：客户端 → STT → LLM

### Phase 5: 部署 & 稳定性（1-2 天）

- [ ] 配置 NSSM 或 Task Scheduler 自启动
- [ ] 日志收集
- [ ] CUDA 异常捕获（OOM、driver 崩溃）
- [ ] 基准测试对比数据
- [ ] 更新 README 和文档

---

## 10. 附录：依赖汇总

### 必须安装

| 软件 | 版本 | 来源 |
|------|------|------|
| NVIDIA Driver | ≥ 550.0 | nvidia.com |
| CUDA Toolkit | 12.4+ | nvidia.com |
| Conda/Miniconda | 最新 | docs.conda.io |
| Python | 3.11 | conda 环境 |
| PyTorch | 2.5+ | pytorch.org (CUDA 12.4) |
| transformers | 4.37-5.0 | pip |
| qwen_asr | ≥ 0.0.6 | pip |

### 可选优化

| 包 | 用途 | 安装命令 |
|----|------|---------|
| flash-attn | Flash Attention 2 | `pip install flash-attn` |
| bitsandbytes | int8 量化 | `pip install bitsandbytes` |
| accelerate | device_map="auto" | `pip install accelerate` |

### 不需要（Apple Silicon 专属）

| 包 | 原因 |
|----|------|
| `mlx` | Apple Silicon 专属 |
| `mlx-lm` | Apple Silicon 专属 |
| `mlx-audio` | Apple Silicon 专属 |
| `mlx_community/` 模型 | MLX 量化格式，CUDA 无法加载 |
