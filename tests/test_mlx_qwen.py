#!/usr/bin/env python3
"""
MLX-Audio Qwen3-ASR API test script.
Tests loading, transcription, streaming, and various input formats.
"""

import time
import numpy as np
import mlx.core as mx
from mlx_audio.stt import load

# ──────────────────────────────────────────────
# 1. Model Loading
# ──────────────────────────────────────────────

def test_load_model(model_name="mlx-community/Qwen3-ASR-0.6B-4bit"):
    """Test loading model from HuggingFace."""
    print(f"\n=== Loading model: {model_name} ===")
    start = time.time()
    model = load(model_name)
    elapsed = time.time() - start
    print(f"  ✓ Model loaded in {elapsed:.2f}s")
    print(f"  Type: {type(model)}")
    print(f"  Model type: {model.config.model_type}")
    print(f"  Supported languages: {model.config.support_languages}")
    print(f"  Sample rate: {model.sample_rate}")
    return model


# ──────────────────────────────────────────────
# 2. Transcription (non-streaming)
# ──────────────────────────────────────────────

def test_transcribe_file(model, audio_path, language="English"):
    """Test transcription from file path."""
    print(f"\n=== Transcribe from file: {audio_path} ===")
    start = time.time()
    result = model.generate(audio_path, language=language)
    elapsed = time.time() - start
    print(f"  ✓ Text: {result.text!r}")
    print(f"  Language: {result.language}")
    print(f"  Prompt tokens: {result.prompt_tokens}")
    print(f"  Generation tokens: {result.generation_tokens}")
    print(f"  Total time: {elapsed:.2f}s")
    print(f"  Generation TPS: {result.generation_tps:.2f}")
    return result


def test_transcribe_numpy(model, audio_path, language="English"):
    """Test transcription from numpy/mx.array input."""
    print(f"\n=== Transcribe from mx.array ===")
    from mlx_audio.stt.utils import load_audio
    audio = load_audio(audio_path)
    print(f"  Audio shape: {audio.shape}, dtype: {audio.dtype}")
    start = time.time()
    result = model.generate(audio, language=language)
    elapsed = time.time() - start
    print(f"  ✓ Text: {result.text!r}")
    print(f"  Total time: {elapsed:.2f}s")
    return result


def test_transcribe_auto_language(model, audio_path):
    """Test auto language detection."""
    print(f"\n=== Auto language detection ===")
    result = model.generate(audio_path)
    print(f"  ✓ Text: {result.text!r}")
    print(f"  Detected language: {result.language}")
    return result


# ──────────────────────────────────────────────
# 3. Streaming transcription
# ──────────────────────────────────────────────

def test_stream_transcribe(model, audio_path, language="English"):
    """Test streaming transcription token-by-token."""
    print(f"\n=== Streaming transcription ===")
    start = time.time()
    full_text = ""
    token_count = 0
    for result in model.generate(audio_path, language=language, stream=True):
        if result.text:
            full_text += result.text
            token_count += 1
        if result.is_final:
            print(f"  [FINAL] prompt_tokens={result.prompt_tokens} gen_tokens={result.generation_tokens}")
    elapsed = time.time() - start
    print(f"  ✓ Full text: {full_text!r}")
    print(f"  Tokens streamed: {token_count}")
    print(f"  Total streaming time: {elapsed:.2f}s")
    return full_text


# ──────────────────────────────────────────────
# 4. High-level API (generate_transcription)
# ──────────────────────────────────────────────

def test_high_level_api(model_name, audio_path, language="English"):
    """Test the high-level generate_transcription function."""
    print(f"\n=== High-level API (generate_transcription) ===")
    from mlx_audio.stt.generate import generate_transcription
    start = time.time()
    result = generate_transcription(
        model=model_name,
        audio=audio_path,
        language=language,
        format="json",
        output_path="/tmp/test_mlx_qwen",
    )
    elapsed = time.time() - start
    print(f"  ✓ Text: {result.text!r}")
    print(f"  Total time: {elapsed:.2f}s")
    return result


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

if __name__ == "__main__":
    MODEL_SMALL = "mlx-community/Qwen3-ASR-0.6B-4bit"
    MODEL_LARGE = "mlx-community/Qwen3-ASR-1.7B-8bit"
    TEST_AUDIO = ".venv/lib/python3.11/site-packages/gradio/media_assets/audio/cate_blanch.mp3"

    model = test_load_model(MODEL_SMALL)
    test_transcribe_file(model, TEST_AUDIO)
    test_transcribe_numpy(model, TEST_AUDIO)
    test_transcribe_auto_language(model, TEST_AUDIO)
    test_stream_transcribe(model, TEST_AUDIO)
    test_high_level_api(MODEL_SMALL, TEST_AUDIO)

    print("\n✅ All tests passed!")
