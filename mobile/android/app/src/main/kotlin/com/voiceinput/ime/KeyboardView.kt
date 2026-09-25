package com.voiceinput.ime

import android.annotation.SuppressLint
import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import android.view.View
import android.view.WindowInsets
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView

/**
 * 输入法的界面:一个大麦克风键,外加切换输入法、放弃 / 重新插入、空格、退格、回车。
 * 全部用代码搭,不用布局文件。只管显示和把按键交给 [Actions];状态由服务决定。
 */
@SuppressLint("ViewConstructor")
class KeyboardView(context: Context, private val actions: Actions) : LinearLayout(context) {
    interface Actions {
        fun onMicDown()
        fun onMicUp()
        fun onCancel()
        fun onReinsert()
        fun onSwitchKeyboard()
        fun onPickKeyboard()
        fun onSpace()
        fun onBackspace()
        fun onEnter()
        fun onOpenSettings()
    }

    enum class Mode { IDLE, RECORDING, PROCESSING }

    private val dark = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
        Configuration.UI_MODE_NIGHT_YES
    private val bg = if (dark) Color.rgb(0x1F, 0x20, 0x24) else Color.rgb(0xE8, 0xEA, 0xED)
    private val keyBg = if (dark) Color.rgb(0x3C, 0x3D, 0x42) else Color.WHITE
    private val fg = if (dark) Color.rgb(0xE8, 0xEA, 0xED) else Color.rgb(0x20, 0x21, 0x24)
    private val muted = if (dark) Color.rgb(0x9A, 0xA0, 0xA6) else Color.rgb(0x5F, 0x63, 0x68)
    private val accent = Color.rgb(0x1A, 0x73, 0xE8)
    private val recording = Color.rgb(0xD9, 0x30, 0x25)

    private val status = TextView(context)
    private val mic = MicButton(context)
    private val switchKey: TextView
    private val cancelKey: TextView
    private val enterKey: TextView
    private val handler = Handler(Looper.getMainLooper())

    private var mode = Mode.IDLE
    private var reinsertAvailable = false

