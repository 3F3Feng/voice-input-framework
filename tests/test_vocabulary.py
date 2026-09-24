"""个人词库(F13):热词作为识别上下文,替换规则识别后确定性改写"""

import pytest
from fastapi.testclient import TestClient

from services import vocabulary as v


def test_parse_splits_hotwords_and_rules():
    vocab = v.parse(["石枫", "陶睿 => Tauri", "千问->Qwen", "凯德 → Kinder", "", "石枫", "x => x"])
    assert vocab.hotwords == ["石枫"]
    assert vocab.rules == [("陶睿", "Tauri"), ("千问", "Qwen"), ("凯德", "Kinder")]


def test_context_contains_hotwords_and_rule_targets():
    vocab = v.parse(["石枫", "陶睿 => Tauri"])
    assert v.context_text(vocab) == "石枫、Tauri"
    assert v.context_text(v.parse([])) is None
    assert "石枫" in v.llm_hint(vocab)


def test_context_is_capped():
    vocab = v.parse([f"词{i:03d}" for i in range(150)])
    assert len(v.context_text(vocab)) <= v.MAX_CONTEXT_CHARS


def test_rules_replace_longest_first():
    vocab = v.parse(["陶 => T", "陶睿 => Tauri"])
    assert v.apply_rules("用陶睿做客户端", vocab) == "用Tauri做客户端"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import services.stt_server as srv

    monkeypatch.setattr(srv, "STATE_FILE", tmp_path / "stt_state.json")
    monkeypatch.setattr(srv, "STATE_DIR", tmp_path)
    monkeypatch.setattr(srv, "VOCABULARY", v.parse([]))
    return TestClient(srv.app), srv


def test_vocabulary_endpoints_persist_and_apply(client):
    c, srv = client
    r = c.put("/vocabulary", json={"entries": ["石枫", " 陶睿 => Tauri ", ""]})
    assert r.status_code == 200
    assert r.json() == {"entries": ["石枫", "陶睿 => Tauri"], "hotwords": 1, "rules": 1}
    assert c.get("/vocabulary").json()["entries"] == ["石枫", "陶睿 => Tauri"]
    assert srv.VOCABULARY.rules == [("陶睿", "Tauri")]
    assert c.put("/vocabulary", json={"entries": "nope"}).status_code == 400


@pytest.mark.asyncio
async def test_ws_applies_rules_and_passes_context(client, monkeypatch):
    import numpy as np

    c, srv = client
    c.put("/vocabulary", json={"entries": ["石枫", "陶睿 => Tauri"]})
    seen = {}

    async def fake_transcribe(audio, language="auto", context=None):
        seen["context"] = context
        return srv.TranscriptionResult(text="我们用陶睿做客户端")

    monkeypatch.setattr(srv.engine, "transcribe", fake_transcribe)
    monkeypatch.setattr(srv, "llm_active", lambda: False)
    import base64
    import json

    tone = (3000 * np.sin(np.arange(16000) / 16000 * 2 * np.pi * 220)).astype(np.int16).tobytes()
    with c.websocket_connect("/ws/stream") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "config", "language": "auto"}))
        ws.receive_text()
        ws.send_text(json.dumps({"type": "audio", "data": base64.b64encode(tone).decode()}))
        ws.send_text(json.dumps({"type": "end"}))
        final = None
        while True:
            m = json.loads(ws.receive_text())
            if m["type"] == "result":
                final = m["text"]
            if m["type"] in ("done", "error"):
                break
    assert seen["context"] == "石枫、Tauri"
    assert final == "我们用Tauri做客户端"
