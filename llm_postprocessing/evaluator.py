#!/usr/bin/env python3
"""
LLM后处理评估器
使用固定测试集评估所有候选模型
"""

import os
import sys
import json
import time
import statistics
import re
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_postprocessing.model_registry import ALL_MODELS, get_model_by_name, ModelInfo
from llm_postprocessing.test_dataset import get_test_cases, get_perf_test_cases


@dataclass
class TestResult:
    """单个测试结果"""
    case_id: str
    category: str
    input_text: str
    output_text: str
    latency_ms: float
    success: bool
    error: Optional[str] = None


@dataclass
class ModelBenchmark:
    """模型基准测试结果"""
    model_name: str
    model_size: str
    memory_usage: str

    # 延迟统计 (ms)
    avg_latency: float
    min_latency: float
    max_latency: float
    median_latency: float
    std_latency: float

    # 吞吐统计
    total_tokens: int
    tokens_per_second: float

    # 测试统计
    total_cases: int
    succeeded_cases: int
    failed_cases: int

    # 详细结果
    results: List[TestResult]


class PostProcessPrompt:
    """后处理提示词"""

    # 简短指令 - 减少模型生成思考内容
    @staticmethod
    def get_prompt(text: str) -> str:
        """获取后处理提示词(简短指令)"""
        return f"标点修正:{text}"

    @staticmethod
    def get_chat_prompt(text: str) -> list:
        """
        获取聊天格式的提示词

        目标：输出简洁、适合网络发布的文字
        - 移除填充词（嗯、啊、就是吧、那个啥）
        - 添加标点
        - 保持原意，表述更简洁
        - 适合网络环境发布
        """
        return [
            {'role': 'user', 'content': f'/no_think 关闭思考模式，只输出纯文本。优化STT识别结果：移除填充词，保持原意，添加标点，输出简洁准确的文本。直接输出结果，不要思考过程：{text}'}
        ]

    @staticmethod
    def clean_thinking_content(response: str) -> str:
        """
        清理思考内容

        移除各种格式的思考标签和内容:
        - <tool_call>...</tool_call> (Qwen3/Qwen3.5)
        - <思考>...</思考>
        - R\n...\nR (旧格式)
        - <thinking>...</thinking>
        """
        if not response:
            return response

        cleaned = response

        # 1. 移除 <tool_call>...</tool_call> (最常见)
        import re
        cleaned = re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', cleaned, flags=re.IGNORECASE)

        # 2. 移除 <思考>...</思考>
        cleaned = re.sub(r'<思考>[\s\S]*?</思考>', '', cleaned)

        # 3. 移除 R\n...\nR 格式（旧格式）
        cleaned = re.sub(r'R\n[\s\S]*?\nR', '', cleaned)

        # 4. 移除 <thinking>...</thinking>
        cleaned = re.sub(r'<thinking>[\s\S]*?</thinking>', '', cleaned, flags=re.IGNORECASE)

        return cleaned.strip()




