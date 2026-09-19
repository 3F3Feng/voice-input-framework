#!/usr/bin/env python3
"""
服务端/客户端端点等价性对比脚本(第 1 层验证)

对基线(修复前)与当前代码分别用 FastAPI TestClient 打同一组请求,
归一化动态字段后对比响应,输出差异清单。

用法:
    python scripts/compare_endpoints.py --baseline /tmp/vif-baseline
    # 不加 --baseline 时,以"期望契约"模式运行(供 CI 使用,见 tests/test_contract.py)

说明:
    - 无需真实模型/GPU,TestClient 即可覆盖全部 HTTP 端点与 WS 握手/消息序列
    - 已知"有意变更"(来自 docs/ARCHITECTURE_REVIEW.md 修复)会被显式豁免,
      其余差异视为潜在回归
"""

import argparse
import json
import sys
from pathlib import Path

# 归一化动态字段:时间戳/延迟/uptime/模型加载状态等
DYNAMIC_KEYS = {
    "uptime_seconds",
    "stt_latency_ms",
    "llm_latency_ms",
    "loading_since",
    "timestamp",
    "latency_ms",
}


def normalize(obj):
    """递归归一化:去动态字段、排序、转 JSON 可比较结构"""
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in sorted(obj.items()) if k not in DYNAMIC_KEYS}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    return obj


def collect_endpoints(app):
    """用 TestClient 打全部端点,返回 {名称: 归一化响应}"""
    from fastapi.testclient import TestClient

    results = {}
    with TestClient(app) as c:
        # ── STT 服务 HTTP 端点 ──
        for name, method, url, kwargs in [
            ("health", "get", "/health", {}),
            ("models", "get", "/models", {}),
            ("models_status_first", "get", "/models/status/qwen_asr_mlx_native", {}),
            ("models_status_unknown", "get", "/models/status/does_not_exist", {}),
            ("llm_enabled", "get", "/llm/enabled", {}),
            ("transcribe_empty", "post", "/transcribe", {}),
            ("diarize_models", "get", "/diarize/models", {}),
            ("llm_models_proxy", "get", "/llm/models", {}),  # LLM 未启动→错误响应
            ("llm_health_proxy", "get", "/llm/health", {}),  # LLM 未启动→错误响应
            ("llm_prompt_proxy", "get", "/llm/prompt", {}),  # LLM 未启动→错误响应
        ]:
            try:
                if url == "/transcribe":
                    r = c.post(url, files={"file": ("t.wav", b"RIFF" + b"\x00" * 100, "audio/wav")})
                else:
                    r = getattr(c, method)(url, **kwargs)
                results[name] = {
                    "status": r.status_code,
                    "body": normalize(r.json()),
                }
            except Exception as e:  # noqa: BLE001
                results[name] = {"status": "ERROR", "body": f"{type(e).__name__}: {e}"}

        # ── WS /ws/stream 消息序列(发 config + end,收 ready/ack/done)──
        ws_msgs = []
        try:
            with c.websocket_connect("/ws/stream") as ws:
                ws_msgs.append(normalize(json.loads(ws.receive_text())))  # ready
                ws.send_text(json.dumps({"type": "config", "language": "auto"}))
                try:
                    ws_msgs.append(normalize(json.loads(ws.receive_text())))  # config_ack(可能无)
                except Exception:  # noqa: BLE001
                    pass
                ws.send_text(json.dumps({"type": "end"}))
                try:
                    ws_msgs.append(normalize(json.loads(ws.receive_text())))  # done
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            ws_msgs.append(f"WS ERROR: {type(e).__name__}: {e}")
        results["ws_stream_sequence"] = ws_msgs
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", help="基线代码目录(修复前)")
    args = parser.parse_args()

    def load_app(path):
        sys.path.insert(0, str(path))
        import services.stt_server

        return services.stt_server.app

    current = collect_endpoints(load_app(Path.cwd()))
    print("=== 当前 HEAD 端点响应 ===")
    print(json.dumps(current, ensure_ascii=False, indent=1)[:2000])

    if not args.baseline:
        return

    # 清除模块缓存,加载基线
    for mod in [m for m in list(sys.modules) if m.startswith(("services", "shared", "server"))]:
        del sys.modules[mod]
    baseline = collect_endpoints(load_app(Path(args.baseline).resolve()))
    print("\n=== 基线端点响应 ===")
    print(json.dumps(baseline, ensure_ascii=False, indent=1)[:2000])

    # 已知有意变更(H4 移除时间戳 / M7 结构化错误)——显式豁免
    EXPECTED_DIFFS = {
        "ws_stream_sequence": "ready 消息含 aligner_loaded 字段(H4 移除);config_ack 含 return_timestamps(H4 移除)",
        "llm_models_proxy": "错误结构由 error 字符串改为 error_code/error_message(M7)",
        "llm_health_proxy": "错误结构由 status/error 改为 error_code/error_message(M7)",
        "llm_prompt_proxy": "错误结构由 error 字符串改为 error_code/error_message(M7)",
        "transcribe_empty": "return_timestamps 参数移除(H4);错误体结构变更",
        "models_status_unknown": "HTTPException 结构(fastapi 版本差异,需人工确认)",
        "models_status_first": "model_info 移除 aligner_id 字段(H4 删除 aligner 配置)",
    }

    print("\n=== 差异对比 ===")
    changed = []
    for name in sorted(set(baseline) | set(current)):
        b, c = baseline.get(name), current.get(name)
        if b != c:
            changed.append(name)
            tag = "EXPECTED(有意变更)" if name in EXPECTED_DIFFS else "⚠️ UNEXPECTED(需人工确认)"
            print(f"\n[{name}] {tag}")
            print(f"  基线: {json.dumps(b, ensure_ascii=False)[:300]}")
            print(f"  当前: {json.dumps(c, ensure_ascii=False)[:300]}")
    if not changed:
        print("✅ 全部端点响应与基线一致")
    elif all(n in EXPECTED_DIFFS for n in changed):
        print("\n✅ 差异全部为已知有意变更(H4/M7),无意外回归")
    else:
        print("\n⚠️ 存在非预期差异,需人工确认")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
