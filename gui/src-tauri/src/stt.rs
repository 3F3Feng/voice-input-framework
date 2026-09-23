use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::mpsc;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::Message;

use crate::i18n::t;
use crate::tr;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub is_loaded: bool,
    // 以下给界面用(见 services/model_catalog.py)。老服务端没有这些字段,
    // 一律带默认值:描述空着就退回显示名字,可用性默认为「可用」。
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub memory_gb: Option<f64>,
    #[serde(default = "default_true")]
    pub available: bool,
    #[serde(default)]
    pub unavailable_reason: Option<String>,
    #[serde(default)]
    pub downloaded: Option<bool>,
    #[serde(default)]
    pub recommended: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Deserialize)]
struct LlmModelsResponse {
    models: Vec<ModelInfo>,
    #[allow(dead_code)]
    current_model: Option<String>,
    #[allow(dead_code)]
    enabled: Option<bool>,
}

/// LLM 后处理开关的状态(`GET /llm/enabled`)。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LlmStatus {
    pub enabled: bool,
    /// 这台服务能不能做 LLM 后处理:Apple Silicon 用 MLX,其它平台要装了 llama.cpp。
    pub supported: bool,
    /// 不支持时的原因,前端原样显示在置灰的开关旁边。
    pub reason: Option<String>,
}

impl LlmStatus {
    /// 老服务端只回 `{"enabled": bool}`:没有 `supported` 就当作支持,行为和以前一样。
    pub fn from_json(data: &Value) -> Result<Self, String> {
        let enabled = data["enabled"]
            .as_bool()
            .ok_or_else(|| {
                t(
                    "读取后处理开关失败:服务端没有返回 enabled",
                    "Reading the LLM post-processing setting failed: the server didn't return `enabled`",
                )
                .to_string()
            })?;
        Ok(Self {
            enabled,
            supported: data["supported"].as_bool().unwrap_or(true),
            reason: data["reason"]
                .as_str()
                .filter(|r| !r.trim().is_empty())
                .map(str::to_string),
        })
    }
}

/// 连不上服务类错误的统一前缀。悬浮胶囊按前缀归类(`indicator::failure_display`),
/// 以前按「连不上」「超时」这些词去猜,改一句文案就会悄悄归错类。
///
/// 界面是英文时错误以 [`ERR_UNREACHABLE_EN`] 开头。归类请用 [`is_unreachable`],
/// 它两种语言都认——错误可能是切换语言之前生成的。
pub const ERR_UNREACHABLE: &str = "连不上 STT 服务";
pub const ERR_UNREACHABLE_EN: &str = "Can't reach the STT service";
/// 等识别结果超时类错误的统一前缀。英文见 [`ERR_RESULT_TIMEOUT_EN`],归类用 [`is_result_timeout`]。
pub const ERR_RESULT_TIMEOUT: &str = "等待识别结果超时";
pub const ERR_RESULT_TIMEOUT_EN: &str = "Timed out waiting for the transcription result";

/// 按当前界面语言取 [`ERR_UNREACHABLE`] / [`ERR_UNREACHABLE_EN`]。
fn err_unreachable() -> &'static str {
    t(ERR_UNREACHABLE, ERR_UNREACHABLE_EN)
}

/// 按当前界面语言取 [`ERR_RESULT_TIMEOUT`] / [`ERR_RESULT_TIMEOUT_EN`]。
fn err_result_timeout() -> &'static str {
    t(ERR_RESULT_TIMEOUT, ERR_RESULT_TIMEOUT_EN)
}

/// 是不是「连不上 STT 服务」类错误(中英文前缀都认)。
// `indicator::failure_display` 改用这两个判断之前,主程序里还没有调用方。
#[allow(dead_code)]
pub fn is_unreachable(err: &str) -> bool {
    err.starts_with(ERR_UNREACHABLE) || err.starts_with(ERR_UNREACHABLE_EN)
}

/// 是不是「等待识别结果超时」类错误(中英文前缀都认)。
#[allow(dead_code)]
pub fn is_result_timeout(err: &str) -> bool {
    err.starts_with(ERR_RESULT_TIMEOUT) || err.starts_with(ERR_RESULT_TIMEOUT_EN)
}

