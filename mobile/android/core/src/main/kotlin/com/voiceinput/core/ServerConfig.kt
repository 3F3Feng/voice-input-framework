package com.voiceinput.core

/**
 * 连哪台 STT 服务、用什么令牌、识别什么语言。
 *
 * 手机只连 STT 服务(默认 6544):LLM 后处理由 STT 服务在 `/ws/stream` 里转发给
 * LLM 服务,所以 LLM 服务可以一直只绑回环地址。
 */
data class ServerConfig(
    /** 已经过 [normalizeUrl] 的地址,如 `http://192.168.1.10:6544`。 */
    val baseUrl: String,
    /** 服务端设了 `VIF_API_TOKEN` 时要带的令牌;没设就是 null。 */
    val token: String? = null,
    /** 识别语言:`auto` / `zh` / `en` / `yue` / `ja` / `ko`,和桌面端一样(services/stt_engine.py)。 */
    val language: String = "auto",
    /** 界面是不是英文。决定错误提示的语言,也作为 Accept-Language 发给服务端(shared/i18n.py)。 */
    val english: Boolean = false,
) {
    val acceptLanguage: String get() = if (english) "en" else "zh-CN"

    /** 服务上的某个路径。地址里可以带路径前缀(反向代理后面),这里原样保留。 */
    fun url(path: String): String = baseUrl.trimEnd('/') + path

    fun t(zh: String, en: String): String = if (english) en else zh

    companion object {
        const val DEFAULT_PORT = 6544

        /** 界面上给用户选的识别语言,和桌面端设置面板一致。 */
        val LANGUAGES = listOf("auto", "zh", "en", "yue", "ja", "ko")

        /**
         * 把用户填的地址整理成能用的 URL。
         *
         * - `192.168.1.10` → `http://192.168.1.10:6544`(只填主机时补默认端口);
         * - `ws://` / `wss://` 当成 `http://` / `https://`;
         * - 写了 scheme 却没写端口时**不**补 6544:那通常是 `tailscale serve` 或
         *   反向代理给的 https 地址,就该走 443。
         *
         * 填的不像地址时返回 null。
         */
        fun normalizeUrl(input: String): String? {
            val raw = input.trim()
            // 先认 scheme 再去掉结尾的 `/`:否则 `http://` 会变成主机名叫 `http:` 的地址。
            val schemeEnd = raw.indexOf("://")
            var s = if (schemeEnd < 0) raw.trimEnd('/') else raw.substring(0, schemeEnd + 3) + raw.substring(schemeEnd + 3).trimEnd('/')
            if (s.isEmpty()) return null
            if (schemeEnd < 0) {
                val authority = s.substringBefore('/')
                val hasPort = if (authority.startsWith("[")) {
                    authority.substringAfter(']', "").startsWith(":")
                } else {
                    authority.contains(':')
                }
                val rest = s.substring(authority.length)
                s = "http://" + authority + (if (hasPort) "" else ":$DEFAULT_PORT") + rest
            } else {
                val scheme = s.substring(0, schemeEnd).lowercase()
                val rest = s.substring(schemeEnd + 3)
                s = when (scheme) {
                    "http", "ws" -> "http://$rest"
                    "https", "wss" -> "https://$rest"
                    else -> return null
                }
            }
            val host = s.substringAfter("://").substringBefore('/').substringBefore('?')
            if (host.isEmpty() || host.startsWith(":") || host.any { it.isWhitespace() }) return null
            return s
        }
    }
}
