package com.voiceinput.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerConfigTest {
    @Test
    fun bareHostGetsSchemeAndDefaultPort() {
        assertEquals("http://192.168.1.10:6544", ServerConfig.normalizeUrl("192.168.1.10"))
        assertEquals("http://mac.local:6544", ServerConfig.normalizeUrl(" mac.local/ "))
        assertEquals("http://[fd7a::1]:6544", ServerConfig.normalizeUrl("[fd7a::1]"))
    }

    @Test
    fun explicitPortIsKept() {
        assertEquals("http://192.168.1.10:7000", ServerConfig.normalizeUrl("192.168.1.10:7000"))
        assertEquals("http://[fd7a::1]:7000", ServerConfig.normalizeUrl("[fd7a::1]:7000"))
    }

    @Test
    fun explicitSchemeWithoutPortIsNotGivenOne() {
        // tailscale serve / 反向代理给的 https 地址就该走 443。
        assertEquals("https://box.tail1234.ts.net", ServerConfig.normalizeUrl("https://box.tail1234.ts.net/"))
        assertEquals("https://box.example/vif", ServerConfig.normalizeUrl("wss://box.example/vif"))
        assertEquals("http://10.0.0.2:6544", ServerConfig.normalizeUrl("ws://10.0.0.2:6544"))
    }

    @Test
    fun rejectsGarbage() {
        assertNull(ServerConfig.normalizeUrl(""))
        assertNull(ServerConfig.normalizeUrl("ftp://x"))
        assertNull(ServerConfig.normalizeUrl("http://"))
        assertNull(ServerConfig.normalizeUrl("my server"))
    }

    @Test
    fun urlKeepsPathPrefix() {
        val c = ServerConfig("https://box.example/vif")
        assertEquals("https://box.example/vif/ws/stream", c.url("/ws/stream"))
    }
}
