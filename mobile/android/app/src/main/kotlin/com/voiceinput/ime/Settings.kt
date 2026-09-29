package com.voiceinput.ime

import android.content.Context
import com.voiceinput.core.ServerConfig
import java.util.Locale

/** 界面跟系统语言走:中文系统显示中文,其余一律英文(和桌面端一样只有这两种)。 */
object L {
    val english: Boolean get() = Locale.getDefault().language != "zh"

    fun t(zh: String, en: String): String = if (english) en else zh

    fun languageName(code: String): String = when (code) {
        "auto" -> t("自动检测", "Auto-detect")
        "zh" -> t("中文", "Chinese")
        "en" -> "English"
        "yue" -> t("粤语", "Cantonese")
        "ja" -> "日本語"
        "ko" -> "한국어"
        else -> code
    }
}

/**
 * 应用和输入法共用的设置。输入法服务和设置页在同一个进程里,SharedPreferences 直接共用。
 *
 * 令牌以明文存在应用私有目录里:它只能用来调用户自己的 STT 服务,而且手机上的其它
 * 应用读不到这个目录。
 */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(v) = prefs.edit().putString("server_url", v).apply()

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(v) = prefs.edit().putString("token", v).apply()

    var language: String
        get() = prefs.getString("language", "auto") ?: "auto"
        set(v) = prefs.edit().putString("language", v).apply()

    /** 还没填服务地址时返回 null。 */
    fun serverConfig(): ServerConfig? {
        val url = serverUrl.takeIf { it.isNotBlank() } ?: return null
        return ServerConfig(url, token.trim().ifEmpty { null }, language, L.english)
    }
}
