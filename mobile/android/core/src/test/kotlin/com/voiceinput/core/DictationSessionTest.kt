package com.voiceinput.core

import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.util.Base64
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.random.Random

/**
 * 用 MockWebServer 扮演 `/ws/stream`(services/stt_server.py),按协议逐条对。
 */
class DictationSessionTest {
    private lateinit var server: MockWebServer
    private val http = DictationSession.defaultHttpClient()

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /** 一条 WS 连接上服务端的剧本。 */
    private class FakeConnection(
        val incremental: Boolean = true,
        /** 录音中收到这么多音频字节后服务端断开。 */
        val dropAfterBytes: Int? = null,
        /** 收到 end 后不回结果、直接断开。 */
        val dropAfterEnd: Boolean = false,
        /** 收到 end 后依次回这些消息。 */
        val replies: List<String> = listOf(
            """{"type":"stt_result","text":"raw text"}""",
            """{"type":"llm_start","text":"raw text"}""",
            """{"type":"progress","stage":"llm","elapsed_s":5.0}""",
            """{"type":"result","text":"Polished text.","llm_error":null}""",
            """{"type":"done"}""",
        ),
    ) : WebSocketListener() {
        val received = CopyOnWriteArrayList<JSONObject>()
        val audio = ByteArrayOutputStream()
        val closed = CountDownLatch(1)

        override fun onOpen(webSocket: WebSocket, response: Response) {
            webSocket.send(
                JSONObject().put("type", "ready").put("model", "fake")
                    .put("is_loading", false).apply { if (incremental) put("incremental", true) }
                    .toString(),
            )
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val msg = JSONObject(text)
            received.add(msg)
            when (msg.getString("type")) {
                "config" -> webSocket.send("""{"type":"config_ack"}""")
                "audio" -> {
                    synchronized(audio) { audio.write(Base64.getDecoder().decode(msg.getString("data"))) }
                    if (dropAfterBytes != null && audio.size() >= dropAfterBytes) drop(webSocket)
                }
                "end" -> if (dropAfterEnd) drop(webSocket) else replies.forEach { webSocket.send(it) }
                "cancel" -> webSocket.close(1000, null)
            }
        }

        /**
         * 服务端这头断开连接(服务重启、网络切换)。MockWebServer 4 里服务端的 WebSocket
         * 没有 Call,`cancel()` 断不掉,只能发 close 帧;客户端走的是同一条「连接没了」的路。
         */
        private fun drop(webSocket: WebSocket) {
            webSocket.close(1001, "going away")
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
            closed.countDown()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            closed.countDown()
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            closed.countDown()
        }

        fun types() = received.map { it.getString("type") }
        fun config() = received.first { it.getString("type") == "config" }
    }

    private class Recorder : DictationSession.Listener {
        val stages = CopyOnWriteArrayList<DictationSession.Stage>()
        val done = CountDownLatch(1)

        @Volatile var text: String? = null

        @Volatile var llmError: String? = null

        @Volatile var error: DictationError? = null

        override fun onStage(stage: DictationSession.Stage) {
            stages.add(stage)
        }

        override fun onResult(text: String, llmError: String?) {
            this.text = text
            this.llmError = llmError
            done.countDown()
        }

        override fun onError(error: DictationError) {
            this.error = error
            done.countDown()
        }

        fun await() = assertTrue("session did not finish", done.await(10, TimeUnit.SECONDS))
    }

    private fun enqueue(conn: FakeConnection) =
        server.enqueue(MockResponse().withWebSocketUpgrade(conn))

    private fun session(rec: Recorder, token: String? = null) = DictationSession(
        ServerConfig(server.url("/").toString(), token = token, language = "zh"),
        http,
        rec,
    )

    private fun pcm(bytes: Int) = Random(42).nextBytes(bytes)

