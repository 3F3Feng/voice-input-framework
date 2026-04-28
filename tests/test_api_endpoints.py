"""Tests for STT/LLM API endpoints (live server integration tests).

Run with: .venv/bin/python -m pytest tests/test_api_endpoints.py -v

Requires servers running:
  - STT: localhost:6544
  - LLM: localhost:6545
  - Main: localhost:6543
"""

import httpx

STT_URL = "http://localhost:6544"
LLM_URL = "http://localhost:6545"
MAIN_URL = "http://localhost:6543"


# ── STT Server (6544) ──────────────────────────────────────────────


class TestSTTHealth:
    """STT /health endpoint"""

    def test_health_ok(self):
        resp = httpx.get(f"{STT_URL}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "loading")
        assert "uptime_seconds" in data
        assert "current_model" in data

    def test_health_has_version(self):
        resp = httpx.get(f"{STT_URL}/health", timeout=5)
        assert resp.status_code == 200
        assert "version" in resp.json()


class TestSTTModels:
    """STT /models endpoint"""

    def test_models_list(self):
        resp = httpx.get(f"{STT_URL}/models", timeout=5)
        assert resp.status_code == 200
        models = resp.json()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_qwen_asr_registered(self):
        resp = httpx.get(f"{STT_URL}/models", timeout=5)
        models = resp.json()
        names = [m["name"] for m in models]
        assert "qwen_asr_mlx_native_small" in names

    def test_model_info_fields(self):
        resp = httpx.get(f"{STT_URL}/models", timeout=5)
        models = resp.json()
        for m in models:
            assert "name" in m
            assert "description" in m
            assert "is_loaded" in m
            assert "is_default" in m


class TestSTTModelSelect:
    """STT /models/select endpoint"""

    def test_select_invalid_model(self):
        resp = httpx.post(
            f"{STT_URL}/models/select",
            json={"model": "nonexistent_model"},
            timeout=5,
        )
        assert resp.status_code in (400, 404, 422)


# ── LLM Server (6545) ──────────────────────────────────────────────


class TestLLMHealth:
    """LLM /health endpoint"""

    def test_health_ok(self):
        resp = httpx.get(f"{LLM_URL}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "loading")
        assert "current_model" in data

    def test_health_has_is_processing(self):
        resp = httpx.get(f"{LLM_URL}/health", timeout=5)
        assert resp.status_code == 200
        assert "is_processing" in resp.json()


class TestLLMModels:
    """LLM /models endpoint"""

    def test_models_list(self):
        resp = httpx.get(f"{LLM_URL}/models", timeout=5)
        assert resp.status_code == 200
        models = resp.json()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_model_info_fields(self):
        resp = httpx.get(f"{LLM_URL}/models", timeout=5)
        models = resp.json()
        for m in models:
            assert "name" in m
            assert "is_loaded" in m


class TestLLMProcess:
    """LLM /process endpoint"""

    def test_process_text(self):
        resp = httpx.post(
            f"{LLM_URL}/process",
            json={"text": "你好世界这是一个测试"},
            timeout=30,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "text" in data
        assert "original_text" in data
        assert "success" in data

    def test_process_empty_text(self):
        resp = httpx.post(
            f"{LLM_URL}/process",
            json={"text": ""},
            timeout=10,
        )
        # Should handle gracefully
        assert resp.status_code in (200, 400, 422)


# ── Main Server (6543) ─────────────────────────────────────────────


class TestMainHealth:
    """Main /health endpoint"""

    def test_health_ok(self):
        resp = httpx.get(f"{MAIN_URL}/health", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "loading")

    def test_health_has_llm_info(self):
        resp = httpx.get(f"{MAIN_URL}/health", timeout=5)
        data = resp.json()
        assert "llm_enabled" in data


class TestMainModels:
    """Main /models endpoint"""

    def test_models_list(self):
        resp = httpx.get(f"{MAIN_URL}/models", timeout=5)
        assert resp.status_code == 200
        models = resp.json()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_mlx_models_registered(self):
        resp = httpx.get(f"{MAIN_URL}/models", timeout=5)
        models = resp.json()
        names = [m["name"] for m in models]
        assert "qwen_asr_mlx_native_small" in names
        assert "whisper_mlx" in names

    def test_model_info_fields(self):
        resp = httpx.get(f"{MAIN_URL}/models", timeout=5)
        models = resp.json()
        for m in models:
            assert "name" in m
            assert "is_loaded" in m
            assert "supported_languages" in m


class TestMainLLM:
    """Main /llm/* endpoints"""

    def test_llm_models(self):
        resp = httpx.get(f"{MAIN_URL}/llm/models", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data

    def test_llm_models_has_enabled_field(self):
        resp = httpx.get(f"{MAIN_URL}/llm/models", timeout=5)
        data = resp.json()
        assert "enabled" in data