    init {
        orientation = VERTICAL
        setBackgroundColor(bg)
        setPadding(dp(8), dp(6), dp(8), dp(8))

        // 顶部:状态 + 设置
        val top = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        status.apply {
            setTextColor(muted)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            maxLines = 2
            ellipsize = TextUtils.TruncateAt.END
        }
        top.addView(status, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        top.addView(
            key("⚙") { actions.onOpenSettings() }.apply { background = null },
            LayoutParams(dp(40), dp(36)),
        )
        addView(top, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))

        // 中间:麦克风
        val micBox = FrameLayout(context)
        micBox.addView(mic, FrameLayout.LayoutParams(dp(132), dp(132), Gravity.CENTER))
        addView(micBox, LayoutParams(LayoutParams.MATCH_PARENT, dp(140)))
        mic.setOnTouchListener { v, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
                    actions.onMicDown()
                }
                MotionEvent.ACTION_UP -> {
                    v.performClick()
                    actions.onMicUp()
                }
                MotionEvent.ACTION_CANCEL -> actions.onMicUp()
            }
            true
        }
        mic.contentDescription = L.t("按住说话,或点一下开始、再点一下结束", "Hold to talk, or tap to start and tap again to stop")

        // 底部一排键
        val row = LinearLayout(context).apply { orientation = HORIZONTAL }
        switchKey = key("🌐") { actions.onSwitchKeyboard() }
        switchKey.setOnLongClickListener { actions.onPickKeyboard(); true }
        switchKey.contentDescription = L.t("切换输入法", "Switch keyboard")
        cancelKey = key("✕") {
            if (mode == Mode.IDLE) actions.onReinsert() else actions.onCancel()
        }
        val space = key(L.t("空格", "space")) { actions.onSpace() }
        val backspace = key("⌫") {}
        backspace.contentDescription = L.t("删除", "Delete")
        repeatWhileHeld(backspace) { actions.onBackspace() }
        enterKey = key("⏎") { actions.onEnter() }
        for ((v, weight) in listOf(switchKey to 1f, cancelKey to 1f, space to 3f, backspace to 1f, enterKey to 1.3f)) {
            row.addView(v, LayoutParams(0, dp(46), weight).apply { setMargins(dp(3), 0, dp(3), 0) })
        }
        addView(row, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))

        // 手势导航的横条 / 三键导航栏占掉的高度,别让按键被压在下面。
        setOnApplyWindowInsetsListener { v, insets ->
            val bottom = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                insets.getInsets(WindowInsets.Type.navigationBars()).bottom
            } else {
                @Suppress("DEPRECATION")
                insets.systemWindowInsetBottom
            }
            v.setPadding(dp(8), dp(6), dp(8), dp(8) + bottom)
            insets
        }
        setMode(Mode.IDLE, "")
    }

    fun setMode(mode: Mode, message: String) {
        this.mode = mode
        status.text = message
        mic.mode = mode
        if (mode != Mode.RECORDING) mic.level = 0f
        cancelKey.text = if (mode == Mode.IDLE) "↺" else "✕"
        cancelKey.contentDescription = if (mode == Mode.IDLE) {
            L.t("再插入一次上一条结果", "Insert the last result again")
        } else {
            L.t("放弃这段录音", "Discard this recording")
        }
        val enabled = mode != Mode.IDLE || reinsertAvailable
        cancelKey.isEnabled = enabled
        cancelKey.alpha = if (enabled) 1f else 0.35f
        mic.invalidate()
    }

    fun setStatus(message: String) {
        status.text = message
    }

    fun setLevel(level: Float) {
        // 音量条做个衰减,别一跳一跳的。
        mic.level = maxOf(level, mic.level * 0.8f)
        mic.invalidate()
    }

    fun setReinsertAvailable(available: Boolean) {
        reinsertAvailable = available
        setMode(mode, status.text.toString())
    }

    fun setEnterLabel(label: String) {
        enterKey.text = label
    }

    fun setSwitchKeyVisible(visible: Boolean) {
        switchKey.visibility = if (visible) VISIBLE else GONE
    }

    private fun key(label: String, onClick: () -> Unit): TextView =
        TextView(context).apply {
            text = label
            gravity = Gravity.CENTER
            setTextColor(fg)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f)
            background = GradientDrawable().apply {
                cornerRadius = dp(8).toFloat()
                setColor(keyBg)
            }
            isClickable = true
            setOnClickListener {
                it.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
                onClick()
            }
        }

    /** 按住退格连删:先删一个,停 400 ms,之后每 60 ms 一个。 */
    @SuppressLint("ClickableViewAccessibility")
    private fun repeatWhileHeld(view: View, action: () -> Unit) {
        val repeat = object : Runnable {
            override fun run() {
                action()
                handler.postDelayed(this, 60)
            }
        }
        view.setOnTouchListener { v, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    v.isPressed = true
                    v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
                    action()
                    handler.postDelayed(repeat, 400)
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    v.isPressed = false
                    handler.removeCallbacks(repeat)
                }
            }
            true
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    /** 圆形麦克风键;录音时外面一圈随音量变大。 */
    private inner class MicButton(context: Context) : View(context) {
        var mode = Mode.IDLE
        var level = 0f
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
        private val glyph = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            textAlign = Paint.Align.CENTER
            color = Color.WHITE
        }

        override fun onDraw(canvas: Canvas) {
            val cx = width / 2f
            val cy = height / 2f
            val r = minOf(width, height) / 2f * 0.62f
            val color = when (mode) {
                Mode.IDLE -> accent
                Mode.RECORDING -> recording
                Mode.PROCESSING -> muted
            }
            if (mode == Mode.RECORDING) {
                paint.color = color
                paint.alpha = 60
                canvas.drawCircle(cx, cy, r * (1f + 0.55f * level), paint)
            }
            paint.color = color
            paint.alpha = 255
            canvas.drawCircle(cx, cy, r, paint)
            glyph.textSize = r * 0.8f
            val text = when (mode) {
                Mode.IDLE -> "🎤"
                Mode.RECORDING -> "■"
                Mode.PROCESSING -> "…"
            }
            canvas.drawText(text, cx, cy - (glyph.descent() + glyph.ascent()) / 2, glyph)
        }
    }
}