    /** 模拟录音回调:每 100 ms 一块(3200 字节),不真的等。 */
    private fun feed(s: DictationSession, audio: ByteArray, block: Int = 3200) {
        var i = 0
        while (i < audio.size) {
            val end = minOf(i + block, audio.size)
            s.appendAudio(audio.copyOfRange(i, end))
            i = end
        }
    }

    @Test
    fun streamsWhileRecordingAndReturnsPolishedText() {
        val conn = FakeConnection()
        enqueue(conn)
        val rec = Recorder()
        val s = session(rec, token = "secret")
        s.start()
        val audio = pcm(40_000)
        feed(s, audio)
        s.finish()
        rec.await()

        assertEquals("Polished text.", rec.text)
        assertNull(rec.llmError)
        assertArrayEquals(audio, conn.audio.toByteArray())
        assertEquals("zh", conn.config().getString("language"))
        assertTrue(conn.config().getBoolean("incremental"))
        assertEquals("end", conn.types().last())
        assertEquals("Bearer secret", server.takeRequest().getHeader("Authorization"))
        assertTrue(DictationSession.Stage.POLISHING in rec.stages)
    }

    @Test
    fun audioRecordedBeforeConnectingIsNotLost() {
        val conn = FakeConnection()
        enqueue(conn)
        val rec = Recorder()
        val s = session(rec)
        // 连接还没建就已经录了一大段、甚至已经松手。
        val audio = pcm(100_000)
        feed(s, audio)
        s.start()
        s.finish()
        rec.await()
        assertEquals("Polished text.", rec.text)
        assertArrayEquals(audio, conn.audio.toByteArray())
    }

    @Test
    fun oldServerWithoutIncrementalGetsWholeUploadAfterStop() {
        val old = FakeConnection(incremental = false)
        val upload = FakeConnection(incremental = false)
        enqueue(old)
        enqueue(upload)
        val rec = Recorder()
        val s = session(rec)
        s.start()
        Thread.sleep(300) // 让第一条连接先收到 ready、被丢掉
        val audio = pcm(20_000)
        feed(s, audio)
        s.finish()
        rec.await()

        assertEquals("Polished text.", rec.text)
        assertTrue("old connection must not get audio", old.received.isEmpty())
        assertArrayEquals(audio, upload.audio.toByteArray())
        assertFalse(upload.config().has("incremental"))
        assertTrue(DictationSession.Stage.RECORDING_OFFLINE in rec.stages)
    }

    @Test
    fun connectionDroppedWhileRecordingResendsEverything() {
        val live = FakeConnection(dropAfterBytes = 16 * 1024)
        val upload = FakeConnection()
        enqueue(live)
        enqueue(upload)
        val rec = Recorder()
        val s = session(rec)
        s.start()
        Thread.sleep(300)
        val audio = pcm(60_000)
        feed(s, audio.copyOfRange(0, 30_000))
        assertTrue(live.closed.await(5, TimeUnit.SECONDS))
        Thread.sleep(200)
        feed(s, audio.copyOfRange(30_000, audio.size))
        s.finish()
        rec.await()

        assertEquals("Polished text.", rec.text)
        assertArrayEquals(audio, upload.audio.toByteArray())
    }

    @Test
    fun connectionDroppedAfterEndResendsOnce() {
        enqueue(FakeConnection(dropAfterEnd = true))
        val upload = FakeConnection()
        enqueue(upload)
        val rec = Recorder()
        val s = session(rec)
        s.start()
        val audio = pcm(12_000)
        feed(s, audio)
        s.finish()
        rec.await()
        assertEquals("Polished text.", rec.text)
        assertArrayEquals(audio, upload.audio.toByteArray())
    }

    @Test
    fun secondFailureIsReportedNotRetriedForever() {
        enqueue(FakeConnection(dropAfterEnd = true))
        enqueue(FakeConnection(dropAfterEnd = true))
        val rec = Recorder()
        val s = session(rec)
        s.start()
        feed(s, pcm(12_000))
        s.finish()
        rec.await()
        assertEquals(DictationError.Kind.UNREACHABLE, rec.error?.kind)
        assertEquals(2, server.requestCount)
    }

