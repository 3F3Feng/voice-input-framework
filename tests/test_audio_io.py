"""上传音频解码(R37:/transcribe 以前把任何文件都当 16 kHz 单声道 PCM)"""

import io
import wave

import numpy as np
import pytest

from services.audio_io import UnsupportedAudio, decode_to_pcm16k, resample


def _wav(audio: np.ndarray, rate: int, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        data = np.repeat(audio[:, None], channels, axis=1).reshape(-1)
        if width == 2:
            w.writeframes((data * 32767).astype("<i2").tobytes())
        elif width == 1:
            w.writeframes((data * 127 + 128).astype(np.uint8).tobytes())
    return buf.getvalue()


def _tone(rate: int, secs: float = 1.0, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(rate * secs)) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _peak_freq(pcm: bytes, rate: int = 16000) -> float:
    a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    spectrum = np.abs(np.fft.rfft(a))
    return float(np.fft.rfftfreq(a.size, 1 / rate)[spectrum.argmax()])


@pytest.mark.parametrize("rate,channels", [(16000, 1), (44100, 2), (48000, 1), (8000, 1)])
def test_wav_is_converted_to_16k_mono(rate, channels):
    pcm = decode_to_pcm16k(_wav(_tone(rate), rate, channels))
    samples = len(pcm) // 2
    assert abs(samples - 16000) <= 2  # 时长不变
    assert abs(_peak_freq(pcm) - 440) < 5  # 音高不变(采样率换算对了)


def test_8bit_wav():
    pcm = decode_to_pcm16k(_wav(_tone(16000), 16000, width=1))
    assert abs(_peak_freq(pcm) - 440) < 5


def test_raw_pcm_passes_through():
    raw = (_tone(16000) * 32767).astype("<i2").tobytes()
    assert decode_to_pcm16k(raw) == raw
    assert len(decode_to_pcm16k(raw + b"\x01")) == len(raw)


@pytest.mark.parametrize(
    "head,name",
    [(b"ID3\x04" + b"\x00" * 20, "MP3"), (b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8, "M4A")],
)
def test_compressed_formats_are_rejected_with_a_reason(head, name):
    with pytest.raises(UnsupportedAudio, match=name):
        decode_to_pcm16k(head)


def test_downsampling_suppresses_aliasing():
    """48 kHz 里 12 kHz 的成分超出 16 kHz 的奈奎斯特频率,降采样后应被滤掉,
    而不是折叠成一个 4 kHz 的假音。"""
    out = resample(_tone(48000, freq=12000), 48000)
    assert np.sqrt(np.mean(out**2)) < 0.05
