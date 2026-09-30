package com.voiceinput.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairingLinkTest {
    @Test
    fun parsesTheLinkTheDesktopClientMakes() {
        // 桌面客户端(mobile_pairing.rs)生成的形式:地址整个做了百分号编码。
        val r = PairingLink.parse("voiceinput://setup?url=https%3A%2F%2Fmac.tailnet.ts.net%3A8443")
        assertEquals(PairingRequest("https://mac.tailnet.ts.net:8443", null), r)
    }

    @Test
    fun parsesTheUnencodedFormToo() {
        val r = PairingLink.parse("voiceinput://setup?url=https://mac.tailnet.ts.net:8443")
        assertEquals("https://mac.tailnet.ts.net:8443", r?.url)
    }

    @Test
    fun keepsAnAddressWithoutAPort() {
        // tailscale serve 走 443 时地址不带端口,不能被补上 6544。
        val r = PairingLink.parse("voiceinput://setup?url=https%3A%2F%2Fbox.tail1234.ts.net")
        assertEquals("https://box.tail1234.ts.net", r?.url)
    }

    @Test
    fun readsAnOptionalToken() {
        val r = PairingLink.parse("voiceinput://setup?url=https%3A%2F%2Fbox.example&token=s3cret")
        assertEquals("s3cret", r?.token)
    }

    @Test
    fun aPlusInTheTokenStaysAPlus() {
        // 令牌里的 + 是字面的加号,不能被当成空格。
        assertEquals("a+b", PairingLink.parse("voiceinput://setup?url=https%3A%2F%2Fbox.example&token=a%2Bb")?.token)
        assertEquals("a+b", PairingLink.parse("voiceinput://setup?url=https%3A%2F%2Fbox.example&token=a+b")?.token)
    }

    @Test
    fun blankTokenIsNoToken() {
        assertNull(PairingLink.parse("voiceinput://setup?url=https%3A%2F%2Fbox.example&token=")?.token)
        assertNull(PairingLink.parse("voiceinput://setup?url=https%3A%2F%2Fbox.example&token=%20")?.token)
    }

    @Test
    fun plainHttpIsRejected() {
        // 明文地址不该经一个链接就写进设置。
        assertNull(PairingLink.parse("voiceinput://setup?url=http%3A%2F%2Fevil.example%3A6544"))
        assertNull(PairingLink.parse("voiceinput://setup?url=192.168.1.10")) // 没写 scheme 会被补成 http
        assertNull(PairingLink.parse("voiceinput://setup?url=ws%3A%2F%2Fevil.example"))
    }

    @Test
    fun rejectsLinksThatAreNotPairing() {
        assertNull(PairingLink.parse(""))
        assertNull(PairingLink.parse("https://setup?url=https%3A%2F%2Fbox.example")) // scheme 不对
        assertNull(PairingLink.parse("voiceinput://dictate?url=https%3A%2F%2Fbox.example")) // 不是 setup
        assertNull(PairingLink.parse("voiceinput://setup")) // 没有 url
        assertNull(PairingLink.parse("voiceinput://setup?url=")) // url 是空的
        assertNull(PairingLink.parse("voiceinput://setup?url=ftp%3A%2F%2Fbox.example"))
        assertNull(PairingLink.parse("voiceinput://setup?token=x"))
    }

    @Test
    fun schemeAndHostAreCaseInsensitive() {
        val r = PairingLink.parse("VoiceInput://Setup?url=https%3A%2F%2Fbox.example")
        assertEquals("https://box.example", r?.url)
    }

    @Test
    fun brokenPercentEscapesDoNotCrash() {
        // 残缺的 %xx 不能让应用崩;要么解不出地址返回 null,要么原样处理。
        assertNull(PairingLink.parse("voiceinput://setup?url=https%3A%2F%2F%ZZ"))
    }
}
