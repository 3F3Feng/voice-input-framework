#!/usr/bin/env python3
"""
Voice Input Framework - 统一平台检测模块

自动检测硬件环境，返回 PlatformInfo 对象。
所有组件使用同一份检测结果，避免重复检测和不一致。

使用方式:
    from shared.platform_detector import detect_platform, get_platform_info
    
    platform = detect_platform()
    print(platform.best_backend)  # "mlx" | "cuda" | "mps" | "cpu"
    print(platform.recommended_models)  # ["qwen_asr_mlx_native_small", ...]
"""

import logging
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CUDADeviceInfo:
    """CUDA 设备信息"""
    name: str                           # "NVIDIA RTX 4090"
    memory_gb: float                    # 24.0
    driver_version: str                 # "550.0"
    compute_capability: str             # "8.9"
    cuda_version: str                   # "12.4"


@dataclass
class PlatformInfo:
    """平台硬件信息"""
    # 系统信息
    system: str                         # "Darwin", "Windows", "Linux"
    arch: str                           # "arm64", "x86_64"
    python_version: str                 # "3.11.0"
    
    # 平台标志
    is_apple_silicon: bool              # ARM64 + macOS
    is_macos: bool                      # macOS (any arch)
    is_windows: bool                    # Windows
    is_linux: bool                      # Linux
    
    # 硬件加速
    has_mlx: bool                       # mlx 库可用 (Apple Silicon)
    has_cuda: bool                      # CUDA 可用 (NVIDIA GPU)
    has_mps: bool                       # MPS (Metal Performance Shaders) 可用
    cuda_device: Optional[CUDADeviceInfo] = None  # CUDA 设备信息
    
    # 系统资源
    cpu_cores: int = 0                  # CPU 核心数
    ram_gb: float = 0.0                 # 系统内存 (GB)
    
    # 检测状态
    detection_errors: list[str] = field(default_factory=list)
    
    @property
    def best_backend(self) -> str:
        """返回最优后端: mlx > cuda > mps > cpu"""
        if self.has_mlx:
            return "mlx"
        elif self.has_cuda:
            return "cuda"
        elif self.has_mps:
            return "mps"
        else:
            return "cpu"
    
    @property
    def gpu_info(self) -> str:
        """返回 GPU 信息字符串"""
        if self.has_cuda and self.cuda_device:
            return f"{self.cuda_device.name} ({self.cuda_device.memory_gb}GB)"
        elif self.has_mps:
            return "Apple Silicon GPU (MPS)"
        elif self.has_mlx:
            return "Apple Silicon GPU (MLX)"
        else:
            return "No GPU acceleration"
    
    def get_recommended_stt_models(self) -> list[str]:
        """返回推荐的 STT 模型列表（按优先级排序）"""
        models = []
        
        if self.is_apple_silicon and self.has_mlx:
            # Apple Silicon: 优先 MLX 模型
            models.extend([
                "qwen_asr_mlx_native_small",  # 0.5GB, 最快
                "qwen_asr_mlx_native",         # 1.0GB, 更准
                "whisper_mlx_small",           # 0.5GB, 备选
                "whisper_mlx_turbo",           # 2.0GB, 平衡
            ])
        elif self.has_cuda:
            # NVIDIA GPU: 优先 CUDA 模型
            if self.cuda_device and self.cuda_device.memory_gb >= 8:
                models.extend([
                    "qwen_asr_cuda",            # 3.5GB, 大模型
                    "qwen_asr_cuda_small",      # 1.5GB, 小模型
                ])
            else:
                models.extend([
                    "qwen_asr_cuda_small",      # 1.5GB, 小模型
                    "qwen_asr_cuda",            # 3.5GB, 大模型
                ])
            models.append("whisper_turbo")      # 3GB, 通用备选
        else:
            # CPU: 只能用 transformers 模型
            models.extend([
                "whisper_turbo",                # 3GB, 通用
            ])
        
        return models
    
    def get_recommended_llm_models(self) -> list[str]:
        """返回推荐的 LLM 模型列表（按优先级排序）"""
        models = []
        
        if self.is_apple_silicon and self.has_mlx:
            models.extend([
                "qwen3.5-4b-mlx",              # MLX 量化，2.5GB
                "qwen3.5-2b-mlx",              # MLX 量化，1.5GB
            ])
        elif self.has_cuda:
            if self.cuda_device and self.cuda_device.memory_gb >= 16:
                models.extend([
                    "qwen3.5-4b-cuda",          # FP16, 8GB
                    "qwen3.5-2b-cuda",          # FP16, 4GB
                ])
            else:
                models.extend([
                    "qwen3.5-2b-cuda-int8",     # int8 量化, 2GB
                    "qwen3.5-4b-cuda-int8",     # int8 量化, 4GB
                ])
        else:
            # CPU: 不推荐运行 LLM
            pass
        
        return models
    
    def summary(self) -> str:
        """返回平台信息摘要"""
        lines = [
            f"System: {self.system} {self.arch}",
            f"Python: {self.python_version}",
            f"CPU: {self.cpu_cores} cores, {self.ram_gb:.1f}GB RAM",
            f"GPU: {self.gpu_info}",
            f"Backend: {self.best_backend}",
        ]
        
        if self.has_cuda and self.cuda_device:
            lines.append(f"CUDA: {self.cuda_device.cuda_version}, Driver: {self.cuda_device.driver_version}")
        
        if self.detection_errors:
            lines.append(f"Warnings: {'; '.join(self.detection_errors)}")
        
        return "\n".join(lines)


def _detect_mlx() -> bool:
    """检测 MLX 是否可用"""
    try:
        import mlx.core
        return True
    except ImportError:
        return False


