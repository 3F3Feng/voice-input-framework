package com.voiceinput.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PcmTest {
    private fun tone(amplitude: Short, samples: Int = 1600): ByteArray {
        val out = ByteArray(samples * 2)
        for (i in 0 until samples) {
            val v = if (i % 2 == 0) amplitude.toInt() else -amplitude.toInt()
            out[2 * i] = (v and 0xFF).toByte()
            out[2 * i + 1] = ((v shr 8) and 0xFF).toByte()
        }
        return out
    }

    @Test
    fun silenceIsZeroAndFullScaleIsOne() {
        assertEquals(0f, Pcm.level(ByteArray(3200)))
        assertEquals(1f, Pcm.level(tone(Short.MAX_VALUE)), 0.01f)
    }

    @Test
    fun louderIsHigher() {
        assertTrue(Pcm.level(tone(3000)) > Pcm.level(tone(300)))
    }
}
