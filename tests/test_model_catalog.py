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


def test_cache_bytes_counts_files_moved_into_snapshots(monkeypatch, tmp_path):
    """Windows 上建不了软链时,下完的文件从 blobs/ 被挪进 snapshots/:进度不能倒退"""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    root = tmp_path / "models--org--m"
    blobs = root / "blobs"
    snap = root / "snapshots" / "abc"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    (blobs / "big.incomplete").write_bytes(b"x" * 300)
    assert model_catalog.cache_bytes("org/m") == 300
    # 下完:没有软链的系统上是直接挪过去的真实文件
    (blobs / "big.incomplete").rename(snap / "model.safetensors")
    assert model_catalog.cache_bytes("org/m") == 300
    # 下一个文件开始下
    (blobs / "next.incomplete").write_bytes(b"x" * 40)
    assert model_catalog.cache_bytes("org/m") == 340


def test_cache_bytes_does_not_double_count_symlinked_snapshots(monkeypatch, tmp_path):
    """有软链的系统(macOS / Linux):snapshots/ 里是指向 blobs/ 的链接,只算一次"""
    import os

    import pytest

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    root = tmp_path / "models--org--m"
    blobs = root / "blobs"
    snap = root / "snapshots" / "abc"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    (blobs / "deadbeef").write_bytes(b"x" * 500)
    try:
        os.symlink(blobs / "deadbeef", snap / "model.safetensors")
    except (OSError, NotImplementedError):
        pytest.skip("this system can't create symlinks")
    assert model_catalog.cache_bytes("org/m") == 500


def test_load_progress_reports_totals_in_decimal_megabytes():
    p = model_catalog.load_progress("m", 405_000_000, 100.0, 105_000_000, 1620, 130.4)
    assert p == {
        "model": "m",
        "elapsed_s": 30.4,
        "downloaded_bytes": 300_000_000,
        "cached_bytes": 405_000_000,
        "total_bytes": 1_620_000_000,
        "phase": "downloading",
    }
    # 模型已经在本地:没在下载;不知道总大小时分母为空
    idle = model_catalog.load_progress("m", 700, 0.0, 700, None, 2.0)
    assert idle["phase"] == "loading" and idle["downloaded_bytes"] == 0
    assert idle["total_bytes"] is None


def test_every_downloadable_model_has_a_download_size():
    """界面拿 download_mb 当下载进度的分母、「需下载约 X」的数字"""
    from shared.model_registry import MODELS_CONFIG

    for name, info in MODELS_CONFIG.items():
        if "/" in info["model_id"]:
            assert info.get("download_mb", 0) > 0, name
            assert model_catalog.describe(name)["download_mb"] == info["download_mb"]
        else:
            assert "download_mb" not in info, name


def test_llm_backends_have_a_download_size_for_every_model():
    from services.llm_server import LlamaCppBackend, MLXBackend

    for backend in (MLXBackend, LlamaCppBackend):
        assert set(backend.DOWNLOAD_MB) == set(backend.MODEL_IDS), backend.name
    # GGUF:一个仓库里有十几种量化,只下一个文件;数进度看的是仓库目录
    assert LlamaCppBackend.repo_of("org/repo-GGUF/file-Q4.gguf") == "org/repo-GGUF"
    assert MLXBackend.repo_of("mlx-community/x-4bit") == "mlx-community/x-4bit"
