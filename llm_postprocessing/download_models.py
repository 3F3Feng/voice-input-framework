#!/usr/bin/env python3
"""
模型下载脚本
下载所有候选模型的MLX量化版本
"""

import os
import sys
import argparse
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_postprocessing.model_registry import ALL_MODELS, get_model_by_name


def get_model_dir() -> Path:
    """获取模型存储目录"""
    model_dir = Path.home() / ".cache" / "mlx" / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def download_model(model_name: str, verbose: bool = False) -> bool:
    """下载单个模型"""
    model_info = get_model_by_name(model_name)
    if not model_info:
        print(f"❌ Unknown model: {model_name}")
        return False
    
    print(f"\n📥 Downloading {model_info.name}...")
    print(f"   ID: {model_info.model_id}")
    print(f"   Size: {model_info.size} | Memory: {model_info.memory_int4}")
    
    try:
        # 使用 mlx_lm.download 命令
        import subprocess
        result = subprocess.run(
            ["mlx_lm", "download", "--model", model_info.model_id],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            print(f"✅ {model_info.name} downloaded successfully")
            if verbose:
                print(result.stdout)
            return True
        else:
            print(f"❌ Failed to download {model_info.name}")
            print(result.stderr)
            return False
            
    except FileNotFoundError:
        print("❌ mlx_lm not found. Please install: pip install mlx-lm")
        return False
    except Exception as e:
        print(f"❌ Error downloading {model_info.name}: {e}")
        return False


def download_all(verbose: bool = False) -> dict:
    """下载所有候选模型"""
    results = {}
    total = len(ALL_MODELS)
    
    print(f"\n📦 Downloading {total} models to {get_model_dir()}")
    print("=" * 60)
    
    for i, model in enumerate(ALL_MODELS, 1):
        print(f"\n[{i}/{total}] ", end="")
        success = download_model(model.name, verbose)
        results[model.name] = success
    
    # 打印总结
    print("\n" + "=" * 60)
    print("📊 Download Summary:")
    print("-" * 60)
    
    succeeded = [name for name, ok in results.items() if ok]
    failed = [name for name, ok in results.items() if not ok]
    
    print(f"✅ Succeeded: {len(succeeded)}")
    for name in succeeded:
        print(f"   - {name}")
    
    if failed:
        print(f"\n❌ Failed: {len(failed)}")
        for name in failed:
            print(f"   - {name}")
    
    return results


def list_downloaded() -> list:
    """列出已下载的模型"""
    model_dir = get_model_dir()
    downloaded = []
    
    for item in model_dir.iterdir():
        if item.is_dir():
            # 检查是否是MLX模型
            if (item / "mlx_model").exists() or (item / "config.json").exists():
                downloaded.append(item.name)
    
    return sorted(downloaded)


def main():
    parser = argparse.ArgumentParser(description="Download MLX models")
    parser.add_argument("--model", "-m", type=str, help="Download specific model by name")
    parser.add_argument("--list", "-l", action="store_true", help="List downloaded models")
    parser.add_argument("--all", "-a", action="store_true", help="Download all models")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    if args.list:
        print("\n📁 Downloaded models:")
        downloaded = list_downloaded()
        if downloaded:
            for name in downloaded:
                print(f"   - {name}")
        else:
            print("   No models downloaded yet")
        return
    
    if args.all:
        download_all(args.verbose)
        return
    
    if args.model:
        download_model(args.model, args.verbose)
        return
    
    # 默认显示帮助
    parser.print_help()
    print("\n📋 Available models:")
    for m in ALL_MODELS:
        print(f"   {m.name:<15} ({m.size}, {m.memory_int4})")


if __name__ == "__main__":
    main()
