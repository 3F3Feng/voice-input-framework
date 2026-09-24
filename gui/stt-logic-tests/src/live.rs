//! 边录边传(`stt::LiveSession`)对着一个假的 WS 服务端跑:新服务端、老服务端、
//! 录音中断线、放弃、服务端 ping。服务端的行为按连接序号脚本化。

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

use crate::stt;

/// 假服务端一条连接的脚本。
#[derive(Clone, Copy)]
struct Script {
    /// `ready` 里带不带 `incremental: true`(老服务端不带)。
    incremental: bool,
    /// 收到这么多字节音频之后直接断开(模拟录音中服务重启 / 网络断)。
    drop_after_bytes: Option<usize>,
    /// 连上后先发一个 ping,看客户端录音期间回不回 pong。
    ping: bool,
    /// 收到 end 之后不回结果、直接断开(模拟转写到一半服务被重启)。
    drop_on_end: bool,
}

const NEW: Script = Script {
    incremental: true,
    drop_after_bytes: None,
    ping: false,
    drop_on_end: false,
};
const OLD: Script = Script {
    incremental: false,
    drop_after_bytes: None,
    ping: false,
    drop_on_end: false,
};

/// 服务端看到的事,按发生顺序;测试方也往里记(比如「松手了」),好比较先后。
type Log = Arc<Mutex<Vec<String>>>;

fn log(l: &Log, s: String) {
    l.lock().unwrap().push(s);
}

/// 起一个假服务端,第 n 条连接用 `scripts[n]`(超出的用最后一个)。
async fn fake_server(scripts: Vec<Script>, events: Log) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let mut n = 0usize;
        loop {
            let (tcp, _) = listener.accept().await.unwrap();
            let script = scripts[n.min(scripts.len() - 1)];
            let conn = n;
            n += 1;
            let events = events.clone();
            tokio::spawn(async move {
                let mut ws = tokio_tungstenite::accept_async(tcp).await.unwrap();
                let mut ready = json!({"type": "ready", "model": "fake"});
                if script.incremental {
                    ready["incremental"] = json!(true);
                }
                ws.send(Message::Text(ready.to_string())).await.unwrap();
                if script.ping {
                    ws.send(Message::Ping(b"hi".to_vec())).await.unwrap();
                }
                let mut bytes = 0usize;
                while let Some(Ok(msg)) = ws.next().await {
                    let data: Value = match msg {
                        Message::Text(t) => serde_json::from_str(&t).unwrap(),
                        Message::Pong(_) => {
                            log(&events, format!("{conn}:pong"));
                            continue;
                        }
                        Message::Close(_) => {
                            log(&events, format!("{conn}:close"));
                            break;
                        }
                        _ => continue,
                    };
                    match data["type"].as_str().unwrap() {
                        "config" => log(
                            &events,
                            format!("{conn}:config incremental={}", data["incremental"]),
                        ),
                        "audio" => {
                            use base64::Engine;
                            let pcm = base64::engine::general_purpose::STANDARD
                                .decode(data["data"].as_str().unwrap())
                                .unwrap();
                            bytes += pcm.len();
                            log(&events, format!("{conn}:audio"));
                            if script.drop_after_bytes.is_some_and(|max| bytes >= max) {
                                log(&events, format!("{conn}:dropped"));
                                return; // 直接丢掉连接,不发 close
                            }
                        }
                        "cancel" => log(&events, format!("{conn}:cancel")),
                        "end" => {
                            log(&events, format!("{conn}:end bytes={bytes}"));
                            if script.drop_on_end {
                                return;
                            }
                            let text = format!("收到 {bytes} 字节");
                            ws.send(Message::Text(
                                json!({"type": "stt_result", "text": text}).to_string(),
                            ))
                            .await
                            .unwrap();
                            ws.send(Message::Text(
                                json!({"type": "result", "text": text}).to_string(),
                            ))
                            .await
                            .unwrap();
                            let _ = ws.close(None).await;
                            break;
                        }
                        other => log(&events, format!("{conn}:{other}")),
                    }
                }
            });
        }
    });
    format!("http://{addr}")
}