class LLMEvaluator:
    """LLM评估器"""

    def __init__(self, model_name: str, verbose: bool = False, thinking_timeout: float = 5.0):
        self.model_name = model_name
        self.model_info = get_model_by_name(model_name)
        # 如果没在注册表中找到，假设 model_name 就是完整的 model_id
        self._model_id = self.model_info.model_id if self.model_info else model_name
        self.verbose = verbose
        self.thinking_timeout = thinking_timeout  # 思考超时时间（秒）
        self._model = None
        self._tokenizer = None
        self._use_mlx = False

    def _has_incomplete_thinking(self, response: str) -> bool:
        """检查响应是否包含未完成的思考内容"""
        has_think_start = '<think>' in response or 'Thinking Process' in response
        has_think_end = '</think>' in response
        return has_think_start and not has_think_end

    def _extract_result_from_thinking(self, response: str, original_text: str) -> str:
        """
        从思考内容中提取最终结果
        如果模型在思考中给出答案，尝试提取
        """
        # 尝试提取 Final, Output, Answer 等标记后的内容
        markers = ['Final Output:', 'Final:', 'Output:', '**Output**', 'Answer:', '**Answer**']
        for marker in markers:
            if marker in response:
                parts = response.split(marker)
                if len(parts) > 1:
                    result = parts[-1].strip()
                    # 清理并返回
                    result = result.split('\n')[0].strip()
                    if result:
                        return result

        # 尝试从 Input 字段提取原始输入（模型可能会重复输入）
        import re
        # 查找 Input: 或 "输入": 后的内容
        input_patterns = [r'Input:\s*["\'](.+?)["\']', r'输入:\s*["\'](.+?)["\']',
                         r'Input:\s*(.+?)(?:\n|$)', r'输入:\s*(.+?)(?:\n|$)']
        for pattern in input_patterns:
            match = re.search(pattern, response)
            if match:
                extracted = match.group(1).strip()
                if len(extracted) > 3:
                    return extracted

        # 尝试提取引号中的答案（排除 Input 相关）
        quotes = re.findall(r'["\']([^"\']{10,100})["\']', response)
        for q in quotes:
            if not any(x in q.lower() for x in ['input', 'analyze', 'request', 'constraint']):
                return q

        # 如果所有方法都失败，返回原始文本（不做处理）
        return original_text

    def load(self) -> bool:
        """加载模型"""
        if self._model is not None:
            return True

        # 尝试 MLX
        try:
            import mlx_lm

            self._model, self._tokenizer = mlx_lm.load(self._model_id)
            self._use_mlx = True

            if self.verbose:
                print(f"✅ [{self.model_name}] Loaded via MLX")

            return True

        except (ImportError, ValueError, Exception) as e:
            if self.verbose:
                print(f"⚠️  MLX failed ({type(e).__name__}), trying transformers...")

            return self._load_with_transformers()

    def _load_with_transformers(self) -> bool:
        """使用 transformers 加载模型"""
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import torch

            model_id = self.model_info.model_id

            print(f"📥 Downloading {self.model_name} via transformers...")

            self._tokenizer = AutoTokenizer.from_pretrained(
                model_id,
                trust_remote_code=True
            )

            self._model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True
            )

            self._use_mlx = False

            if self.verbose:
                print(f"✅ [{self.model_name}] Loaded via transformers")

            return True

        except Exception as e:
            print(f"❌ Failed to load {self.model_name}: {e}")
            return False

    def unload(self):
        """卸载模型"""
        self._model = None
        self._tokenizer = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def process(self, text: str) -> tuple[str, float]:
        """处理单个文本,返回: (处理结果, 延迟ms)"""
        if self._model is None or self._tokenizer is None:
            if not self.load():
                return "", -1

        messages = PostProcessPrompt.get_chat_prompt(text)
        start_time = time.time()

        try:
            if self._use_mlx:
                import mlx_lm

                prompt_formatted = self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True
                )

                # 移除 Qwen3.5 模板默认添加的  <think>\n 思考标签
                # 使用 Unicode 码点明确构建字符串
                mlx_think_end = chr(0x0a) + chr(0x3c) + 'think' + chr(0x3e) + chr(0x0a)  # \nthink\n
                if prompt_formatted.endswith(mlx_think_end):
                    prompt_formatted = prompt_formatted[:-len(mlx_think_end)]

                response = mlx_lm.generate(
                    model=self._model,
                    tokenizer=self._tokenizer,
                    prompt=prompt_formatted,
                    max_tokens=128,
                )
            else:
                import torch

                inputs = self._tokenizer.apply_chat_template(
                    messages,
                    return_tensors="pt",
                    add_generation_prompt=True
                ).to(self._model.device)

                with torch.no_grad():
                    outputs = self._model.generate(
                        **inputs,
                        max_new_tokens=128,
                        temperature=0.1,
                        do_sample=False,
                    )

                full_response = self._tokenizer.decode(outputs[0], skip_special_tokens=True)

                # 提取 assistant 回复(跳过输入 prompt)
                if 'assistant' in full_response:
                    response = full_response.split('assistant')[-1].strip()
                elif '<|im_end|>' in full_response:
                    response = full_response.split('<|im_end|>')[-1].strip()
                else:
                    # 尝试按 token 数量估算
                    response = full_response.strip()

            # 清理思考内容
            response = PostProcessPrompt.clean_thinking_content(response)

            # 如果仍有未完成的思考内容，等待思考完成
            if self._has_incomplete_thinking(response):
                if self.verbose:
                    print(f"  ⏳ 检测到未完成的思考，等待完成... (超时: {self.thinking_timeout}s)")

                elapsed = time.time() - start_time
                timeout = self.thinking_timeout

                # 继续生成直到思考完成或超时
                max_attempts = 10
                for attempt in range(max_attempts):
                    # 检查是否超时
                    current_elapsed = time.time() - start_time
                    if current_elapsed >= timeout:
                        if self.verbose:
                            print(f"  ⏸️  思考等待超时 ({current_elapsed:.1f}s >= {timeout}s)")
                        break

                    # 继续生成更多 token
                    if self._use_mlx:
                        import mlx_lm
                        try:
                            continue_response = mlx_lm.generate(
                                model=self._model,
                                tokenizer=self._tokenizer,
                                prompt=response,  # 用当前响应继续
                                max_tokens=64,
                            )
                            if continue_response:
                                response = response + continue_response
                                response = PostProcessPrompt.clean_thinking_content(response)

                                # 检查思考是否完成
                                if not self._has_incomplete_thinking(response):
                                    if self.verbose:
                                        print(f"  ✅ 思考完成 (尝试 {attempt + 1})")
                                    break
                        except Exception as e:
                            if self.verbose:
                                print(f"  ⚠️  继续生成失败: {e}")
                            break
                    else:
                        # transformers 路径 - 简化处理
                        break

            latency = (time.time() - start_time) * 1000

            return response.strip(), latency

        except Exception as e:
            print(f"⚠️  Process error: {e}")
            return "", -1

    def benchmark(self, test_cases: List[Dict]) -> ModelBenchmark:
        """运行基准测试"""
        if not self.load():
            return None

        results = []
        latencies = []
        total_tokens = 0

        print(f"\n🔄 Benchmarking {self.model_name}...")

        for i, case in enumerate(test_cases, 1):
            input_text = case["input"]

            if self.verbose:
                print(f"  [{i}/{len(test_cases)}] Input: {input_text[:30]}...")

            output, latency = self.process(input_text)

            # 估算token数(简单用字符数/2)
            token_count = max(1, len(output) // 2)
            total_tokens += token_count

            latencies.append(latency)
            success = len(output) > 0 and latency > 0

            results.append(TestResult(
                case_id=case.get("id", f"case_{i}"),
                category=case.get("category", "unknown"),
                input_text=input_text,
                output_text=output,
                latency_ms=latency,
                success=success,
            ))

            if not success:
                print(f"  ⚠️  Case {case.get('id')} failed")

        # 计算统计
        valid_latencies = [l for l in latencies if l > 0]

        if valid_latencies:
            benchmark = ModelBenchmark(
                model_name=self.model_name,
                model_size=self.model_info.size,
                memory_usage=self.model_info.memory_int4,
                avg_latency=statistics.mean(valid_latencies),
                min_latency=min(valid_latencies),
                max_latency=max(valid_latencies),
                median_latency=statistics.median(valid_latencies),
                std_latency=statistics.stdev(valid_latencies) if len(valid_latencies) > 1 else 0,
                total_tokens=total_tokens,
                tokens_per_second=total_tokens / (sum(valid_latencies) / 1000) if valid_latencies else 0,
                total_cases=len(test_cases),
                succeeded_cases=len([r for r in results if r.success]),
                failed_cases=len([r for r in results if not r.success]),
                results=results,
            )
        else:
            benchmark = ModelBenchmark(
                model_name=self.model_name,
                model_size=self.model_info.size,
                memory_usage=self.model_info.memory_int4,
                avg_latency=-1,
                min_latency=-1,
                max_latency=-1,
                median_latency=-1,
                std_latency=-1,
                total_tokens=0,
                tokens_per_second=0,
                total_cases=len(test_cases),
                succeeded_cases=0,
                failed_cases=len(test_cases),
                results=[],
            )

        return benchmark


def run_all_benchmarks(verbose: bool = False) -> List[ModelBenchmark]:
    """运行所有模型的基准测试"""
    results = []
    test_cases = get_test_cases()
    perf_cases = get_perf_test_cases()

    print("=" * 70)
    print("🧪 LLM后处理模型评估")
    print("=" * 70)
    print(f"📋 Test cases: {len(test_cases)} quality + {len(perf_cases)} performance")
    print(f"🤖 Models to test: {len(ALL_MODELS)}")
    print()

    for model in ALL_MODELS:
        evaluator = LLMEvaluator(model.name, verbose=verbose)

        try:
            benchmark = evaluator.benchmark(test_cases + perf_cases)

            if benchmark:
                results.append(benchmark)
                print(f"\n📊 {model.name} Results:")
                print(f"   Latency: avg={benchmark.avg_latency:.1f}ms, "
                      f"median={benchmark.median_latency:.1f}ms, "
                      f"range=[{benchmark.min_latency:.1f}-{benchmark.max_latency:.1f}]ms")
                print(f"   Throughput: {benchmark.tokens_per_second:.1f} tokens/s")
                print(f"   Success: {benchmark.succeeded_cases}/{benchmark.total_cases}")
        finally:
            evaluator.unload()

    return results


def print_comparison_table(benchmarks: List[ModelBenchmark]):
    """打印模型对比表"""
    print("\n" + "=" * 100)
    print("📊 Model Comparison (sorted by avg latency)")
    print("=" * 100)
    print(f"{'Model':<18} {'Size':<6} {'Memory':<10} {'Avg(ms)':<10} {'Median':<10} {'Throughput':<12} {'Success'}")
    print("-" * 100)

    sorted_benchmarks = sorted(benchmarks, key=lambda x: x.avg_latency)

    for b in sorted_benchmarks:
        print(f"{b.model_name:<18} {b.model_size:<6} {b.memory_usage:<10} "
              f"{b.avg_latency:<10.1f} {b.median_latency:<10.1f} "
              f"{b.tokens_per_second:<12.1f} {b.succeeded_cases}/{b.total_cases}")

    print("=" * 100)


def save_results(benchmarks: List[ModelBenchmark], output_file: str = "benchmark_results.json"):
    """保存结果到文件"""
    output_path = Path(__file__).parent.parent / output_file

    data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "benchmarks": [asdict(b) for b in benchmarks]
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Results saved to {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate LLM models for post-processing")
    parser.add_argument("--model", "-m", type=str, help="Test specific model")
    parser.add_argument("--all", "-a", action="store_true", help="Test all models")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--output", "-o", type=str, default="benchmark_results.json", help="Output file")
    parser.add_argument("--list", "-l", action="store_true", help="List available models")

    args = parser.parse_args()

    if args.list:
        from llm_postprocessing.model_registry import print_model_table
        print_model_table()
        return

    if args.model:
        evaluator = LLMEvaluator(args.model, verbose=args.verbose)
        test_cases = get_test_cases() + get_perf_test_cases()
        benchmark = evaluator.benchmark(test_cases)

        if benchmark:
            print(f"\n📊 Results for {args.model}:")
            print(f"   Latency: avg={benchmark.avg_latency:.1f}ms, "
                  f"median={benchmark.median_latency:.1f}ms")
            print(f"   Throughput: {benchmark.tokens_per_second:.1f} tokens/s")
            print(f"   Success: {benchmark.succeeded_cases}/{benchmark.total_cases}")
            save_results([benchmark], args.output)
        return

    if args.all:
        benchmarks = run_all_benchmarks(verbose=args.verbose)
        print_comparison_table(benchmarks)
        save_results(benchmarks, args.output)
        return

    parser.print_help()
    print("\n📋 Available models:")
    for m in ALL_MODELS:
        print(f"   {m.name:<15} ({m.size}, {m.memory_int4})")


if __name__ == "__main__":
    main()
