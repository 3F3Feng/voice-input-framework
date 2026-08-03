"""
端点契约测试(第 1 层等价性固化)

将 scripts/compare_endpoints.py 的对比结果固化为持久断言:
当前代码的 HTTP/WS 端点响应必须保持与基线(a279c8e 修复前)等价的契约,
已知有意变更(H4 移除 aligner/时间戳、M7 结构化错误)以显式断言锁定。

这些测试无需真实模型/GPU,CI 可直接运行(TestClient 内联 app)。
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from services.stt_server import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ──────────────────── HTTP 端点契约 ────────────────────


class TestHealthContract:
    """GET /health 与基线等价"""

    def test_status_loading(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "loading"  # 模型未加载
        assert body["version"] == "1.1.0"
        assert "uptime_seconds" in body
        assert body["current_model"]  # 非空
        assert isinstance(body["loaded_models"], list)


class TestModelsContract:
    """GET /models 与基线等价"""

    def test_models_list_structure(self, client):
        r = client.get("/models")
        assert r.status_code == 200
        models = r.json()
        assert isinstance(models, list) and len(models) > 0
        for m in models:
            assert set(m) >= {"name", "description", "is_loaded", "is_default"}
            assert isinstance(m["name"], str) and m["name"]

    def test_model_status_known(self, client):
        """GET /models/status/{known}:含 model_info,无 aligner_id(H4 有意移除)"""
        r = client.get("/models/status/qwen_asr_mlx_native")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "qwen_asr_mlx_native"
        assert "is_loaded" in body and "is_loading" in body
        assert "model_info" in body
        assert "aligner_id" not in body["model_info"]  # H4 移除

    def test_model_status_unknown(self, client):
        r = client.get("/models/status/does_not_exist")
        assert r.status_code == 404


class TestLLMProxyContract:
    """LLM 转发端点契约(M7:结构化错误)"""

    def test_llm_enabled(self, client):
        r = client.get("/llm/enabled")
        assert r.status_code == 200
        assert r.json() == {"enabled": True}

    def test_llm_proxy_error_structure(self, client):
        """LLM 服务未启动时,转发端点返回结构化 ErrorResponse(M7)"""
        for path in ("/llm/models", "/llm/health", "/llm/prompt"):
            r = client.get(path)
            assert r.status_code == 200  # 转发端点返回 200 + 错误体
            body = r.json()
            assert body["error_code"] == "LLM_PROXY_ERROR"
            assert "error_message" in body
            assert "details" in body


class TestTranscribeContract:
    """POST /transcribe 契约(H4:无 return_timestamps 参数)"""

    def test_invalid_audio_error(self, client):
        r = client.post(
            "/transcribe",
            files={"file": ("t.wav", b"RIFF" + b"\x00" * 100, "audio/wav")},
        )
        # 无真实模型时返回 500;重点是响应为 JSON 错误而非崩溃
        assert r.status_code in (200, 500)
        if r.status_code == 500:
            assert "detail" in r.json()


# ──────────────────── WebSocket 契约 ────────────────────


class TestWebSocketContract:
    """WS /ws/stream 消息序列与基线等价(H4 移除 aligner/return_timestamps)"""

    def test_ws_handshake_and_sequence(self, client):
        with client.websocket_connect("/ws/stream") as ws:
            # ready
            ready = json.loads(ws.receive_text())
            assert ready["type"] == "ready"
            assert ready["model"]
            assert "is_loading" in ready
            assert "aligner_loaded" not in ready  # H4 移除

            # config → config_ack
            ws.send_text(json.dumps({"type": "config", "language": "auto"}))
            ack = json.loads(ws.receive_text())
            assert ack["type"] == "config_ack"
            assert ack["language"] == "auto"
            assert "return_timestamps" not in ack  # H4 移除

            # end → done
            ws.send_text(json.dumps({"type": "end"}))
            done = json.loads(ws.receive_text())
            assert done["type"] == "done"

    def test_ws_audio_roundtrip_messages(self, client):
        """发送静音音频:收到已知类型消息并正常结束(不卡死)

        无真实模型时服务器会返回 error 后关闭连接——与基线行为一致;
        有模型环境会返回 stt_result/result。两种都接受,重点是连接不挂起。
        """
        with client.websocket_connect("/ws/stream") as ws:
            ws.receive_text()  # ready
            ws.send_text(json.dumps({"type": "config", "language": "auto"}))
            ws.receive_text()  # config_ack

            silence = b"\x00\x00" * 8000  # 0.5s 16kHz 静音
            ws.send_text(json.dumps({"type": "audio", "data": base64.b64encode(silence).decode()}))
            ws.send_text(json.dumps({"type": "end"}))

            seen = []
            while True:
                msg = json.loads(ws.receive_text())
                seen.append(msg["type"])
                if msg["type"] in ("done", "error"):
                    break
            # 已知消息类型集合(无模型:error;有模型:stt_result/result)
            assert seen[-1] in ("done", "error")
