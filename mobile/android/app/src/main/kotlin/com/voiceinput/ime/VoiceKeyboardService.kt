package com.voiceinput.ime

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.inputmethodservice.InputMethodService
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import com.voiceinput.core.DictationError
import com.voiceinput.core.DictationSession
import com.voiceinput.core.DictationSession.Stage
import okhttp3.OkHttpClient

/**
 * 语音输入法。点一下麦克风开始、再点一下结束;或者按住说话、松手结束。
 * 边录边传给 STT 服务(见 core 模块的 [DictationSession]),结果直接插进当前输入框。
 *
 * 另外声明成了「语音」输入法(res/xml/method.xml 的 imeSubtypeMode="voice"),
 * 支持的键盘(AOSP 键盘、HeliBoard、FlorisBoard 等)上的麦克风键可以直接切过来。
 */
class VoiceKeyboardService : InputMethodService() {
    private lateinit var settings: Settings
    private val http: OkHttpClient by lazy { DictationSession.defaultHttpClient() }
    private val main = Handler(Looper.getMainLooper())

    private var view: KeyboardView? = null
    private var mode = KeyboardView.Mode.IDLE
    private var session: DictationSession? = null
    private var capture: AudioCapture? = null

    /** 每开始 / 放弃一次加一。旧会话迟到的回调对不上号,丢掉。 */
    private var dictationId = 0

    /** 这一次按下是不是开始了录音(是的话,按住够久再松手就算结束)。 */
    private var pressStarted = false
    private var pressedAt = 0L
    private var recordingSince = 0L

    /** 最近一条结果,「↺」键再插一次用;键盘收起时出的结果也先放在这里。 */
    private var lastResult: String? = null

