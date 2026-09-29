package com.voiceinput.core

import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.Base64
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/**
 * 一次听写:按下开始,边录边传,松手([finish])后等结果。协议见 services/stt_server.py
 * 的 `/ws/stream`,做法照搬桌面端的 `LiveSession`(gui/src-tauri/src/stt.rs):
 *
 * - 连接在后台建,不挡录音:连上之前录到的音频先在本地攒着,连上后补发;
 * - 录到的全部音频一直留在本地。连不上、服务端太老(`ready` 里没有 `incremental`)、
 *   录音中或发完 `end` 后连接断了,松手后都换一条新连接把整段重发一次,不丢一个字;
 * - 服务端明确回了 error、超时、没听到声音,重发也没用,直接报错;
 * - [cancel] 告诉服务端 `cancel`,已经转好 / 正在转的段一律作废,不回调。
 *
 * 手机网络比桌面的局域网差得多(切 Wi‑Fi / 蜂窝、进电梯),「断了就整段重发」这一条
 * 在手机上更要紧。
 *
 * 线程:所有状态只在内部的单线程里改;[Listener] 也在这个线程上回调,界面要自己切回
 * 主线程。[appendAudio] / [finish] / [cancel] 可以在任何线程调用。
 */
class DictationSession(
    private val config: ServerConfig,
    private val http: OkHttpClient,
    private val listener: Listener,
) {
    enum class Stage {
        /** 录音中,正在连服务。 */
        CONNECTING,

        /** 录音中,边录边传。 */
        STREAMING,

        /** 录音中,但没连上(或连接断了):音频攒在本地,松手后一次性上传。 */
        RECORDING_OFFLINE,

        /** 松手后整段上传中(回退路径)。 */
        UPLOADING,

        /** 音频发完了,等服务端转写。 */
        TRANSCRIBING,

        /** 转写完了,LLM 在整理文字。 */
        POLISHING,
    }

    interface Listener {
        fun onStage(stage: Stage) {}

        /** 录音期间服务端转好的一段(只是进度,最终文本以 [onResult] 为准)。 */
        fun onSegment(index: Int, text: String) {}

        /** 最终文本。[llmError] 不为 null 时表示后处理开着却没做成,[text] 是原文。 */
        fun onResult(text: String, llmError: String?)

        fun onError(error: DictationError)
    }

    private val loop = Executors.newSingleThreadScheduledExecutor { r ->
        Thread(r, "dictation").apply { isDaemon = true }
    }

    // 以下字段只在 loop 线程上读写。

    /** 录到的全部音频(16 kHz 单声道 16 位小端 PCM)。 */
    private var audio = ByteArray(256 * 1024)
    private var audioLen = 0

    /** `audio` 里已经发给当前连接的字节数。 */
    private var sent = 0
    private var socket: WebSocket? = null

    /** 每建 / 弃一条连接加一。旧连接迟到的回调对不上号,直接丢掉。 */
    private var generation = 0

    /** false = 边录边传那条连接;true = 松手后整段上传的回退连接(只有一次)。 */
    private var uploading = false

    /** 当前连接收到了 ready、发过了 config,可以发音频了。 */
    private var ready = false

    /** 已经排了一次 [pump] 续发(发送队列满了,等它消化)。 */
    private var pumpScheduled = false

    /** 用户松手了。 */
    private var finished = false

    /** 当前连接上已经发了 `end`,在等结果。 */
    private var awaiting = false

    /** 出了结果、报了错或被取消:之后什么都不做。 */
    private var closed = false

    /** 目前拿到的最好的文本:先是 stt_result,再被 result 覆盖。 */
    private var lastText = ""

    /** 服务端在转写 / 后处理期间每 5 秒发一次 progress。收到过就能更快判断它挂了。 */
    private var sawKeepalive = false
    private var timer: ScheduledFuture<*>? = null

    fun start() = post { openSocket() }

    /** 录音回调里调用。会复制一份,调用方可以复用自己的缓冲区。 */
    fun appendAudio(pcm: ByteArray, length: Int = pcm.size) {
        if (length <= 0) return
        val copy = pcm.copyOf(length)
        post { onAudio(copy) }
    }

    /** 录音停了:发完剩下的音频、等结果。 */
    fun finish() = post { onFinish() }

    /** 放弃这段录音。之后不会再有任何回调。 */
    fun cancel() = post { onCancel() }

    private fun post(block: () -> Unit) {
        try {
            loop.execute(block)
        } catch (_: RejectedExecutionException) {
            // 已经结束,loop 关了。
        }
    }

    // ── 连接 ──

    private fun openSocket() {
        if (closed) return
        generation++
        val gen = generation
        ready = false
        awaiting = false
        sawKeepalive = false
        sent = 0
        listener.onStage(if (uploading) Stage.UPLOADING else Stage.CONNECTING)

        val request = Request.Builder()
            .url(config.url("/ws/stream"))
            .header("Accept-Language", config.acceptLanguage)
            .apply { config.token?.let { header("Authorization", "Bearer $it") } }
            .build()
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) =
                post { if (gen == generation) onServerMessage(webSocket, text) }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                val code = response?.code
                post {
                    if (gen == generation) onSocketLost(t.message ?: t.javaClass.simpleName, code)
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                post { if (gen == generation) onSocketLost("closed ($code $reason)", null) }
            }
        })
        schedule(READY_TIMEOUT_S) { onSocketLost("no ready message", null) }
    }

    /** 丢掉当前连接,之后它的回调一律忽略。 */
    private fun dropSocket() {
        socket?.cancel()
        socket = null
        generation++
        ready = false
        awaiting = false
        timer?.cancel(false)
    }

    private fun onSocketLost(reason: String, httpCode: Int?) {
        if (closed) return
        dropSocket()
        // 令牌不对时服务端在握手阶段就拒绝(stt_server.py 在 accept 之前 close(4401),
        // 到客户端这边是 HTTP 403)。换条连接重发也一样,直接报。
        if (httpCode == 401 || httpCode == 403) {
            fail(
                DictationError.Kind.UNAUTHORIZED,
                config.t(
                    "访问令牌缺失或不对,请在设置里填上服务端的 VIF_API_TOKEN",
                    "Missing or invalid access token. Enter the server's VIF_API_TOKEN in settings.",
                ),
            )
            return
        }
        when {
            uploading -> fail(
                DictationError.Kind.UNREACHABLE,
                config.t("连不上识别服务", "Can't reach the STT service") + " (${config.baseUrl}): $reason",
            )
            finished -> startUpload()
            else -> listener.onStage(Stage.RECORDING_OFFLINE)
        }
    }

    // ── 服务端消息 ──

    private fun onServerMessage(ws: WebSocket, text: String) {
        if (closed) return
        val msg = try {
            JSONObject(text)
        } catch (_: Exception) {
            return
        }
        when (msg.optString("type")) {
            "ready" -> onReady(ws, msg)
            "segment" -> listener.onSegment(msg.optInt("index"), msg.optString("text"))
            "stt_result" -> {
                msg.str("text")?.takeIf { it.isNotEmpty() }?.let { lastText = it }
            }
            "llm_start" -> listener.onStage(Stage.POLISHING)
            "progress" -> sawKeepalive = true
            "result" -> {
                msg.str("text")?.takeIf { it.isNotEmpty() }?.let { lastText = it }
                deliver(msg.str("llm_error")?.trim()?.takeIf { it.isNotEmpty() })
                return
            }
            "done" -> {
                deliver(null)
                return
            }
            "error" -> {
                val message = msg.str("error_message")?.takeIf { it.isNotBlank() }
                    ?: config.t("未知错误", "Unknown error")
                if (awaiting) {
                    fail(DictationError.Kind.SERVER, message)
                } else {
                    // 录音中服务端报错:这条连接不能用了,松手后整段重发(和桌面端一样)。
                    onSocketLost("server error: $message", null)
                }
                return
            }
        }
        if (awaiting) scheduleResultTimeout()
    }

    private fun onReady(ws: WebSocket, msg: JSONObject) {
        timer?.cancel(false)
        // 老服务端不支持边录边识别:这条连接没用,松手后按老办法整段上传。
        if (!uploading && !msg.optBoolean("incremental", false)) {
            dropSocket()
            if (finished) startUpload() else listener.onStage(Stage.RECORDING_OFFLINE)
            return
        }
        val cfg = JSONObject().put("type", "config").put("language", config.language)
        if (!uploading) cfg.put("incremental", true)
        if (!ws.send(cfg.toString())) {
            onSocketLost("send failed", null)
            return
        }
        ready = true
        if (!finished) listener.onStage(Stage.STREAMING)
        pump()
    }

    // ── 音频 ──

    private fun onAudio(chunk: ByteArray) {
        if (closed || finished) return
        if (audioLen + chunk.size > audio.size) {
            audio = audio.copyOf(maxOf(audio.size * 2, audioLen + chunk.size))
        }
        System.arraycopy(chunk, 0, audio, audioLen, chunk.size)
        audioLen += chunk.size
        pump()
    }

    /**
     * 把还没发的音频发出去;松手了且发完了就发 `end`。
     *
     * - 录音中攒够 [LIVE_FRAME_BYTES] 才发,别一个录音回调一条消息;
     * - 按 [MAX_FRAME_BYTES] 切块;
     * - OkHttp 的发送队列超过 16 MB 会直接关连接。长录音整段重发时 base64 后轻松超过,
     *   所以队列积压到 [QUEUE_HIGH_WATER] 就停下,过一会儿再接着发。
     */
    private fun pump() {
        val ws = socket ?: return
        if (!ready || awaiting || closed) return
        if (!finished && audioLen - sent < LIVE_FRAME_BYTES) return
        while (sent < audioLen) {
            if (ws.queueSize() >= QUEUE_HIGH_WATER) {
                schedulePump()
                return
            }
            val end = minOf(sent + MAX_FRAME_BYTES, audioLen)
            val b64 = Base64.getEncoder().encodeToString(audio.copyOfRange(sent, end))
            if (!ws.send("""{"type":"audio","data":"$b64"}""")) {
                onSocketLost("send failed", null)
                return
            }
            sent = end
        }
        if (finished) sendEnd(ws)
    }

    private fun schedulePump() {
        if (pumpScheduled) return
        pumpScheduled = true
        val gen = generation
        loop.schedule({
            pumpScheduled = false
            if (!closed && gen == generation) pump()
        }, 20, TimeUnit.MILLISECONDS)
    }

    // ── 松手 / 放弃 ──

    private fun onFinish() {
        if (closed || finished) return
        finished = true
        // 还在连时什么都不用做:ready 到了 pump 会发完再发 end;连不上会走 onSocketLost 整段上传。
        if (socket == null) startUpload() else pump()
    }

    private fun sendEnd(ws: WebSocket) {
        if (!ws.send("""{"type":"end"}""")) {
            onSocketLost("send failed", null)
            return
        }
        awaiting = true
        listener.onStage(Stage.TRANSCRIBING)
        scheduleResultTimeout()
    }

    private fun startUpload() {
        if (closed) return
        if (audioLen == 0) {
            fail(DictationError.Kind.NO_SPEECH, noSpeech())
            return
        }
        uploading = true
        openSocket()
    }

    private fun onCancel() {
        if (closed) return
        closed = true
        socket?.let {
            it.send("""{"type":"cancel"}""")
            it.close(1000, null)
        }
        socket = null
        generation++
        shutdown()
    }

    // ── 结束 ──

    private fun deliver(llmError: String?) {
        if (lastText.isBlank()) {
            fail(DictationError.Kind.NO_SPEECH, noSpeech())
            return
        }
        val text = lastText
        finishSocket()
        listener.onResult(text, llmError)
    }

    private fun fail(kind: DictationError.Kind, message: String) {
        finishSocket()
        listener.onError(DictationError(kind, message))
    }

    private fun finishSocket() {
        closed = true
        socket?.close(1000, null)
        socket = null
        generation++
        shutdown()
    }

    private fun shutdown() {
        timer?.cancel(false)
        loop.shutdown()
    }

    private fun noSpeech() = config.t("没有听到说话", "No speech detected")

    // ── 超时 ──

    private fun schedule(seconds: Long, onTimeout: () -> Unit) {
        timer?.cancel(false)
        val gen = generation
        timer = loop.schedule({ if (!closed && gen == generation) onTimeout() }, seconds, TimeUnit.SECONDS)
    }

    /** 等下一条服务端消息的上限:见过心跳后 60 秒,老服务端不发心跳只能等 5 分钟。 */
    private fun scheduleResultTimeout() {
        val wait = if (sawKeepalive) 60L else 300L
        schedule(wait) {
            fail(
                DictationError.Kind.TIMEOUT,
                config.t(
                    "识别服务 $wait 秒没有动静,可能已经卡住",
                    "The STT service has been silent for ${wait}s and may be stuck",
                ),
            )
        }
    }

    companion object {
        /** 边录边传时攒够这么多再发一条(和桌面端一样,约 0.25 秒)。 */
        const val LIVE_FRAME_BYTES = 8 * 1024

        /** 一条音频消息最多这么大(约 2 秒)。服务端单帧上限是 16 MB。 */
        const val MAX_FRAME_BYTES = 64 * 1024

        /** 发送队列积压到这么多就先停一停(OkHttp 到 16 MB 会关连接)。 */
        const val QUEUE_HIGH_WATER = 1L * 1024 * 1024

        /** 连上之后等 `ready` 的上限。连接本身的超时由 OkHttpClient 管。 */
        const val READY_TIMEOUT_S = 10L

        /** 推荐的 OkHttpClient 配置:连接超时短一点,读不设上限(结果超时自己管)。 */
        fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }
}

/**
 * 取字符串字段。JSON 里的 null 返回 null —— Android 自带的 org.json 的 `optString`
 * 碰到 null 会返回字符串 "null"。
 */
internal fun JSONObject.str(key: String): String? =
    if (!has(key) || isNull(key)) null else optString(key)