/// 没录到任何音频时的错误。前端按这句话认出「没听到声音」,而不是当成故障。
///
/// 它是个**标记**,不随界面语言变:所有接收方(`indicator::failure_display`、
/// App.vue、Onboarding.vue)都按相等比较认出它,换成各自的友好文案再显示,
/// 从来不把它原样给用户看。翻译它反而会让这些比较在英文界面下失效。
pub const NO_SPEECH: &str = "没有录到声音";

/// 服务端给出的最终文本 → 转写结果。空的、只有空白的都算「没听到声音」。
///
/// 以前只有 `done` 分支会这么判,`result` 分支拿到空串照样 `Ok("")`:
/// 录了一段静音,胶囊亮绿灯、显示耗时,像是成功了,然后什么都没发生。
pub fn require_speech(text: String) -> Result<String, String> {
    if text.trim().is_empty() {
        Err(NO_SPEECH.to_string())
    } else {
        Ok(text)
    }
}

/// 一条 WS 音频消息最多攒这么多字节(16 kHz i16 单声道约 2 秒)。
pub const MAX_AUDIO_FRAME_BYTES: usize = 64 * 1024;

/// 把通道里已经排着的分块拼到 `first` 后面,凑成一条消息,直到 `max_bytes`。
///
/// 录音期间分块只进不出(松手才开始消费),采集回调每次只给几百字节,
/// 5 分钟的录音能攒下两三万块。一块一条 WS 消息的话,松手后光是逐条
/// base64 + 加锁发送就要好一会儿。每块都是完整的 i16 采样,直接拼接是安全的。
pub fn coalesce_chunks(
    first: Vec<u8>,
    rx: &mut mpsc::UnboundedReceiver<Vec<u8>>,
    max_bytes: usize,
) -> Vec<u8> {
    let mut buf = first;
    while buf.len() < max_bytes {
        match rx.try_recv() {
            Ok(chunk) => buf.extend_from_slice(&chunk),
            Err(_) => break,
        }
    }
    buf
}

/// Events emitted during streaming transcription
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type")]
pub enum StreamEvent {
    #[serde(rename = "stt_result")]
    SttResult { text: String },
    #[serde(rename = "llm_start")]
    LlmStart { text: String },
    #[serde(rename = "llm_progress")]
    LlmProgress { text: String },
    #[serde(rename = "result")]
    FinalResult {
        text: String,
        llm_latency_ms: Option<f64>,
        /// LLM 后处理开着却没做成时的原因(此时 `text` 是原文)。见 `llm_error_of`。
        #[serde(skip_serializing_if = "Option::is_none")]
        llm_error: Option<String>,
    },
    #[serde(rename = "error")]
    Error { message: String },
}

/// `result` 消息里的 `llm_error`:LLM 后处理失败、退回了原文时的原因。
///
/// 以前服务端在 LLM 失败时静默返回原文,用户看到一段没加标点、满是填充词的
/// 文字,还以为后处理就这水平。老服务端没有这个字段、成功时它是 null,两种都
/// 当成「没有问题」。
pub fn llm_error_of(data: &Value) -> Option<String> {
    data["llm_error"]
        .as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

/// Extract a human-readable message from a server payload.
/// Error frames/responses carry `error_message` (see shared/data_types.py
/// ErrorResponse); success responses carry `message`.
pub(crate) fn server_message(data: &Value) -> &str {
    data["error_message"]
        .as_str()
        .or_else(|| data["message"].as_str())
        // FastAPI 的 HTTPException 回的是 `{"detail": "..."}`。以前不认它,
        // `/models/select` 的 400 / 500 解出来是空串,调用方又不看状态码,
        // 于是切换失败也弹「模型已切换」。
        .or_else(|| data["detail"].as_str())
        .unwrap_or("")
}

/// 把 STT 服务的 HTTP 地址换成对应的 WebSocket 地址。
///
/// 以前只替换 `http://`:远程填 `https://` 时原样交给 tungstenite,scheme 不认,
/// 于是「能列出模型、一转写就失败」。
pub(crate) fn ws_base(stt_url: &str) -> String {
    if let Some(rest) = stt_url.strip_prefix("https://") {
        format!("wss://{}", rest)
    } else if let Some(rest) = stt_url.strip_prefix("http://") {
        format!("ws://{}", rest)
    } else {
        stt_url.to_string()
    }
}

/// 等下一条服务端消息的上限。见 `transcribe_stream` 里 `saw_keepalive` 的说明。
pub(crate) fn result_wait(saw_keepalive: bool) -> Duration {
    if saw_keepalive {
        Duration::from_secs(60)
    } else {
        Duration::from_secs(300)
    }
}

/// 连接超时。服务不在时本机是立刻被拒;远程地址被丢包时,不设它要等系统的
/// TCP 超时(几十秒),界面就一直停在「连接中」。
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
/// 普通查询(模型列表、开关、提示词)的总超时。
const QUERY_TIMEOUT: Duration = Duration::from_secs(15);
/// 切换 LLM 模型要等 LLM 服务把模型加载完,STT 那头的转发自己等 30 秒,
/// 这里比它多留一点,好让服务端写好的失败原因回得来。
const LLM_SWITCH_TIMEOUT: Duration = Duration::from_secs(45);

/// 远程服务要求的访问令牌(F20,服务端 shared/auth.py)。只在远程模式下设置;
/// 本应用拉起的本地服务不带令牌。放在全局是因为 `SttClient` 到处都是临时 new 出来的。
static API_TOKEN: std::sync::RwLock<Option<String>> = std::sync::RwLock::new(None);

pub fn set_api_token(token: Option<String>) {
    if let Ok(mut t) = API_TOKEN.write() {
        *t = token
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty());
    }
}

