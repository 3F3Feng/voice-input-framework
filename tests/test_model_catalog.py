"""模型目录(F5):描述、本机能不能跑、下没下载"""

from services import model_catalog


def test_every_model_has_a_readable_description():
    from shared.model_registry import MODELS_CONFIG

    for name in MODELS_CONFIG:
        d = model_catalog.describe(name)
        assert d["description"] and not d["description"].startswith("STT model:")
        assert isinstance(d["available"], bool)
        assert d["available"] == (d["unavailable_reason"] is None)


def test_apple_only_models_are_unavailable_elsewhere(monkeypatch):
    monkeypatch.setattr(model_catalog, "IS_APPLE_SILICON", False)
    reason = model_catalog.unavailable_reason(
        {"engine": "whisper_mlx", "requires_apple_silicon": True}
    )
    assert "Apple Silicon" in reason


def test_whisper_cpp_needs_a_local_build(monkeypatch, tmp_path):
    monkeypatch.setattr(model_catalog.Path, "home", lambda: tmp_path)
    assert "whisper.cpp" in model_catalog.unavailable_reason({"engine": "whisper_cpp"})


def test_missing_package_is_reported(monkeypatch):
    monkeypatch.setattr(model_catalog, "IS_APPLE_SILICON", True)
    monkeypatch.setattr(model_catalog, "_has_package", lambda name: False)
    reason = model_catalog.unavailable_reason({"engine": "qwen_asr_mlx_native"})
    assert "mlx_audio" in reason


def test_download_state_follows_the_hf_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    info = {"model_id": "org/some-model"}
    assert model_catalog.is_downloaded(info) is False
    (tmp_path / "models--org--some-model" / "snapshots" / "abc").mkdir(parents=True)
    assert model_catalog.is_downloaded(info) is True
    assert model_catalog.is_downloaded({"model_id": "whisper_cpp_base"}) is None


def test_cache_bytes_counts_blobs_including_incomplete(monkeypatch, tmp_path):
    """下载进度按缓存目录增长算(F4)"""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    blobs = tmp_path / "models--org--m" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "a").write_bytes(b"x" * 100)
    (blobs / "b.incomplete").write_bytes(b"x" * 50)
    assert model_catalog.cache_bytes("org/m") == 150
    assert model_catalog.cache_bytes("org/missing") == 0
    assert model_catalog.cache_bytes("not-a-hf-id") == 0
