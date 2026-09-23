//! 轻量测试入口:通过 #[path] 直接复用 src-tauri/src/stt.rs 源码(单一来源),
//! 在不链接 tauri 的前提下于任意平台验证 stt 客户端纯逻辑。

#[path = "../../src-tauri/src/stt.rs"]
mod stt;

// stt.rs 里的文案用 `crate::i18n::t` / `crate::tr!` 挑语言。
#[path = "../../src-tauri/src/i18n.rs"]
#[macro_use]
#[allow(dead_code)]
mod i18n;

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
    let ev = StreamEvent::FinalResult { text: "hi".into(), llm_latency_ms: Some(12.5), llm_error: None };
    let json = serde_json::to_string(&ev).unwrap();
    assert_eq!(json, r#"{"type":"result","text":"hi","llm_latency_ms":12.5}"#);
}

#[test]
fn stream_event_final_result_carries_llm_error() {
    let ev = StreamEvent::FinalResult {
        text: "原文".into(),
        llm_latency_ms: Some(0.0),
        llm_error: Some("连不上 LLM 服务".into()),
    };
    let json = serde_json::to_string(&ev).unwrap();
    assert!(json.contains(r#""llm_error":"连不上 LLM 服务""#), "{}", json);
}

// ── result 消息里的 llm_error(R8)──

#[test]
fn llm_error_is_read_from_the_result_message() {
    let v = serde_json::json!({"type": "result", "text": "原文", "llm_error": "LLM 服务 30 秒没有应答"});
    assert_eq!(stt::llm_error_of(&v).as_deref(), Some("LLM 服务 30 秒没有应答"));
}

#[test]
fn llm_error_is_none_for_old_servers_and_success() {
    // 老服务端没有这个字段;成功时它是 null;空串也不算原因。
    for v in [
        serde_json::json!({"type": "result", "text": "x"}),
        serde_json::json!({"type": "result", "text": "x", "llm_error": null}),
        serde_json::json!({"type": "result", "text": "x", "llm_error": "  "}),
    ] {
        assert_eq!(stt::llm_error_of(&v), None, "{}", v);
    }
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
    assert_eq!(stt::ws_base(&c.stt_url), "ws://localhost:6544");
}

#[test]
fn ws_url_from_https_is_wss() {
    // 以前只替换 "http://",https 地址原样交给 tungstenite:能列模型、转写必失败。
    let c = SttClient::new("https://stt.example.com");
    assert_eq!(stt::ws_base(&c.stt_url), "wss://stt.example.com");
}

// ── 错误消息字段解析(服务端发 error_message,旧代码读 message)──

#[test]
fn error_frame_uses_error_message() {
    // services/stt_server.py 的 WS error 帧:{"type","error_code","error_message"}
    let data: serde_json::Value = serde_json::from_str(
        r#"{"type":"error","error_code":"E5001","error_message":"转写超时"}"#,
    )
    .unwrap();
    assert_eq!(stt::server_message(&data), "转写超时");
}

#[test]
fn success_response_falls_back_to_message() {
    // /models/select 成功响应只有 message 字段
    let data: serde_json::Value =
        serde_json::from_str(r#"{"status":"success","message":"Switching to whisper_turbo"}"#)
            .unwrap();
    assert_eq!(stt::server_message(&data), "Switching to whisper_turbo");
}

#[test]
fn missing_both_fields_is_empty() {
    let data: serde_json::Value = serde_json::from_str(r#"{"status":"success"}"#).unwrap();
    assert_eq!(stt::server_message(&data), "");
}

// ── 模型切换的成败判定(回归:失败曾被当成成功)──

#[test]
fn switch_success_is_not_a_failure() {
    let data: serde_json::Value =
        serde_json::from_str(r#"{"status":"success","current_model":"Qwen3.5-4B-OptiQ"}"#).unwrap();
    assert!(!stt::switch_failed(true, &data));
}

#[test]
fn http_error_is_a_failure() {
    // 服务端现在用 503 回答「模型加载失败」
    let data: serde_json::Value =
        serde_json::from_str(r#"{"status":"failed","message":"模型 X 加载失败"}"#).unwrap();
    assert!(stt::switch_failed(false, &data));
}

#[test]
fn status_failed_in_a_200_is_still_a_failure() {
    // 老服务端用 200 + status:"failed" 报失败;只看 HTTP 状态码会把它当成功,
    // 界面于是弹出一句「LLM 已切换」,而模型根本没换。
    let data: serde_json::Value =
        serde_json::from_str(r#"{"status":"failed","current_model":"Qwen3.5-4B-OptiQ"}"#).unwrap();
    assert!(stt::switch_failed(true, &data));
}

#[test]
fn proxy_error_body_is_a_failure_with_a_readable_message() {
    // 转发层的 502:错误原因在 error_message 里
    let data: serde_json::Value = serde_json::from_str(
        r#"{"error_code":"LLM_PROXY_ERROR","error_message":"LLM 模型切换失败:模型 X 加载失败"}"#,
    )
    .unwrap();
    assert!(stt::switch_failed(false, &data));
    assert_eq!(
        stt::server_message(&data),
        "LLM 模型切换失败:模型 X 加载失败"
    );
}

#[test]
fn fastapi_detail_is_a_readable_message() {
    // FastAPI 的 HTTPException 回 {"detail": ...}。/models/select 的 400/500 就是这种,
    // 以前读不出来,又不看状态码,切换失败也提示「模型已切换」。
    let data: serde_json::Value =
        serde_json::from_str(r#"{"detail":"Unknown model: nope"}"#).unwrap();
    assert!(stt::switch_failed(false, &data));
    assert_eq!(stt::server_message(&data), "Unknown model: nope");
}

// ── 空结果 = 没听到声音(F8)──

#[test]
fn empty_or_blank_text_is_no_speech() {
    // 录了一段静音:服务端回 `result` 带空串。以前这被当成成功,
    // 胶囊亮绿灯,然后什么都没发生。
    for t in ["", " ", "\n\t  "] {
        assert_eq!(
            stt::require_speech(t.to_string()),
            Err(stt::NO_SPEECH.to_string())
        );
    }
    assert_eq!(
        stt::require_speech(" 你好 ".to_string()),
        Ok(" 你好 ".to_string())
    );
}

// ── 分块合并(R1:长录音松手后要一次性发出几万块)──

#[test]
fn coalesce_merges_queued_chunks_in_order_up_to_the_cap() {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Vec<u8>>();
    for i in 0..10u8 {
        tx.send(vec![i; 4]).unwrap();
    }
    drop(tx);
    let first = rx.try_recv().unwrap();
    // 上限 12 字节:第一块 4 + 再拼两块 = 12,到上限就停
    let frame = stt::coalesce_chunks(first, &mut rx, 12);
    assert_eq!(frame, [vec![0u8; 4], vec![1; 4], vec![2; 4]].concat());
    // 剩下的不丢,按顺序拼完
    let mut rest = Vec::new();
    while let Ok(c) = rx.try_recv() {
        rest.push(stt::coalesce_chunks(c, &mut rx, 1 << 20));
    }
    let expected: Vec<u8> = (3..10u8).flat_map(|i| vec![i; 4]).collect();
    assert_eq!(rest, vec![expected]);
}

#[test]
fn coalesce_with_empty_queue_returns_first_chunk() {
    let (_tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Vec<u8>>();
    assert_eq!(stt::coalesce_chunks(vec![1, 2], &mut rx, 64), vec![1, 2]);
}

#[test]
fn model_info_from_old_server_defaults_to_available() {
    // 老服务端的 /models 只有 name / is_loaded:必须照样解出来,且默认可选。
    let m: stt::ModelInfo =
        serde_json::from_str(r#"{"name":"whisper_base","is_loaded":false}"#).unwrap();
    assert!(m.available);
    assert!(!m.recommended);
    assert!(m.description.is_empty());
}

#[test]
fn model_info_carries_catalog_fields() {
    let m: stt::ModelInfo = serde_json::from_str(
        r#"{"name":"whisper_mlx","is_loaded":false,"description":"MLX Whisper Large V3",
            "memory_gb":3.0,"available":false,"unavailable_reason":"需要 Apple Silicon 的 Mac",
            "downloaded":false,"recommended":false}"#,
    )
    .unwrap();
    assert!(!m.available);
    assert_eq!(m.unavailable_reason.as_deref(), Some("需要 Apple Silicon 的 Mac"));
    assert_eq!(m.memory_gb, Some(3.0));
}

// ── LLM 后处理开关状态(F17)──

#[test]
fn llm_status_reads_supported_and_reason() {
    let v = serde_json::json!({"enabled": false, "supported": false, "reason": "请运行 scripts/setup-env.sh --llm 安装后重启服务"});
    let st = stt::LlmStatus::from_json(&v).unwrap();
    assert!(!st.enabled && !st.supported);
    assert_eq!(st.reason.as_deref(), Some("请运行 scripts/setup-env.sh --llm 安装后重启服务"));
}

#[test]
fn llm_status_from_old_server_counts_as_supported() {
    // 老服务端只回 {"enabled": bool}:照旧当作支持,行为和以前一样。
    let st = stt::LlmStatus::from_json(&serde_json::json!({"enabled": true})).unwrap();
    assert!(st.enabled && st.supported);
    assert_eq!(st.reason, None);
    assert!(stt::LlmStatus::from_json(&serde_json::json!({})).is_err());
}

#[test]
fn result_wait_shrinks_once_the_server_sends_keepalives() {
    // 老服务端不发心跳:照旧等 5 分钟;收到过心跳:60 秒没动静就算卡住。
    assert_eq!(stt::result_wait(false).as_secs(), 300);
    assert_eq!(stt::result_wait(true).as_secs(), 60);
}