    private val ticker = object : Runnable {
        override fun run() {
            if (mode != KeyboardView.Mode.RECORDING) return
            val s = (SystemClock.elapsedRealtime() - recordingSince) / 1000
            view?.setStatus(recordingStatus + "  " + "%d:%02d".format(s / 60, s % 60))
            main.postDelayed(this, 500)
        }
    }
    private var recordingStatus = ""

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
    }

    override fun onCreateInputView(): View {
        val v = KeyboardView(this, actions)
        view = v
        v.setReinsertAvailable(lastResult != null)
        return v
    }

    override fun onStartInputView(info: EditorInfo, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        val v = view ?: return
        v.setSwitchKeyVisible(shouldOfferSwitchingToNextInputMethod())
        v.setEnterLabel(enterLabel(info))
        if (mode == KeyboardView.Mode.IDLE) v.setMode(KeyboardView.Mode.IDLE, idleHint())
    }

    override fun onFinishInputView(finishingInput: Boolean) {
        // 键盘收起了还在录,多半是用户走开了:别在后台偷偷录下去。
        if (mode == KeyboardView.Mode.RECORDING) cancelDictation()
        super.onFinishInputView(finishingInput)
    }

    override fun onDestroy() {
        cancelDictation()
        super.onDestroy()
    }

    private val actions = object : KeyboardView.Actions {
        override fun onMicDown() {
            pressedAt = SystemClock.elapsedRealtime()
            when (mode) {
                KeyboardView.Mode.IDLE -> pressStarted = startDictation()
                KeyboardView.Mode.RECORDING -> {
                    pressStarted = false
                    stopDictation()
                }
                KeyboardView.Mode.PROCESSING -> pressStarted = false
            }
        }

        override fun onMicUp() {
            // 按住超过 HOLD_MS 再松手 = 按住说话;短按 = 开关式,录音继续,等下一次点。
            if (mode == KeyboardView.Mode.RECORDING && pressStarted &&
                SystemClock.elapsedRealtime() - pressedAt >= HOLD_MS
            ) {
                stopDictation()
            }
            pressStarted = false
        }

        override fun onCancel() {
            cancelDictation()
            view?.setMode(KeyboardView.Mode.IDLE, L.t("已放弃", "Discarded"))
        }

        override fun onReinsert() {
            lastResult?.let { commit(it) }
        }

        override fun onSwitchKeyboard() {
            switchToNextInputMethod(false)
        }

        override fun onPickKeyboard() {
            getSystemService(InputMethodManager::class.java)?.showInputMethodPicker()
        }

        override fun onSpace() {
            currentInputConnection?.commitText(" ", 1)
        }

        override fun onBackspace() {
            // 发 DEL 键而不是 deleteSurroundingText(1, 0):后者会把 emoji 这种代理对删一半。
            sendDownUpKeyEvents(KeyEvent.KEYCODE_DEL)
        }

        override fun onEnter() {
            val info = currentInputEditorInfo ?: return
            val action = info.imeOptions and EditorInfo.IME_MASK_ACTION
            val noAction = info.imeOptions and EditorInfo.IME_FLAG_NO_ENTER_ACTION != 0
            if (!noAction && action != EditorInfo.IME_ACTION_NONE && action != EditorInfo.IME_ACTION_UNSPECIFIED) {
                currentInputConnection?.performEditorAction(action)
            } else {
                sendKeyChar('\n')
            }
        }

        override fun onOpenSettings() {
            startActivity(
                Intent(this@VoiceKeyboardService, SettingsActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        }
    }

    // ── 听写 ──

    /** 开始录音。开不了(没权限、没填服务地址、麦克风被占)时提示原因并返回 false。 */
    private fun startDictation(): Boolean {
        val v = view ?: return false
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            v.setMode(KeyboardView.Mode.IDLE, L.t("没有麦克风权限:点右上角 ⚙ 打开应用授权", "No microphone permission: tap ⚙ to grant it"))
            return false
        }
        val config = settings.serverConfig()
        if (config == null) {
            v.setMode(KeyboardView.Mode.IDLE, L.t("还没填服务地址:点右上角 ⚙ 设置", "No server set up yet: tap ⚙"))
            return false
        }

        val id = ++dictationId
        val s = DictationSession(config, http, object : DictationSession.Listener {
            override fun onStage(stage: Stage) = onUi(id) { showStage(stage) }
            override fun onSegment(index: Int, text: String) = onUi(id) {
                if (text.isNotBlank()) recordingStatus = "…" + text.takeLast(24)
            }
            override fun onResult(text: String, llmError: String?) = onUi(id) { finishWith(text, llmError) }
            override fun onError(error: DictationError) = onUi(id) { failWith(error) }
        })
        val cap = AudioCapture(
            onAudio = { buf, n -> s.appendAudio(buf, n) },
            onLevel = { level -> main.post { if (id == dictationId) view?.setLevel(level) } },
        )
        if (!cap.start()) {
            dictationId++
            v.setMode(KeyboardView.Mode.IDLE, L.t("麦克风打不开(可能被别的应用占着)", "Couldn't open the microphone (another app may be using it)"))
            return false
        }
        session = s
        capture = cap
        s.start()
        mode = KeyboardView.Mode.RECORDING
        recordingSince = SystemClock.elapsedRealtime()
        recordingStatus = L.t("正在听…", "Listening…")
        v.setMode(mode, recordingStatus)
        main.post(ticker)
        return true
    }

    private fun stopDictation() {
        capture?.stop()
        capture = null
        session?.finish()
        mode = KeyboardView.Mode.PROCESSING
        view?.setMode(mode, L.t("识别中…", "Transcribing…"))
    }

    private fun cancelDictation() {
        dictationId++
        capture?.stop()
        capture = null
        session?.cancel()
        session = null
        mode = KeyboardView.Mode.IDLE
    }

    private fun showStage(stage: Stage) {
        val v = view ?: return
        when (stage) {
            Stage.CONNECTING -> recordingStatus = L.t("正在听…(连接中)", "Listening… (connecting)")
            Stage.STREAMING -> recordingStatus = L.t("正在听…", "Listening…")
            Stage.RECORDING_OFFLINE -> recordingStatus = L.t("正在听…(连接断了,说完后整段上传)", "Listening… (offline, will upload when you stop)")
            Stage.UPLOADING -> v.setStatus(L.t("上传中…", "Uploading…"))
            Stage.TRANSCRIBING -> v.setStatus(L.t("识别中…", "Transcribing…"))
            Stage.POLISHING -> v.setStatus(L.t("整理文字…", "Polishing…"))
        }
    }

    private fun finishWith(text: String, llmError: String?) {
        session = null
        mode = KeyboardView.Mode.IDLE
        lastResult = text
        // 键盘已经收起(用户切走了),别往不知道是哪里的输入框里插;留着等「↺」。
        val inserted = isInputViewShown && currentInputConnection != null
        if (inserted) commit(text)
        view?.setReinsertAvailable(true)
        view?.setMode(
            mode,
            when {
                !inserted -> L.t("结果已保存,点 ↺ 插入", "Result saved, tap ↺ to insert")
                llmError != null -> L.t("文字整理没做成,插入的是原文:", "Polishing failed, inserted the raw text: ") + llmError
                else -> idleHint()
            },
        )
    }

    private fun failWith(error: DictationError) {
        capture?.stop()
        capture = null
        session = null
        mode = KeyboardView.Mode.IDLE
        view?.setMode(mode, error.message)
    }

    /**
     * 插入文字。前面紧挨着英文字母 / 数字 / 句末标点、插入的又以英文开头时补一个空格,
     * 免得两句英文粘在一起;中文之间不加。
     */
    private fun commit(text: String) {
        val ic = currentInputConnection ?: return
        val before = ic.getTextBeforeCursor(1, 0)?.toString().orEmpty()
        val prev = before.lastOrNull()
        val first = text.firstOrNull()
        val needsSpace = prev != null && first != null &&
            (prev.isAsciiLetterOrDigit() || prev in ".,!?;:") && first.isAsciiLetterOrDigit()
        ic.commitText(if (needsSpace) " $text" else text, 1)
    }

    private fun Char.isAsciiLetterOrDigit() = this.code < 128 && this.isLetterOrDigit()

    /** 回调来自 DictationSession 的线程:切回主线程,并丢掉已经作废的会话的回调。 */
    private fun onUi(id: Int, block: () -> Unit) {
        main.post { if (id == dictationId) block() }
    }

    private fun idleHint() = L.t("点一下说话,或按住说话", "Tap to talk, or hold to talk")

    private fun enterLabel(info: EditorInfo): String {
        if (info.imeOptions and EditorInfo.IME_FLAG_NO_ENTER_ACTION != 0) return "⏎"
        return when (info.imeOptions and EditorInfo.IME_MASK_ACTION) {
            EditorInfo.IME_ACTION_GO -> L.t("前往", "Go")
            EditorInfo.IME_ACTION_SEARCH -> L.t("搜索", "Search")
            EditorInfo.IME_ACTION_SEND -> L.t("发送", "Send")
            EditorInfo.IME_ACTION_NEXT -> L.t("下一项", "Next")
            EditorInfo.IME_ACTION_DONE -> L.t("完成", "Done")
            else -> "⏎"
        }
    }

    companion object {
        /** 按住超过这么久再松手算「按住说话」。 */
        const val HOLD_MS = 400L
    }
}
