package com.voiceinput.core

/** 一次听写没成的原因。界面按 [kind] 决定怎么提示,[message] 已经按界面语言写好。 */
data class DictationError(val kind: Kind, val message: String) {
    enum class Kind {
        /** 录到的是静音,或者服务端没识别出字。不算故障,界面轻描淡写地提一句即可。 */
        NO_SPEECH,

        /** 连不上服务(地址不对、不在同一网络、服务没开)。 */
        UNREACHABLE,

        /** 服务端设了 `VIF_API_TOKEN`,而令牌没填或填错了。 */
        UNAUTHORIZED,

        /** 服务端很久没有动静。 */
        TIMEOUT,

        /** 服务端明确回了 error(模型没加载好、音频太长……)。 */
        SERVER,
    }
}
