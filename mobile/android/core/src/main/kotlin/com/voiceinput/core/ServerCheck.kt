package com.voiceinput.core

import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.IOException

/**
 * 设置页的「测试连接」:服务在不在、令牌对不对、模型加载好没有。
 *
 * `/health` 不要令牌(shared/auth.py 的 PUBLIC_PATHS),只能说明服务在;令牌对不对
 * 要拿一个需要令牌的端点(`/models`)试一下。
 */
object ServerCheck {
    data class Result(
        val ok: Boolean,
        /** 给用户看的一句话,已按界面语言写好。 */
        val message: String,
        val appVersion: String? = null,
        val model: String? = null,
    )

    /** 同步调用,会发网络请求 —— 别在主线程上调。 */
    fun run(config: ServerConfig, http: OkHttpClient): Result {
        val health = try {
            get(config, http, "/health", withToken = false)
        } catch (e: IOException) {
            return Result(
                false,
                config.t("连不上服务", "Can't reach the service") + ": ${e.message ?: e.javaClass.simpleName}",
            )
        }
        if (health.first != 200) {
            return Result(false, config.t("服务返回了 HTTP ", "The service returned HTTP ") + health.first)
        }
        val body = try {
            JSONObject(health.second)
        } catch (_: Exception) {
            return Result(false, config.t("这个地址不像是 STT 服务", "This doesn't look like the STT service"))
        }
        val version = body.str("app_version")
        val model = body.str("current_model")

        val models = try {
            get(config, http, "/models", withToken = true)
        } catch (e: IOException) {
            return Result(false, config.t("连接中断", "Connection lost") + ": ${e.message}")
        }
        if (models.first == 401) {
            return Result(
                false,
                if (config.token.isNullOrEmpty()) {
                    config.t("服务端要求访问令牌,请填上 VIF_API_TOKEN", "The server requires an access token (VIF_API_TOKEN)")
                } else {
                    config.t("访问令牌不对", "The access token is wrong")
                },
                version,
                model,
            )
        }

        val status = body.optString("status")
        val detail = buildString {
            append(config.t("已连接", "Connected"))
            if (!model.isNullOrEmpty()) append(" · ").append(model)
            if (!version.isNullOrEmpty()) append(" · v").append(version)
        }
        return when (status) {
            "ok" -> Result(true, detail, version, model)
            "loading" -> Result(true, detail + config.t("(模型加载中)", " (model loading)"), version, model)
            else -> Result(
                false,
                detail + " · " + (body.str("error") ?: config.t("模型没有加载", "Model not loaded")),
                version,
                model,
            )
        }
    }

    private fun get(config: ServerConfig, http: OkHttpClient, path: String, withToken: Boolean): Pair<Int, String> {
        val request = Request.Builder()
            .url(config.url(path))
            .header("Accept-Language", config.acceptLanguage)
            .apply { if (withToken) config.token?.let { header("Authorization", "Bearer $it") } }
            .build()
        http.newCall(request).execute().use { resp ->
            return resp.code to (resp.body.string())
        }
    }
}
