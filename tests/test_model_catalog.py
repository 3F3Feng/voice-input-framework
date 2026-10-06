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


def test_load_progress_uses_the_download_meter_when_file_sizes_do_not_move():
    """新版 huggingface_hub 上缓存目录很久才动一下:看着一个字节没多,计数却在涨"""
    p = model_catalog.load_progress("m", 0, 0.0, 0, 3350, 42.0, metered=1_200_000_000)
    assert p["phase"] == "downloading"
    assert p["downloaded_bytes"] == 1_200_000_000
    assert p["cached_bytes"] == 1_200_000_000
    assert p["total_bytes"] == 3_350_000_000
    # 两边都有数时取大的;文件系统正常的系统上两者相等,不会翻倍
    both = model_catalog.load_progress("m", 500, 0.0, 0, None, 1.0, metered=500)
    assert (both["downloaded_bytes"], both["cached_bytes"]) == (500, 500)
    ahead = model_catalog.load_progress("m", 900, 0.0, 100, None, 1.0, metered=300)
    assert (ahead["downloaded_bytes"], ahead["cached_bytes"]) == (800, 900)


def test_download_meter_counts_byte_bars_only():
    """计数接在 huggingface_hub 的进度条上:按字节计的才算,显示关掉了也照样算"""
    import pytest

    pytest.importorskip("huggingface_hub")
    from huggingface_hub.utils import tqdm as hf_tqdm

    assert model_catalog.install_download_meter() is True
    # 再装几次也不会在同一个类上包两层(包了就会数两遍)
    model_catalog.DOWNLOAD_METER.installed = False
    assert model_catalog.install_download_meter() is True
    assert model_catalog.install_download_meter() is True

    start = model_catalog.DOWNLOAD_METER.snapshot()
    for disable in (False, True):
        with hf_tqdm(total=1000, unit="B", disable=disable) as bar:
            bar.update(300)
            bar.update(200)
        with hf_tqdm(total=5, unit="it", disable=disable) as files:  # 「Fetching N files」
            files.update(1)
    assert model_catalog.DOWNLOAD_METER.since(start) == 1000


def test_download_meter_does_not_add_network_bytes_to_file_bytes():
    """新版 huggingface_hub(Xet)一个文件报两条进度:网络上收到的、写进文件的。

    加在一起会数出文件大小的 1.5~1.9 倍(CI 上实测:1.19 MB 的文件数出 2.24 MB)。
    取大的那个:下载中跟着网络走,最后落在文件的真实大小上。
    """
    import pytest

    pytest.importorskip("huggingface_hub")
    from huggingface_hub.utils import tqdm as hf_tqdm

    assert model_catalog.install_download_meter() is True
    meter = model_catalog.DOWNLOAD_METER
    start = meter.snapshot()
    size = 1_185_376
    for disable in (False, True):
        begin = meter.snapshot()
        written = hf_tqdm(
            desc="tinyllamas/stories260K.gguf: reconstructing file",
            total=size,
            unit="B",
            disable=disable,
        )
        received = hf_tqdm(
            desc="tinyllamas/stories260K.gguf: downloading bytes",
            total=size,
            unit="B",
            disable=disable,
        )
        # 网络先到,文件还一个字节没写:进度已经在走
        received.update(400_000)
        assert meter.since(begin) == 400_000
        received.update(657_091)  # 压缩 / 去重过,比文件小
        assert meter.since(begin) == 1_057_091
        # 文件一次写完:落在真实大小上,不是两者之和
        written.update(size)
        assert meter.since(begin) == size
        written.close()
        received.close()
    # 整仓库下载时那条叫「Downloading bytes」
    begin = meter.snapshot()
    with hf_tqdm(desc="Downloading bytes", total=0, unit="B") as received:
        received.update(10)
    with hf_tqdm(desc="Reconstructing (incomplete total...)", total=0, unit="B") as written:
        written.update(25)
    assert meter.since(begin) == 25
    assert meter.since(start) == 2 * size + 25
    assert meter.since(meter.snapshot()) == 0
