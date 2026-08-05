//! 轻量测试入口:通过 #[path] 直接复用 src-tauri/src/stt.rs 源码(单一来源),
//! 在不链接 tauri 的前提下于任意平台验证 stt 客户端纯逻辑。

#[path = "../../src-tauri/src/stt.rs"]
mod stt;

use stt::{SttClient, StreamEvent};

// ── SttClient::new URL 构造(纯逻辑)──

#[test]
fn new_with_full_url_keeps_scheme() {
    let c = SttClient::new("http://192.168.1.5:6544");
    assert_eq!(c.stt_url, "http://192.168.1.5:6544");
}

#[test]
fn new_with_https_keeps_scheme() {
    let c = SttClient::new("https://example.com:6544");
    assert_eq!(c.stt_url, "https://example.com:6544");
}

#[test]
fn new_with_host_only_appends_default_port() {
    // 兜底默认端口 6544(与 config 默认一致,非硬编码错误)
    let c = SttClient::new("myhost");
    assert_eq!(c.stt_url, "http://myhost:6544");
}

#[test]
fn new_with_host_and_port_is_naive() {
    // 已知局限:host:port 不带 scheme 时会把整个字符串当 host,追加默认端口
    // (调用方 lib.rs 总是传完整 URL,故实际不触发;此测试锁定当前行为)
    let c = SttClient::new("10.0.0.1:7000");
    assert_eq!(c.stt_url, "http://10.0.0.1:7000:6544");
}

// ── StreamEvent 序列化(WS 协议契约,与 Python 端一致)──

#[test]
fn stream_event_stt_result_json() {
    let ev = StreamEvent::SttResult { text: "你好".into() };
    let json = serde_json::to_string(&ev).unwrap();
    assert_eq!(json, r#"{"type":"stt_result","text":"你好"}"#);
}

#[test]
fn stream_event_final_result_json() {
    let ev = StreamEvent::FinalResult { text: "hi".into(), llm_latency_ms: Some(12.5) };
    let json = serde_json::to_string(&ev).unwrap();
    assert_eq!(json, r#"{"type":"result","text":"hi","llm_latency_ms":12.5}"#);
}

#[test]
fn stream_event_error_json() {
    let ev = StreamEvent::Error { message: "boom".into() };
    let json = serde_json::to_string(&ev).unwrap();
    assert_eq!(json, r#"{"type":"error","message":"boom"}"#);
}

#[test]
fn stream_event_llm_start_json() {
    let ev = StreamEvent::LlmStart { text: "processing".into() };
    let json = serde_json::to_string(&ev).unwrap();
    assert_eq!(json, r#"{"type":"llm_start","text":"processing"}"#);
}

// ── WS URL 派生(transcribe 路径使用)──

#[test]
fn ws_url_derives_from_http() {
    let c = SttClient::new("http://localhost:6544");
    let ws_url = c.stt_url.replace("http://", "ws://");
    assert_eq!(ws_url, "ws://localhost:6544");
    let url = format!("{}/ws/stream", ws_url);
    assert_eq!(url, "ws://localhost:6544/ws/stream");
}