    @Test
    fun wrongTokenIsReportedWithoutRetry() {
        // 服务端令牌不对时在 accept 之前 close(4401),到客户端是握手 403。
        server.enqueue(MockResponse().setResponseCode(403))
        val rec = Recorder()
        val s = session(rec, token = "wrong")
        s.start()
        feed(s, pcm(8_000))
        s.finish()
        rec.await()
        assertEquals(DictationError.Kind.UNAUTHORIZED, rec.error?.kind)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun silenceIsNoSpeech() {
        enqueue(
            FakeConnection(
                replies = listOf(
                    """{"type":"stt_result","text":""}""",
                    """{"type":"result","text":"","llm_error":null}""",
                ),
            ),
        )
        val rec = Recorder()
        val s = session(rec)
        s.start()
        feed(s, ByteArray(16_000))
        s.finish()
        rec.await()
        assertEquals(DictationError.Kind.NO_SPEECH, rec.error?.kind)
    }

    @Test
    fun llmFailureStillDeliversRawText() {
        enqueue(
            FakeConnection(
                replies = listOf(
                    """{"type":"stt_result","text":"raw text"}""",
                    """{"type":"result","text":"raw text","llm_error":"LLM service down"}""",
                ),
            ),
        )
        val rec = Recorder()
        val s = session(rec)
        s.start()
        feed(s, pcm(8_000))
        s.finish()
        rec.await()
        assertEquals("raw text", rec.text)
        assertEquals("LLM service down", rec.llmError)
    }

    @Test
    fun serverErrorAfterEndIsReported() {
        enqueue(
            FakeConnection(
                replies = listOf("""{"type":"error","error_code":"E5001","error_message":"model exploded"}"""),
            ),
        )
        val rec = Recorder()
        val s = session(rec)
        s.start()
        feed(s, pcm(8_000))
        s.finish()
        rec.await()
        assertEquals(DictationError.Kind.SERVER, rec.error?.kind)
        assertEquals("model exploded", rec.error?.message)
    }

    @Test
    fun cancelTellsServerAndNeverCallsBack() {
        val conn = FakeConnection()
        enqueue(conn)
        val rec = Recorder()
        val s = session(rec)
        s.start()
        feed(s, pcm(20_000))
        Thread.sleep(300)
        s.cancel()
        assertTrue(conn.closed.await(5, TimeUnit.SECONDS))
        assertEquals("cancel", conn.types().last())
        assertFalse(rec.done.await(500, TimeUnit.MILLISECONDS))
    }

    @Test
    fun unreachableServerFailsAfterStop() {
        server.shutdown()
        val rec = Recorder()
        val s = DictationSession(ServerConfig(server.url("/").toString()), http, rec)
        s.start()
        feed(s, pcm(8_000))
        s.finish()
        rec.await()
        assertEquals(DictationError.Kind.UNREACHABLE, rec.error?.kind)
    }

    @Test
    fun longUploadDoesNotOverflowOkHttpQueue() {
        // 12 分钟的录音 base64 后约 30 MB,一口气塞进 OkHttp 会超过它 16 MB 的发送队列、被关连接。
        val upload = FakeConnection(incremental = false)
        server.enqueue(MockResponse().setResponseCode(500)) // 边录边传那条连不上
        enqueue(upload)
        val rec = Recorder()
        val s = session(rec)
        val audio = pcm(12 * 60 * Pcm.BYTES_PER_SECOND)
        feed(s, audio, block = 64 * 1024)
        s.start()
        s.finish()
        assertTrue(rec.done.await(60, TimeUnit.SECONDS))
        assertEquals("Polished text.", rec.text)
        assertEquals(audio.size, upload.audio.size())
    }
}
