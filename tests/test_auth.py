"""可选访问令牌(F20):设了 VIF_API_TOKEN 才生效,/health 始终公开"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from shared import auth


def test_token_check_logic(monkeypatch):
    monkeypatch.delenv("VIF_API_TOKEN", raising=False)
    assert auth.token_ok(None)  # 没配置:一律放行
    monkeypatch.setenv("VIF_API_TOKEN", "s3cret")
    assert not auth.token_ok(None)
    assert not auth.token_ok("Bearer wrong")
    assert auth.token_ok("Bearer s3cret")
    assert auth.token_ok(None, query_token="s3cret")
    assert auth.outgoing_headers() == {"Authorization": "Bearer s3cret"}
    assert not auth.needs_check("GET", "/health")
    assert not auth.needs_check("OPTIONS", "/models")


@pytest.fixture
def stt_client(monkeypatch):
    import services.stt_server as srv

    monkeypatch.setenv("VIF_API_TOKEN", "s3cret")
    return TestClient(srv.app)


def test_http_requires_token_except_health(stt_client):
    assert stt_client.get("/health").status_code == 200
    r = stt_client.get("/models")
    assert r.status_code == 401
    assert "令牌" in r.json()["error_message"]
    assert stt_client.get("/models", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_websocket_requires_token(stt_client):
    with pytest.raises(WebSocketDisconnect):
        with stt_client.websocket_connect("/ws/stream") as ws:
            ws.receive_text()
    with stt_client.websocket_connect("/ws/stream?token=s3cret") as ws:
        assert '"ready"' in ws.receive_text()


def test_llm_server_requires_token(monkeypatch):
    import services.llm_server as llm

    monkeypatch.setenv("VIF_API_TOKEN", "s3cret")
    c = TestClient(llm.app)
    assert c.get("/health").status_code == 200
    assert c.get("/models").status_code == 401
    assert c.get("/models", headers={"Authorization": "Bearer s3cret"}).status_code == 200