fn api_token() -> Option<String> {
    API_TOKEN.read().ok().and_then(|t| t.clone())
}

/// 发给服务端的 `Accept-Language`:服务端按它挑错误信息的语言(F22)。
pub fn accept_language() -> &'static str {
    accept_language_for(crate::i18n::is_en())
}

pub fn accept_language_for(english: bool) -> &'static str {
    if english {
        "en"
    } else {
        "zh"
    }
}

/// 带超时的 HTTP 客户端。以前每处都是 `Client::new()` —— 默认**没有任何超时**,
/// 服务卡住时请求会一直挂着,前端连接循环的「截止时间」形同虚设。
///
/// 每个请求都现 new 一个,所以 `Accept-Language` 按发请求这一刻的界面语言走。
fn http(timeout: Duration) -> Client {
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert(
        reqwest::header::ACCEPT_LANGUAGE,
        reqwest::header::HeaderValue::from_static(accept_language()),
    );
    if let Some(token) = api_token() {
        if let Ok(v) = reqwest::header::HeaderValue::from_str(&format!("Bearer {}", token)) {
            headers.insert(reqwest::header::AUTHORIZATION, v);
        }
    }
    Client::builder()
        .connect_timeout(CONNECT_TIMEOUT)
        .timeout(timeout)
        .default_headers(headers)
        .build()
        .unwrap_or_else(|_| Client::new())
}

/// 请求没发出去 / 没等到回答时,给用户看的那句话。
fn request_error(what: &str, e: reqwest::Error) -> String {
    if e.is_timeout() {
        tr!(
            "{}超时:服务没有应答",
            "{} timed out: no response from the service",
            what
        )
    } else if e.is_connect() {
        tr!(
            "{}失败:连不上服务({})",
            "{} failed: can't reach the service ({})",
            what,
            e
        )
    } else {
        tr!("{}失败:{}", "{} failed: {}", what, e)
    }
}

/// 非 2xx 就变成 Err,优先用服务端写好的原因。
async fn ensure_ok(resp: reqwest::Response, what: &str) -> Result<Value, String> {
    let status = resp.status();
    let data: Value = resp.json().await.unwrap_or(Value::Null);
    if !status.is_success() {
        let msg = server_message(&data);
        return Err(if msg.is_empty() {
            tr!(
                "{}失败(HTTP {})",
                "{} failed (HTTP {})",
                what,
                status.as_u16()
            )
        } else {
            tr!("{}失败:{}", "{} failed: {}", what, msg)
        });
    }
    Ok(data)
}

/// 模型切换是否失败。两种失败都要认:
/// - HTTP 非 2xx —— 服务端现在这么答;
/// - 200 但 `status: "failed"` —— 老服务端的答法,也正是「切换明明失败、
///   界面却弹『已切换』」的来源:调用方只看有没有 HTTP 错误就下结论了。
pub(crate) fn switch_failed(http_ok: bool, data: &Value) -> bool {
    !http_ok || data["status"].as_str() == Some("failed")
}

pub struct SttClient {
    pub stt_url: String,
}

impl SttClient {
    pub fn new(host_or_url: &str) -> Self {
        if host_or_url.starts_with("http://") || host_or_url.starts_with("https://") {
            Self {
                stt_url: host_or_url.to_string(),
            }
        } else {
            Self {
                stt_url: format!("http://{}:6544", host_or_url),
            }
        }
    }