/// 模拟录音:每 20 ms 一块 640 字节(16 kHz i16 单声道),共 `n` 块。
async fn record(tx: &mpsc::UnboundedSender<Vec<u8>>, n: usize) {
    for i in 0..n {
        tx.send(vec![(i % 251) as u8; 640]).unwrap();
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

fn events_of(l: &Log) -> Vec<String> {
    l.lock().unwrap().clone()
}

fn position(events: &[String], needle: &str) -> Option<usize> {
    events.iter().position(|e| e.starts_with(needle))
}

#[tokio::test]
async fn new_server_gets_audio_while_recording() {
    let events: Log = Default::default();
    let url = fake_server(vec![NEW], events.clone()).await;
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    tokio::spawn(task);

    record(&tx, 50).await; // 1 秒
    log(&events, "stop".into());
    drop(tx); // 录音停了:采集端放掉 sender
    let (etx, mut erx) = mpsc::unbounded_channel();
    let text = session.finish(Some(etx)).await.unwrap();

    assert_eq!(text, "收到 32000 字节"); // 一个字节不少
    let ev = events_of(&events);
    assert_eq!(ev[0], "0:config incremental=true");
    // 松手之前服务端就已经收到音频了
    assert!(position(&ev, "0:audio").unwrap() < position(&ev, "stop").unwrap());
    assert!(ev.iter().any(|e| e == "0:end bytes=32000"));
    // 只用了一条连接
    assert!(!ev.iter().any(|e| e.starts_with("1:")));
    // 进度事件照常转发给界面
    assert!(matches!(
        erx.recv().await,
        Some(stt::StreamEvent::SttResult { .. })
    ));
}

#[tokio::test]
async fn old_server_is_used_exactly_like_before() {
    let events: Log = Default::default();
    let url = fake_server(vec![OLD], events.clone()).await;
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    tokio::spawn(task);

    record(&tx, 25).await;
    log(&events, "stop".into());
    drop(tx);
    let text = session.finish(None).await.unwrap();

    assert_eq!(text, "收到 16000 字节");
    let ev = events_of(&events);
    // 第一条连接只看了一眼 ready 就关了,没发 config、没发音频
    assert!(!ev.iter().any(|e| e.starts_with("0:config") || e.starts_with("0:audio")));
    // 松手后第二条连接:老式 config(不带 incremental),音频全在松手之后才发
    let stop = position(&ev, "stop").unwrap();
    let cfg = position(&ev, "1:config").unwrap();
    assert!(cfg > stop);
    assert_eq!(ev[cfg], "1:config incremental=null");
    assert!(ev.iter().any(|e| e == "1:end bytes=16000"));
}

#[tokio::test]
async fn connection_lost_mid_recording_resends_everything_after_stop() {
    let events: Log = Default::default();
    let broken = Script {
        drop_after_bytes: Some(10_000),
        ..NEW
    };
    let url = fake_server(vec![broken, NEW], events.clone()).await;
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    tokio::spawn(task);

    record(&tx, 50).await;
    drop(tx);
    let text = session.finish(None).await.unwrap();

    // 断线前发出去的那部分也在:第二条连接从头重发了整段
    assert_eq!(text, "收到 32000 字节");
    let ev = events_of(&events);
    assert!(ev.iter().any(|e| e == "0:dropped"));
    assert!(ev.iter().any(|e| e == "1:end bytes=32000"));
}

#[tokio::test]
async fn connection_lost_after_end_resends_once() {
    let events: Log = Default::default();
    let broken = Script {
        drop_on_end: true,
        ..NEW
    };
    let url = fake_server(vec![broken, NEW], events.clone()).await;
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    tokio::spawn(task);

    record(&tx, 25).await;
    drop(tx);
    assert_eq!(session.finish(None).await.unwrap(), "收到 16000 字节");
    let ev = events_of(&events);
    assert!(ev.iter().any(|e| e == "0:end bytes=16000"));
    assert!(ev.iter().any(|e| e == "1:end bytes=16000"));
}

#[tokio::test]
async fn cancel_tells_the_server_and_closes() {
    let events: Log = Default::default();
    let url = fake_server(vec![NEW], events.clone()).await;
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    let handle = tokio::spawn(task);

    record(&tx, 25).await;
    drop(session); // Esc:会话被丢掉
    drop(tx);
    tokio::time::timeout(Duration::from_secs(3), handle)
        .await
        .expect("后台任务应该很快结束")
        .unwrap();
    tokio::time::sleep(Duration::from_millis(50)).await;

    let ev = events_of(&events);
    assert!(ev.iter().any(|e| e == "0:cancel"), "{ev:?}");
    assert!(ev.iter().any(|e| e == "0:close"), "{ev:?}");
    assert!(!ev.iter().any(|e| e.starts_with("0:end")));
}

#[tokio::test]
async fn pings_are_answered_while_recording() {
    // 服务端(uvicorn)每 20 秒 ping 一次,录音期间不回 pong 就会被断开。
    let events: Log = Default::default();
    let url = fake_server(vec![Script { ping: true, ..NEW }], events.clone()).await;
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    tokio::spawn(task);

    record(&tx, 25).await;
    log(&events, "stop".into());
    drop(tx);
    session.finish(None).await.unwrap();

    let ev = events_of(&events);
    assert!(position(&ev, "0:pong").unwrap() < position(&ev, "stop").unwrap());
}

#[tokio::test]
async fn unreachable_server_does_not_block_recording_and_reports_after_stop() {
    // 找一个没人监听的端口
    let port = {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap().port()
    };
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&format!("http://127.0.0.1:{port}"), rx, "zh");
    tokio::spawn(task);
    record(&tx, 10).await;
    drop(tx);
    let err = session.finish(None).await.unwrap_err();
    assert!(stt::is_unreachable(&err), "{err}");
}

#[tokio::test]
async fn instant_release_does_not_hang() {
    // 按下立刻松开:一块音频都没有,连接可能还没建好。不能卡住,照常拿到服务端的回答
    // (真服务端对空音频回 done、没有文字,客户端据此报「没有录到声音」)。
    let events: Log = Default::default();
    let url = fake_server(vec![NEW], events.clone()).await;
    let (tx, rx) = mpsc::unbounded_channel::<Vec<u8>>();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    tokio::spawn(task);
    drop(tx);
    let text = tokio::time::timeout(Duration::from_secs(5), session.finish(None))
        .await
        .expect("不能卡住");
    assert_eq!(text.unwrap(), "收到 0 字节");
}

#[test]
fn incremental_is_only_used_when_advertised() {
    assert!(stt::supports_incremental(
        &json!({"type": "ready", "incremental": true})
    ));
    assert!(!stt::supports_incremental(&json!({"type": "ready"})));
    assert!(!stt::supports_incremental(
        &json!({"type": "ready", "incremental": "yes"})
    ));
}

/// 对着真服务端跑一遍(默认不跑):
/// `VIF_LIVE_E2E_URL=http://127.0.0.1:7644 VIF_LIVE_E2E_WAV=x.wav cargo test live_e2e -- --ignored --nocapture`
/// WAV 须是 16 kHz 单声道 16 位;按真实时间一块块喂,量松手到拿到结果的时间。
/// 录音超过 40 秒时顺带验证了录音期间会回服务端(uvicorn)的 ping,不会被断开。
#[tokio::test]
#[ignore]
async fn live_e2e_against_real_server() {
    let url = std::env::var("VIF_LIVE_E2E_URL").expect("VIF_LIVE_E2E_URL");
    let wav = std::fs::read(std::env::var("VIF_LIVE_E2E_WAV").expect("VIF_LIVE_E2E_WAV")).unwrap();
    // 跳过 WAV 头:找到 "data" 块
    let data_at = wav.windows(4).position(|w| w == b"data").unwrap() + 8;
    let pcm = &wav[data_at..];
    let (tx, rx) = mpsc::unbounded_channel();
    let (session, task) = stt::LiveSession::start(&url, rx, "zh");
    tokio::spawn(task);
    let started = tokio::time::Instant::now();
    // 每 32 ms 一块(512 个采样),和 cpal 回调的粒度差不多
    for (i, piece) in pcm.chunks(1024).enumerate() {
        tx.send(piece.to_vec()).unwrap();
        tokio::time::sleep_until(started + Duration::from_millis(32 * (i as u64 + 1))).await;
    }
    drop(tx);
    let t0 = std::time::Instant::now();
    let text = session.finish(None).await.unwrap();
    println!(
        "audio {:.1}s, release→result {:.2}s\n{}",
        pcm.len() as f64 / 32000.0,
        t0.elapsed().as_secs_f64(),
        text
    );
}
