"""上传音频的解码:把用户给的文件变成模型要的 16 kHz 单声道 int16 PCM。

`/transcribe` 以前把上传的字节**原样当 16 kHz 单声道 PCM** 用。只有恰好是
16 kHz 单声道 WAV 时结果才对(44 字节的文件头被当成几个采样,听不出来);
本机实测:同一句话存成 44.1 kHz 立体声 WAV 或 m4a 上传,都返回 200 + 空文本
—— 失败了,却看不出是失败。

这里只依赖 numpy(CI 的测试环境里没有 scipy / soundfile):

- WAV:任意采样率、任意声道数、8/16/24/32 位整数 PCM,混成单声道再重采样;
- 没有 RIFF 头的字节流:按 16 kHz 单声道 int16 PCM 处理(和以前一致,
  WebSocket 路径和老调用方都是这么传的);
- 其它格式(mp3 / m4a / flac …):明确拒绝,告诉用户转成 WAV。
"""

from __future__ import annotations

import io
import wave

import numpy as np

from shared.i18n import bi

TARGET_RATE = 16000

# 常见压缩格式的文件头,用来给出「不支持这种格式」而不是「解码失败」。
_KNOWN_UNSUPPORTED = {
    b"ID3": "MP3",
    b"fLaC": "FLAC",
    b"OggS": "Ogg",
}


class UnsupportedAudio(ValueError):
    """上传的不是能解的音频格式。消息可直接给用户看(中英两份,见 shared/i18n.py)。"""


def _sniff_unsupported(data: bytes) -> str | None:
    for magic, name in _KNOWN_UNSUPPORTED.items():
        if data.startswith(magic):
            return name
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "M4A / MP4"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "MP3"
    return None


def _pcm_to_float(frames: bytes, sample_width: int) -> np.ndarray:
    if sample_width == 1:
        # 8 位 WAV 是无符号的,128 是零点
        return (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if sample_width == 2:
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if sample_width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        ints = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints & 0x800000, ints - (1 << 24), ints)
        return ints.astype(np.float32) / float(1 << 23)
    if sample_width == 4:
        return np.frombuffer(frames, dtype="<i4").astype(np.float32) / float(1 << 31)
    raise UnsupportedAudio(
        bi(f"不支持 {sample_width * 8} 位的 WAV", f"{sample_width * 8}-bit WAV is not supported")
    )


def resample(audio: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    """带抗混叠的重采样。降采样前先用加窗 sinc 低通,再线性插值取点。"""
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32)
    if dst_rate < src_rate:
        cutoff = 0.5 * dst_rate / src_rate  # 以源采样率归一化的截止频率
        taps = 63
        n = np.arange(taps) - (taps - 1) / 2
        kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        kernel /= kernel.sum()
        audio = np.convolve(audio, kernel, mode="same")
    duration = audio.size / src_rate
    dst_len = int(round(duration * dst_rate))
    src_t = np.arange(audio.size) / src_rate
    dst_t = np.arange(dst_len) / dst_rate
    return np.interp(dst_t, src_t, audio).astype(np.float32)


def decode_to_pcm16k(data: bytes) -> bytes:
    """把上传的音频解成 16 kHz 单声道 int16 小端 PCM。"""
    if data[:4] != b"RIFF":
        fmt = _sniff_unsupported(data)
        if fmt:
            raise UnsupportedAudio(
                bi(
                    f"暂不支持 {fmt} 格式,请先转成 WAV 再上传",
                    f"{fmt} is not supported yet; convert it to WAV before uploading",
                )
            )
        # 裸 PCM:和以前的行为一致。奇数长度丢掉最后半个采样。
        return data[: len(data) - len(data) % 2]

    try:
        with wave.open(io.BytesIO(data)) as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as e:
        # 常见原因是 IEEE float WAV(格式码 3),标准库 wave 不认
        raise UnsupportedAudio(
            bi(
                f"无法解析这个 WAV 文件({e}),请转成 16 位 PCM WAV",
                f"Could not parse this WAV file ({e}); convert it to 16-bit PCM WAV",
            )
        ) from e

    audio = _pcm_to_float(frames, width)
    if channels > 1:
        usable = audio.size - audio.size % channels
        audio = audio[:usable].reshape(-1, channels).mean(axis=1)
    audio = resample(audio, rate)
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