    // ── WebSocket streaming transcription (real-time) ──

    pub async fn transcribe_stream(
        &self,
        mut chunk_rx: mpsc::UnboundedReceiver<Vec<u8>>,
        language: &str,
        event_tx: Option<mpsc::UnboundedSender<StreamEvent>>,
    ) -> Result<String, String> {
        let url = format!("{}/ws/stream", ws_base(&self.stt_url));

        let mut request = url.as_str().into_client_request().map_err(|e| {
            tr!(
                "{}({}):地址不对:{}",
                "{} ({}): invalid address: {}",
                err_unreachable(),
                self.stt_url,
                e
            )
        })?;
        // 服务端按它挑错误信息的语言,和 HTTP 请求一样(见 `http`)。
        request.headers_mut().insert(
            "Accept-Language",
            tokio_tungstenite::tungstenite::http::HeaderValue::from_static(accept_language()),
        );
        if let Some(token) = api_token() {
            if let Ok(v) = format!("Bearer {}", token).parse() {
                request.headers_mut().insert("Authorization", v);
            }
        }
        let (mut ws, _) = tokio::time::timeout(CONNECT_TIMEOUT, connect_async(request))
            .await
            .map_err(|_| {
                tr!(
                    "{}({}):连接超时",
                    "{} ({}): connection timed out",
                    err_unreachable(),
                    self.stt_url
                )
            })?
            .map_err(|e| {
                tr!(
                    "{}({}):{}",
                    "{} ({}): {}",
                    err_unreachable(),
                    self.stt_url,
                    e
                )
            })?;

        match tokio::time::timeout(QUERY_TIMEOUT, ws.next()).await {
            Ok(Some(Ok(Message::Text(json)))) => {
                let data: Value = serde_json::from_str(&json).map_err(|e| {
                    tr!(
                        "服务端消息解析失败: {}",
                        "Couldn't parse the server message: {}",
                        e
                    )
                })?;
                if data["type"] != "ready" {
                    return Err(tr!(
                        "服务端返回了意外的消息: {}",
                        "Unexpected message from the server: {}",
                        json
                    ));
                }
                eprintln!("[stt] Server ready, model: {}", data["model"]);
            }
            Err(_) => {
                return Err(tr!(
                    "{}:服务没有应答(等待就绪消息超时)",
                    "{}: no response from the service (timed out waiting for the ready message)",
                    err_unreachable()
                ))
            }
            _ => {
                return Err(tr!(
                    "{}:服务没有发来就绪消息",
                    "{}: the service didn't send a ready message",
                    err_unreachable()
                ))
            }
        }

        let lang_msg = serde_json::json!({"type": "config", "language": language});
        SinkExt::send(&mut ws, Message::Text(lang_msg.to_string()))
            .await
            .map_err(|e| {
                tr!(
                    "发送识别配置失败: {}",
                    "Couldn't send the recognition settings: {}",
                    e
                )
            })?;

        // Spawn task to stream audio chunks
        let (audio_done_tx, audio_done_rx) = tokio::sync::oneshot::channel::<()>();
        let ws_sender = Arc::new(tokio::sync::Mutex::new(ws));

        let ws_clone = ws_sender.clone();
        let stream_task = tokio::spawn(async move {
            let mut chunk_count: u64 = 0;
            let mut byte_count: u64 = 0;
            while let Some(first) = chunk_rx.recv().await {
                let chunk = coalesce_chunks(first, &mut chunk_rx, MAX_AUDIO_FRAME_BYTES);
                chunk_count += 1;
                byte_count += chunk.len() as u64;
                let b64 = base64::engine::general_purpose::STANDARD.encode(&chunk);
                let audio_msg = serde_json::json!({"type": "audio", "data": b64});
                let mut ws = ws_clone.lock().await;
                if let Err(e) = SinkExt::send(&mut *ws, Message::Text(audio_msg.to_string())).await
                {
                    eprintln!("[stt] Failed to send audio chunk: {}", e);
                    break;
                }
            }
            eprintln!(
                "[stt] Streaming complete: {} frames, {} bytes",
                chunk_count, byte_count
            );
            let _ = audio_done_tx.send(());
        });

        // Wait for streaming to finish, then send end signal
        let _ = audio_done_rx.await;
        {
            let mut ws = ws_sender.lock().await;
            SinkExt::send(&mut *ws, Message::Text(r#"{"type":"end"}"#.into()))
                .await
                .map_err(|e| {
                    tr!(
                        "发送结束信号失败: {}",
                        "Couldn't send the end-of-audio signal: {}",
                        e
                    )
                })?;
        }

        // Receive result(s)
        let mut final_text = String::new();
        let ws_recv = ws_sender.clone();
        // 服务端转写 / 后处理期间每 5 秒发一条 progress(stt_server.py 的
        // `_with_keepalive`)。收到过心跳就知道它在干活,之后 60 秒没动静就是真挂了;
        // 老服务端不发心跳,只能照旧等满上限。
        let mut saw_keepalive = false;
        loop {
            let wait = result_wait(saw_keepalive);
            let msg_result = {
                let mut ws = ws_recv.lock().await;
                tokio::time::timeout(wait, (*ws).next()).await
            };

            let msg = match msg_result {
                Ok(Some(Ok(m))) => m,
                Ok(Some(Err(e))) => {
                    return Err(tr!(
                        "读取识别结果失败: {}",
                        "Couldn't read the transcription result: {}",
                        e
                    ))
                }
                Ok(None) => break,
                Err(_) => {
                    let _ = stream_task.await;
                    return Err(if saw_keepalive {
                        tr!(
                            "{}:识别服务 {} 秒没有动静,可能已经卡住",
                            "{}: the STT service has been silent for {}s and may be stuck",
                            err_result_timeout(),
                            wait.as_secs()
                        )
                    } else {
                        tr!("{}(5 分钟)", "{} (5 minutes)", err_result_timeout())
                    });
                }
            };

            match msg {
                Message::Text(json) => {
                    let data: Value = serde_json::from_str(&json).map_err(|e| {
                        tr!(
                            "服务端消息解析失败: {}",
                            "Couldn't parse the server message: {}",
                            e
                        )
                    })?;
                    let msg_type = data["type"].as_str().unwrap_or("");
                    match msg_type {
                        "stt_result" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if !text.is_empty() {
                                final_text = text.to_string();
                                if let Some(ref tx) = event_tx {
                                    let _ = tx.send(StreamEvent::SttResult {
                                        text: text.to_string(),
                                    });
                                }
                            }
                        }
                        "result" => {
                            let text = data["text"].as_str().unwrap_or("");
                            let llm_ms = data["llm_latency_ms"].as_f64();
                            if !text.is_empty() {
                                final_text = text.to_string();
                            }
                            if let Some(ref tx) = event_tx {
                                let _ = tx.send(StreamEvent::FinalResult {
                                    text: final_text.clone(),
                                    llm_latency_ms: llm_ms,
                                    llm_error: llm_error_of(&data),
                                });
                            }
                            let _ = stream_task.await;
                            return require_speech(final_text);
                        }
                        "llm_start" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if let Some(ref tx) = event_tx {
                                let _ = tx.send(StreamEvent::LlmStart {
                                    text: text.to_string(),
                                });
                            }
                        }
                        "progress" => saw_keepalive = true,
                        "llm_progress" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if let Some(ref tx) = event_tx {
                                let _ = tx.send(StreamEvent::LlmProgress {
                                    text: text.to_string(),
                                });
                            }
                        }
                        "done" => {
                            let _ = stream_task.await;
                            return require_speech(final_text);
                        }
                        "error" => {
                            let _ = stream_task.await;
                            let msg = server_message(&data);
                            return Err(if msg.is_empty() {
                                t("未知错误", "Unknown error").to_string()
                            } else {
                                msg.to_string()
                            });
                        }
                        _ => {}
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }

        let _ = stream_task.await;
        if final_text.is_empty() {
            Err(t(
                "服务在返回结果前断开了连接",
                "The service disconnected before returning a result",
            )
            .to_string())
        } else {
            Ok(final_text)
        }
    }

    // ── HTTP endpoints ──
    //
    // 每个请求都要看状态码。以前 `switch_stt_model` / `save_llm_prompt` /
    // `set_llm_enabled` 只要 HTTP 往返成功就算成功:服务端回 400 / 502,界面照样
    // 弹「模型已切换」「提示词已保存」。

    pub async fn get_stt_models(&self) -> Result<Vec<ModelInfo>, String> {
        let resp = http(QUERY_TIMEOUT)
            .get(format!("{}/models", self.stt_url))
            .send()
            .await
            .map_err(|e| request_error(t("获取模型列表", "Loading the model list"), e))?;
        let data = ensure_ok(resp, t("获取模型列表", "Loading the model list")).await?;
        let models = data.as_array().ok_or(t(
            "获取模型列表失败:服务端返回的不是列表",
            "Loading the model list failed: the server didn't return a list",
        ))?;
        Ok(models
            .iter()
            .filter_map(|m| serde_json::from_value::<ModelInfo>(m.clone()).ok())
            .collect())
    }

    pub async fn switch_stt_model(&self, name: &str) -> Result<String, String> {
        let params = [("model_name", name)];
        let resp = http(QUERY_TIMEOUT)
            .post(format!("{}/models/select", self.stt_url))
            .form(&params)
            .send()
            .await
            .map_err(|e| request_error(t("切换模型", "Switching the model"), e))?;
        let status = resp.status();
        let data: Value = resp.json().await.unwrap_or(Value::Null);
        if switch_failed(status.is_success(), &data) {
            let msg = server_message(&data);
            return Err(if msg.is_empty() {
                tr!(
                    "切换失败(HTTP {})",
                    "Switch failed (HTTP {})",
                    status.as_u16()
                )
            } else {
                msg.to_string()
            });
        }
        Ok(server_message(&data).to_string())
    }

    /// 查询某个模型的加载状态,切换后前端轮询它,等真正加载完才说「已切换」。
    pub async fn get_model_status(&self, name: &str) -> Result<Value, String> {
        let resp = http(QUERY_TIMEOUT)
            .get(format!("{}/models/status/{}", self.stt_url, name))
            .send()
            .await
            .map_err(|e| request_error(t("查询模型状态", "Checking the model status"), e))?;
        ensure_ok(resp, t("查询模型状态", "Checking the model status")).await
    }

    /// STT 服务的 `/health` 原样返回,前端用它区分「可达 / 模型就绪 / 加载中 / 加载失败」。
    pub async fn get_health(&self) -> Result<Value, String> {
        let resp = http(Duration::from_secs(5))
            .get(format!("{}/health", self.stt_url))
            .send()
            .await
            .map_err(|e| request_error(t("健康检查", "Health check"), e))?;
        ensure_ok(resp, t("健康检查", "Health check")).await
    }

    pub async fn get_llm_models(&self) -> Result<Vec<ModelInfo>, String> {
        let resp = http(QUERY_TIMEOUT)
            .get(format!("{}/llm/models", self.stt_url))
            .send()
            .await
            .map_err(|e| request_error(t("获取 LLM 模型列表", "Loading the LLM model list"), e))?;
        // 转发失败现在带 5xx + 结构化错误体;直接按 LlmModelsResponse 解只会
        // 得到一句「missing field `models`」,把服务端写好的原因盖掉。
        let data = ensure_ok(resp, t("获取 LLM 模型列表", "Loading the LLM model list")).await?;
        let data: LlmModelsResponse = serde_json::from_value(data).map_err(|e| e.to_string())?;
        Ok(data.models)
    }

    pub async fn switch_llm_model(&self, name: &str) -> Result<String, String> {
        let body = serde_json::json!({"model_name": name});
        let resp = http(LLM_SWITCH_TIMEOUT)
            .post(format!("{}/llm/models/select", self.stt_url))
            .json(&body)
            .send()
            .await
            .map_err(|e| request_error(t("切换 LLM 模型", "Switching the LLM model"), e))?;
        let status = resp.status();
        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        // 切换失败必须变成 Err,否则前端照样弹「LLM 已切换」。
        if switch_failed(status.is_success(), &data) {
            let msg = server_message(&data);
            return Err(if msg.is_empty() {
                tr!(
                    "切换失败(HTTP {})",
                    "Switch failed (HTTP {})",
                    status.as_u16()
                )
            } else {
                msg.to_string()
            });
        }
        Ok(server_message(&data).to_string())
    }

    pub async fn get_llm_prompt(&self) -> Result<String, String> {
        let resp = http(QUERY_TIMEOUT)
            .get(format!("{}/llm/prompt", self.stt_url))
            .send()
            .await
            .map_err(|e| request_error(t("读取提示词", "Loading the prompt"), e))?;
        let data = ensure_ok(resp, t("读取提示词", "Loading the prompt")).await?;
        Ok(data["prompt"].as_str().unwrap_or("").to_string())
    }

    pub async fn save_llm_prompt(&self, text: &str) -> Result<(), String> {
        let body = serde_json::json!({"prompt": text});
        let resp = http(QUERY_TIMEOUT)
            .put(format!("{}/llm/prompt", self.stt_url))
            .json(&body)
            .send()
            .await
            .map_err(|e| request_error(t("保存提示词", "Saving the prompt"), e))?;
        ensure_ok(resp, t("保存提示词", "Saving the prompt"))
            .await
            .map(|_| ())
    }

    /// 上传一个音频文件转写(服务端 `/transcribe`,见 services/audio_io.py)。
    pub async fn transcribe_file(
        &self,
        bytes: Vec<u8>,
        file_name: &str,
        language: &str,
    ) -> Result<String, String> {
        let part = reqwest::multipart::Part::bytes(bytes).file_name(file_name.to_string());
        let form = reqwest::multipart::Form::new()
            .part("file", part)
            .text("language", language.to_string());
        // 长文件在 CPU 上可能要转好几分钟,服务端自己的上限是 600 秒。
        let resp = http(Duration::from_secs(660))
            .post(format!("{}/transcribe", self.stt_url))
            .multipart(form)
            .send()
            .await
            .map_err(|e| request_error(t("转写文件", "Transcribing the file"), e))?;
        let data = ensure_ok(resp, t("转写文件", "Transcribing the file")).await?;
        Ok(data["text"].as_str().unwrap_or("").to_string())
    }

    /// 个人词库(服务端 services/vocabulary.py):原始的若干行。
    pub async fn get_vocabulary(&self) -> Result<Value, String> {
        let resp = http(QUERY_TIMEOUT)
            .get(format!("{}/vocabulary", self.stt_url))
            .send()
            .await
            .map_err(|e| request_error(t("读取个人词库", "Loading the personal vocabulary"), e))?;
        ensure_ok(resp, t("读取个人词库", "Loading the personal vocabulary")).await
    }

    pub async fn save_vocabulary(&self, entries: &[String]) -> Result<Value, String> {
        let resp = http(QUERY_TIMEOUT)
            .put(format!("{}/vocabulary", self.stt_url))
            .json(&serde_json::json!({ "entries": entries }))
            .send()
            .await
            .map_err(|e| request_error(t("保存个人词库", "Saving the personal vocabulary"), e))?;
        ensure_ok(resp, t("保存个人词库", "Saving the personal vocabulary")).await
    }

    /// 恢复默认提示词,返回恢复后的内容。
    pub async fn reset_llm_prompt(&self) -> Result<String, String> {
        let resp = http(QUERY_TIMEOUT)
            .delete(format!("{}/llm/prompt", self.stt_url))
            .send()
            .await
            .map_err(|e| request_error(t("恢复默认提示词", "Restoring the default prompt"), e))?;
        let data = ensure_ok(resp, t("恢复默认提示词", "Restoring the default prompt")).await?;
        Ok(data["prompt"].as_str().unwrap_or("").to_string())
    }

    pub async fn get_llm_enabled(&self) -> Result<bool, String> {
        self.get_llm_status().await.map(|s| s.enabled)
    }

    pub async fn get_llm_status(&self) -> Result<LlmStatus, String> {
        let resp = http(QUERY_TIMEOUT)
            .get(format!("{}/llm/enabled", self.stt_url))
            .send()
            .await
            .map_err(|e| {
                request_error(
                    t("读取后处理开关", "Reading the LLM post-processing setting"),
                    e,
                )
            })?;
        let data = ensure_ok(
            resp,
            t("读取后处理开关", "Reading the LLM post-processing setting"),
        )
        .await?;
        LlmStatus::from_json(&data)
    }

    pub async fn set_llm_enabled(&self, enabled: bool) -> Result<(), String> {
        let body = serde_json::json!({"enabled": enabled});
        let resp = http(QUERY_TIMEOUT)
            .put(format!("{}/llm/enabled", self.stt_url))
            .json(&body)
            .send()
            .await
            .map_err(|e| {
                request_error(
                    t("设置后处理开关", "Changing the LLM post-processing setting"),
                    e,
                )
            })?;
        ensure_ok(
            resp,
            t("设置后处理开关", "Changing the LLM post-processing setting"),
        )
        .await
        .map(|_| ())
    }
}
