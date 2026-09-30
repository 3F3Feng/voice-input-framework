package com.voiceinput.core

import java.net.URLDecoder

/**
 * 配对链接:`voiceinput://setup?url=<https 地址>&token=<可选>`。
 *
 * 桌面客户端(设置 → 服务 → 配对手机)和 `mobile/tools/pair.swift` 会把它画成二维码,
 * 手机相机扫一下就能打开本应用,不用手打服务地址。规则和 iOS 版完全一致:
 *
 * - **只认 HTTPS**:明文地址不该经一个链接就写进设置;
 * - 链接谁都能构造(网页、别的应用都能触发),所以解析出来只是一个「请求」,
 *   调用方必须先让用户确认地址,不能直接生效。
 */
data class PairingRequest(
    /** 已经过 [ServerConfig.normalizeUrl],一定以 `https://` 开头。 */
    val url: String,
    /** 链接里带的访问令牌;没带就是 null。 */
    val token: String?,
)

object PairingLink {
    private const val SCHEME = "voiceinput"
    private const val HOST = "setup"

    /** 不是配对链接、或者地址不合规矩(不是 https、不像地址)时返回 null。 */
    fun parse(link: String): PairingRequest? {
        val trimmed = link.trim()
        val schemeEnd = trimmed.indexOf("://")
        if (schemeEnd < 0 || !trimmed.substring(0, schemeEnd).equals(SCHEME, ignoreCase = true)) return null

        val afterScheme = trimmed.substring(schemeEnd + 3)
        val host = afterScheme.substringBefore('?').trimEnd('/')
        if (!host.equals(HOST, ignoreCase = true)) return null

        val query = afterScheme.substringAfter('?', "").substringBefore('#')
        val params = query.split('&')
            .filter { it.isNotEmpty() }
            .associate {
                val key = it.substringBefore('=')
                val value = it.substringAfter('=', "")
                key to decode(value)
            }

        val url = ServerConfig.normalizeUrl(params["url"].orEmpty()) ?: return null
        if (!url.startsWith("https://")) return null
        val token = params["token"]?.trim()?.takeIf { it.isNotEmpty() }
        return PairingRequest(url, token)
    }

    /**
     * 百分号解码。`URLDecoder` 会把 `+` 当成空格(表单编码的规矩),但这里的令牌里
     * 可能真有 `+`(桌面端和 iOS 端都把它当字面的加号),所以先转义再解码。
     */
    private fun decode(value: String): String =
        try {
            URLDecoder.decode(value.replace("+", "%2B"), "UTF-8")
        } catch (e: IllegalArgumentException) {
            value // 残缺的 %xx:原样留着,后面 normalizeUrl 会判断像不像地址
        }
}
