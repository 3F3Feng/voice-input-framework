package com.voiceinput.ime

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import com.voiceinput.core.Pcm

/**
 * 麦克风 → 16 kHz 单声道 16 位 PCM,正好是服务端要的格式,不用重采样。
 *
 * 用 VOICE_RECOGNITION 音源:系统会关掉为通话做的自动增益 / 降噪,给识别用的原声更好。
 * [onAudio] 和 [onLevel] 在采集线程上回调。
 */
class AudioCapture(
    private val onAudio: (ByteArray, Int) -> Unit,
    private val onLevel: (Float) -> Unit,
) {
    @Volatile private var running = false
    private var thread: Thread? = null

    /** 开始录音。没权限、麦克风被别的应用占着时返回 false。 */
    @SuppressLint("MissingPermission") // 调用方(VoiceKeyboardService)先查了权限
    fun start(): Boolean {
        val minBuf = AudioRecord.getMinBufferSize(
            Pcm.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf <= 0) return false
        val record = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                Pcm.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf, CHUNK_BYTES * 4),
            )
        } catch (_: SecurityException) {
            return false
        } catch (_: IllegalArgumentException) {
            return false
        }
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            return false
        }
        record.startRecording()
        if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            record.release()
            return false
        }
        running = true
        thread = Thread({
            val buf = ByteArray(CHUNK_BYTES)
            try {
                while (running) {
                    val n = record.read(buf, 0, buf.size)
                    if (n < 0) break
                    if (n > 0) {
                        onAudio(buf, n)
                        onLevel(Pcm.level(buf, n))
                    }
                }
            } finally {
                record.stop()
                record.release()
            }
        }, "audio-capture").apply { start() }
        return true
    }

    /**
     * 停止录音。等采集线程把最后一块交出去再返回,这样紧接着的 `session.finish()`
     * 一定排在所有音频后面。
     */
    fun stop() {
        running = false
        thread?.join(500)
        thread = null
    }

    companion object {
        /** 每 100 ms 交一块。 */
        const val CHUNK_BYTES = Pcm.BYTES_PER_SECOND / 10
    }
}