def _detect_cuda() -> tuple[bool, Optional[CUDADeviceInfo]]:
    """检测 CUDA 是否可用，返回 (available, device_info)"""
    try:
        import torch
        if not torch.cuda.is_available():
            return False, None
        
        device = torch.cuda.get_device_properties(0)
        
        # 获取 CUDA 版本
        cuda_version = torch.version.cuda or "unknown"
        
        # 获取驱动版本（通过 nvidia-smi）
        driver_version = "unknown"
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                driver_version = result.stdout.strip().split("\n")[0]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # 尝试从 torch 获取
            if hasattr(torch.cuda, 'driver_version'):
                driver_version = str(torch.cuda.driver_version())
        
        device_info = CUDADeviceInfo(
            name=device.name,
            memory_gb=device.total_mem / (1024 ** 3),  # bytes to GB
            driver_version=driver_version,
            compute_capability=f"{device.major}.{device.minor}",
            cuda_version=cuda_version,
        )
        
        return True, device_info
        
    except ImportError:
        return False, None
    except Exception as e:
        logger.warning(f"CUDA detection error: {e}")
        return False, None


def _detect_mps() -> bool:
    """检测 MPS (Metal Performance Shaders) 是否可用"""
    try:
        import torch
        return hasattr(torch.backends, 'mps') and torch.backends.mps.is_available()
    except ImportError:
        return False
    except Exception:
        return False


def _get_system_resources() -> tuple[int, float]:
    """获取系统资源信息: (cpu_cores, ram_gb)"""
    import os
    
    cpu_cores = os.cpu_count() or 0
    ram_gb = 0.0
    
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        # psutil 不可用，尝试其他方法
        try:
            if platform.system() == "Darwin":
                result = subprocess.run(
                    ["sysctl", "-n", "hw.memsize"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    ram_bytes = int(result.stdout.strip())
                    ram_gb = ram_bytes / (1024 ** 3)
            elif platform.system() == "Linux":
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            # MemTotal:   16384000 kB
                            parts = line.split()
                            if len(parts) >= 2:
                                ram_kb = int(parts[1])
                                ram_gb = ram_kb / (1024 ** 2)
                            break
        except Exception:
            pass
    
    return cpu_cores, ram_gb


# 全局缓存
_platform_info: Optional[PlatformInfo] = None


def detect_platform(force_redetect: bool = False) -> PlatformInfo:
    """
    检测当前平台信息
    
    Args:
        force_redetect: 强制重新检测（忽略缓存）
    
    Returns:
        PlatformInfo 对象
    """
    global _platform_info
    
    if _platform_info is not None and not force_redetect:
        return _platform_info
    
    errors = []
    
    # 系统信息
    system = platform.system()  # "Darwin", "Windows", "Linux"
    arch = platform.machine()   # "arm64", "x86_64"
    python_version = platform.python_version()
    
    # 平台标志
    is_macos = system == "Darwin"
    is_windows = system == "Windows"
    is_linux = system == "Linux"
    is_apple_silicon = is_macos and arch == "arm64"
    
    # 硬件加速检测
    has_mlx = False
    has_cuda = False
    has_mps = False
    cuda_device = None
    
    # 检测 MLX
    try:
        has_mlx = _detect_mlx()
    except Exception as e:
        errors.append(f"MLX detection failed: {e}")
    
    # 检测 CUDA
    try:
        has_cuda, cuda_device = _detect_cuda()
    except Exception as e:
        errors.append(f"CUDA detection failed: {e}")
    
    # 检测 MPS
    try:
        has_mps = _detect_mps()
    except Exception as e:
        errors.append(f"MPS detection failed: {e}")
    
    # 系统资源
    try:
        cpu_cores, ram_gb = _get_system_resources()
    except Exception as e:
        errors.append(f"Resource detection failed: {e}")
        cpu_cores = 0
        ram_gb = 0.0
    
    _platform_info = PlatformInfo(
        system=system,
        arch=arch,
        python_version=python_version,
        is_apple_silicon=is_apple_silicon,
        is_macos=is_macos,
        is_windows=is_windows,
        is_linux=is_linux,
        has_mlx=has_mlx,
        has_cuda=has_cuda,
        has_mps=has_mps,
        cuda_device=cuda_device,
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        detection_errors=errors,
    )
    
    logger.info(f"Platform detected:\n{_platform_info.summary()}")
    
    return _platform_info


def get_platform_info() -> PlatformInfo:
    """获取已缓存的平台信息（如果未缓存则先检测）"""
    return detect_platform()


def get_best_backend() -> str:
    """获取最优后端: mlx > cuda > mps > cpu"""
    return detect_platform().best_backend


def is_apple_silicon() -> bool:
    """是否是 Apple Silicon"""
    return detect_platform().is_apple_silicon


def has_cuda() -> bool:
    """是否有 CUDA 支持"""
    return detect_platform().has_cuda


def has_mlx() -> bool:
    """是否有 MLX 支持"""
    return detect_platform().has_mlx


# 向后兼容：替换 shared/model_registry.py 中的 IS_APPLE_SILICON
IS_APPLE_SILICON = is_apple_silicon()


if __name__ == "__main__":
    # 测试平台检测
    logging.basicConfig(level=logging.INFO)
    info = detect_platform()
    print("\n" + "=" * 60)
    print("Platform Detection Results")
    print("=" * 60)
    print(info.summary())
    print("\nRecommended STT Models:")
    for model in info.get_recommended_stt_models():
        print(f"  - {model}")
    print("\nRecommended LLM Models:")
    for model in info.get_recommended_llm_models():
        print(f"  - {model}")
