package com.voiceinput.core

import kotlin.math.log10
import kotlin.math.sqrt

object Pcm {
    const val SAMPLE_RATE = 16_000

    /** 16 kHz 单声道 16 位:每秒这么多字节。 */
    const val BYTES_PER_SECOND = SAMPLE_RATE * 2

    /**
     * 一块 16 位小端 PCM 的音量,0..1,给录音时的音量条用。
     * 按 dBFS 线性映射:-60 dB 以下算 0,0 dB 算 1 —— 直接用 RMS 的话说话声只占条的一小截。
     */
    fun level(pcm: ByteArray, length: Int = pcm.size): Float {
        val samples = length / 2
        if (samples == 0) return 0f
        var sum = 0.0
        for (i in 0 until samples) {
            val lo = pcm[2 * i].toInt() and 0xFF
            val hi = pcm[2 * i + 1].toInt()
            val v = ((hi shl 8) or lo).toShort() / 32768.0
            sum += v * v
        }
        val rms = sqrt(sum / samples)
        if (rms <= 0.0) return 0f
        val db = 20 * log10(rms)
        return ((db + 60) / 60).coerceIn(0.0, 1.0).toFloat()
    }
}
