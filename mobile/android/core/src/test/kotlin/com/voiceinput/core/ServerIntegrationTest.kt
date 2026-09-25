package com.voiceinput.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.random.Random

/**
 * 对真的 services/stt_server.py 跑一遍(转写和 LLM 是假的,见 mobile/tools/fake_stt_server.py)。
 * 默认跳过;要跑就先起服务,再 `VIF_TEST_SERVER=http://127.0.0.1:6544 VIF_TEST_TOKEN=secret gradle :core:test`。
 */
class ServerIntegrationTest {
    private val base = System.getenv("VIF_TEST_SERVER").orEmpty()
    private val token = System.getenv("VIF_TEST_TOKEN")?.takeIf { it.isNotEmpty() }
    private val http = DictationSession.defaultHttpClient()

    @Before
    fun requireServer() = assumeTrue("VIF_TEST_SERVER not set", base.isNotEmpty())

    private class Outcome : DictationSession.Listener {
        val done = CountDownLatch(1)
        val segments = mutableListOf<String>()

        @Volatile var text: String? = null

        @Volatile var error: DictationError? = null

        override fun onSegment(index: Int, text: String) {
            synchronized(segments) { segments.add(text) }
        }

        override fun onResult(text: String, llmError: String?) {
            this.text = text
            done.countDown()
        }

        override fun onError(error: DictationError) {
            this.error = error
            done.countDown()
        }
    }

    private fun dictate(audio: ByteArray, config: ServerConfig = ServerConfig(base, token)): Outcome {
        val out = Outcome()
        val s = DictationSession(config, http, out)
        s.start()
        var i = 0
        while (i < audio.size) {
            val end = minOf(i + 3200, audio.size)
            s.appendAudio(audio.copyOfRange(i, end))
            i = end
        }
        s.finish()
        assertTrue(out.done.await(60, TimeUnit.SECONDS))
        return out
    }

    /** 不是静音就行:假转写只看长度。 */
    private fun speech(seconds: Double) = Random(1).nextBytes((seconds * Pcm.BYTES_PER_SECOND).toInt() and 1.inv())

    @Test
    fun shortDictation() {
        val out = dictate(speech(3.2))
        assertEquals(null, out.error)
        assertEquals("heard 3.2 seconds [polished]", out.text)
    }

    @Test
    fun longDictationIsSegmentedWhileRecording() {
        // 超过 SEGMENT_MAX_S(28 秒),服务端录音期间就会先转一段(services/segmenter.py)。
        val out = dictate(speech(45.0))
        assertEquals(null, out.error)
        assertTrue("expected a segment while recording", out.segments.isNotEmpty())
        assertTrue(out.text!!.endsWith("[polished]"))
    }

    @Test
    fun silenceIsNoSpeech() {
        val out = dictate(ByteArray(2 * Pcm.BYTES_PER_SECOND))
        assertEquals(DictationError.Kind.NO_SPEECH, out.error?.kind)
    }

    @Test
    fun wrongToken() {
        assumeTrue("server runs without a token", token != null)
        val out = dictate(speech(1.0), ServerConfig(base, "definitely-wrong"))
        assertEquals(DictationError.Kind.UNAUTHORIZED, out.error?.kind)
    }

    @Test
    fun serverCheck() {
        val ok = ServerCheck.run(ServerConfig(base, token, english = true), http)
        assertTrue(ok.message, ok.ok)
        if (token != null) {
            val bad = ServerCheck.run(ServerConfig(base, "definitely-wrong", english = true), http)
            assertEquals(false, bad.ok)
            assertEquals("The access token is wrong", bad.message)
        }
    }
}
