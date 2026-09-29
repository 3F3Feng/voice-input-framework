package com.voiceinput.ime

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.provider.Settings as SystemSettings
import android.text.InputType
import android.util.TypedValue
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowInsets
import android.view.inputmethod.InputMethodManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import com.voiceinput.core.DictationSession
import com.voiceinput.core.ServerCheck
import com.voiceinput.core.ServerConfig

/**
 * 应用本体:三步启用引导(麦克风权限 → 在系统里启用键盘 → 切换到它)、服务地址 / 令牌 /
 * 识别语言,以及一个试用输入框。
 */
class SettingsActivity : Activity() {
    private lateinit var settings: Settings
    private val http by lazy { DictationSession.defaultHttpClient() }

    private lateinit var micStatus: TextView
    private lateinit var enableStatus: TextView
    private lateinit var urlField: EditText
    private lateinit var tokenField: EditText
    private lateinit var checkResult: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        title = L.t("语音输入键盘", "Voice Input Keyboard")

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(16), dp(20), dp(24))
        }

        // ── 启用 ──
        root.addView(heading(L.t("启用", "Setup")))
        micStatus = body("")
        root.addView(step(L.t("1. 麦克风权限", "1. Microphone permission"), micStatus, L.t("授权", "Grant")) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
        })
        enableStatus = body("")
        root.addView(step(L.t("2. 在系统设置里启用本键盘", "2. Enable the keyboard in system settings"), enableStatus, L.t("去启用", "Open")) {
            startActivity(Intent(SystemSettings.ACTION_INPUT_METHOD_SETTINGS))
        })
        root.addView(step(L.t("3. 切换到本键盘", "3. Switch to this keyboard"), body(L.t("输入时也能长按 🌐 切换", "You can also long-press 🌐 while typing")), L.t("切换", "Switch")) {
            getSystemService(InputMethodManager::class.java)?.showInputMethodPicker()
        })

        // ── 服务 ──
        root.addView(heading(L.t("识别服务", "STT service")))
        root.addView(
            body(
                L.t(
                    "填运行 STT 服务(services/stt_server.py,默认端口 6544)那台电脑的地址。服务端要设 VIF_STT_HOST=0.0.0.0 才能从手机连;建议同时设 VIF_API_TOKEN,并在下面填同一个令牌。出门在外也想用,推荐 Tailscale。",
                    "Enter the address of the computer running the STT service (services/stt_server.py, port 6544 by default). The server needs VIF_STT_HOST=0.0.0.0 to accept connections from the phone; also set VIF_API_TOKEN and enter the same token below. For use away from home, Tailscale is recommended.",
                ),
            ),
        )
        urlField = field(L.t("例如 192.168.1.10 或 https://mac.tailnet.ts.net", "e.g. 192.168.1.10 or https://mac.tailnet.ts.net"))
        urlField.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        urlField.setText(settings.serverUrl)
        root.addView(label(L.t("服务地址", "Server address")))
        root.addView(urlField)

        tokenField = field(L.t("服务端没设 VIF_API_TOKEN 就留空", "Leave empty if the server has no VIF_API_TOKEN"))
        tokenField.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        tokenField.setText(settings.token)
        root.addView(label(L.t("访问令牌", "Access token")))
        root.addView(tokenField)

        root.addView(label(L.t("识别语言", "Language")))
        val languages = ServerConfig.LANGUAGES
        val spinner = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@SettingsActivity,
                android.R.layout.simple_spinner_dropdown_item,
                languages.map { L.languageName(it) },
            )
            setSelection(languages.indexOf(settings.language).coerceAtLeast(0))
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                    settings.language = languages[position]
                }

                override fun onNothingSelected(parent: AdapterView<*>?) {}
            }
        }
        root.addView(spinner)

        root.addView(Button(this).apply {
            text = L.t("保存并测试连接", "Save & test connection")
            setOnClickListener { saveAndTest() }
        }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dp(12) })
        checkResult = body("")
        root.addView(checkResult)

        // ── 试一试 ──
        root.addView(heading(L.t("试一试", "Try it")))
        root.addView(
            EditText(this).apply {
                hint = L.t("点这里,切到语音键盘说几句", "Tap here, switch to the voice keyboard and say something")
                minLines = 3
            },
        )

        val scroll = ScrollView(this).apply { addView(root) }
        // targetSdk 35 起界面默认铺到状态栏 / 导航栏底下,自己让出来。
        scroll.setOnApplyWindowInsetsListener { v, insets ->
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime())
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            } else {
                @Suppress("DEPRECATION")
                v.setPadding(
                    insets.systemWindowInsetLeft,
                    insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight,
                    insets.systemWindowInsetBottom,
                )
            }
            insets
        }
        setContentView(scroll)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    private fun refreshStatus() {
        val micOk = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        micStatus.text = if (micOk) L.t("✓ 已授权", "✓ Granted") else L.t("✗ 还没授权", "✗ Not granted")
        val enabled = getSystemService(InputMethodManager::class.java)
            ?.enabledInputMethodList?.any { it.packageName == packageName } == true
        enableStatus.text = if (enabled) L.t("✓ 已启用", "✓ Enabled") else L.t("✗ 还没启用", "✗ Not enabled")
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        refreshStatus()
    }

    private fun saveAndTest() {
        val url = ServerConfig.normalizeUrl(urlField.text.toString())
        if (url == null) {
            checkResult.text = L.t("地址不对,例如 192.168.1.10", "That's not a valid address, e.g. 192.168.1.10")
            return
        }
        urlField.setText(url)
        settings.serverUrl = url
        settings.token = tokenField.text.toString().trim()
        val config = settings.serverConfig() ?: return
        checkResult.text = L.t("测试中…", "Testing…")
        Thread {
            val result = ServerCheck.run(config, http)
            runOnUiThread { checkResult.text = (if (result.ok) "✓ " else "✗ ") + result.message }
        }.start()
    }

    // ── 小部件 ──

    private fun heading(text: String) = TextView(this).apply {
        this.text = text
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 20f)
        setTypeface(typeface, Typeface.BOLD)
        setPadding(0, dp(20), 0, dp(8))
    }

    private fun label(text: String) = TextView(this).apply {
        this.text = text
        setTypeface(typeface, Typeface.BOLD)
        setPadding(0, dp(12), 0, 0)
    }

    private fun body(text: String) = TextView(this).apply {
        this.text = text
        setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
    }

    private fun field(hint: String) = EditText(this).apply {
        this.hint = hint
        isSingleLine = true
    }

    private fun step(title: String, status: TextView, action: String, onClick: () -> Unit): View {
        val texts = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(this@SettingsActivity).apply {
                text = title
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            })
            addView(status)
        }
        return LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(6), 0, dp(6))
            addView(texts, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            addView(Button(this@SettingsActivity).apply {
                text = action
                setOnClickListener { onClick() }
            })
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val REQ_MIC = 1
    }
}
