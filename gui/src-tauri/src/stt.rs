use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::mpsc;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub is_loaded: bool,
    #[serde(default = "default_true")]
    pub is_available: bool,
}

fn default_true() -> bool { true }

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PlatformInfo {
    pub system: String,
    pub arch: String,
    pub backend: String,
    pub gpu: Option<GPUInfo>,
    pub recommended_stt: Option<String>,
    pub available_models: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct GPUInfo {
    pub name: Option<String>,
    pub memory_gb: Option<f64>,
}

#[derive(Debug, Deserialize)]
struct LlmModelsResponse {
    models: Vec<ModelInfo>,
    #[allow(dead_code)]
    current_model: Option<String>,
    #[allow(dead_code)]
    enabled: Option<bool>,
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
    FinalResult { text: String, llm_latency_ms: Option<f64> },
    #[serde(rename = "error")]
    Error { message: String },
}

pub struct SttClient {
    pub stt_url: String,
}

/// A pre-connected WebSocket session for streaming audio
pub struct StreamingSession {
    ws_sender: Arc<tokio::sync::Mutex<tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>>>,
    result_rx: tokio::sync::mpsc::UnboundedReceiver<StreamEvent>,
    done_tx: Option<tokio::sync::oneshot::Sender<()>>,
}

impl SttClient {
    pub fn new(host_or_url: &str) -> Self {
        if host_or_url.starts_with("http://") || host_or_url.starts_with("https://") {
            Self { stt_url: host_or_url.to_string() }
        } else {
            Self { stt_url: format!("http://{}:6544", host_or_url) }
        }
    }

    /// Pre-connect WebSocket for streaming. Call this BEFORE recording starts.
    pub async fn connect_stream(&self, language: &str) -> Result<StreamingSession, String> {
        let ws_url = self.stt_url.replace("http://", "ws://");
        let url = format!("{}/ws/stream", ws_url);

        eprintln!("[stt] Pre-connecting WebSocket to {}", url);
        let (mut ws, _) = connect_async(&url).await
            .map_err(|e| format!("WebSocket connect failed: {}", e))?;

        // Wait for ready message
        match ws.next().await {
            Some(Ok(Message::Text(json))) => {
                let data: Value = serde_json::from_str(&json).map_err(|e| format!("JSON parse: {}", e))?;
                if data["type"] != "ready" {
                    return Err(format!("Unexpected server message: {}", json));
                }
                eprintln!("[stt] Server ready, model: {}", data["model"]);
            }
            _ => return Err("Expected text ready message".to_string()),
        }

        // Send config
        let lang_msg = serde_json::json!({"type": "config", "language": language});
        SinkExt::send(&mut ws, Message::Text(lang_msg.to_string())).await
            .map_err(|e| format!("WebSocket send config failed: {}", e))?;

        eprintln!("[stt] WebSocket connected and configured");

        let ws_sender = Arc::new(tokio::sync::Mutex::new(ws));
        let (result_tx, result_rx) = tokio::sync::mpsc::unbounded_channel::<StreamEvent>();
        let (done_tx, done_rx) = tokio::sync::oneshot::channel::<()>();

        // Spawn result reader task
        let ws_clone = ws_sender.clone();
        let result_tx_clone = result_tx.clone();
        tokio::spawn(async move {
            let mut final_text = String::new();
            loop {
                let msg = {
                    let mut ws = ws_clone.lock().await;
                    ws.next().await
                };
                
                match msg {
                    Some(Ok(Message::Text(json))) => {
                        let data: Value = match serde_json::from_str(&json) {
                            Ok(d) => d,
                            Err(_) => continue,
                        };
                        let msg_type = data["type"].as_str().unwrap_or("");
                        match msg_type {
                            "stt_result" => {
                                let text = data["text"].as_str().unwrap_or("");
                                if !text.is_empty() {
                                    final_text = text.to_string();
                                    let _ = result_tx_clone.send(StreamEvent::SttResult { text: text.to_string() });
                                }
                            }
                            "result" => {
                                let text = data["text"].as_str().unwrap_or("");
                                let llm_ms = data["llm_latency_ms"].as_f64();
                                if !text.is_empty() { final_text = text.to_string(); }
                                let _ = result_tx_clone.send(StreamEvent::FinalResult { 
                                    text: final_text.clone(), 
                                    llm_latency_ms: llm_ms 
                                });
                                break;
                            }
                            "llm_start" => {
                                let text = data["text"].as_str().unwrap_or("");
                                let _ = result_tx_clone.send(StreamEvent::LlmStart { text: text.to_string() });
                            }
                            "llm_progress" => {
                                let text = data["text"].as_str().unwrap_or("");
                                let _ = result_tx_clone.send(StreamEvent::LlmProgress { text: text.to_string() });
                            }
                            "done" => {
                                if final_text.is_empty() {
                                    let _ = result_tx_clone.send(StreamEvent::Error { message: "No speech detected".to_string() });
                                }
                                break;
                            }
                            "error" => {
                                let msg = data["message"].as_str().unwrap_or("Unknown error");
                                let _ = result_tx_clone.send(StreamEvent::Error { message: msg.to_string() });
                                break;
                            }
                            _ => {}
                        }
                    }
                    Some(Ok(Message::Close(_))) | None => break,
                    _ => {}
                }
            }
        });

        Ok(StreamingSession {
            ws_sender,
            result_rx,
            done_tx: Some(done_tx),
        })
    }
}

impl StreamingSession {
    /// Send an audio chunk to the server during recording
    pub async fn send_chunk(&self, chunk: Vec<u8>) -> Result<(), String> {
        let b64 = base64::engine::general_purpose::STANDARD.encode(&chunk);
        let audio_msg = serde_json::json!({"type": "audio", "data": b64});
        let mut ws = self.ws_sender.lock().await;
        SinkExt::send(&mut *ws, Message::Text(audio_msg.to_string())).await
            .map_err(|e| format!("Failed to send audio chunk: {}", e))
    }

    /// Signal end of recording and wait for final result
    pub async fn finish(mut self) -> Result<String, String> {
        // Send end signal
        {
            let mut ws = self.ws_sender.lock().await;
            SinkExt::send(&mut *ws, Message::Text(r#"{"type":"end"}"#.into())).await
                .map_err(|e| format!("Failed to send end signal: {}", e))?;
        }

        // Wait for final result
        while let Some(event) = self.result_rx.recv().await {
            match event {
                StreamEvent::FinalResult { text, .. } => return Ok(text),
                StreamEvent::Error { message } => return Err(message),
                _ => {} // Ignore intermediate events
            }
        }
        
        Err("Connection closed without result".to_string())
    }

    /// Get a receiver for streaming events (for progress updates)
    pub fn take_event_receiver(&mut self) -> tokio::sync::mpsc::UnboundedReceiver<StreamEvent> {
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        // We need to replace the existing receiver
        // This is a simplified approach - in production you'd want better design
        rx
    }
}

impl SttClient {
    // ── WebSocket streaming transcription (real-time) ──

    pub async fn transcribe_stream(
        &self,
        mut chunk_rx: mpsc::Receiver<Vec<u8>>,
        language: &str,
        event_tx: Option<mpsc::UnboundedSender<StreamEvent>>,
    ) -> Result<String, String> {
        let ws_url = self.stt_url.replace("http://", "ws://");
        let url = format!("{}/ws/stream", ws_url);

        let (mut ws, _) = connect_async(&url).await
            .map_err(|e| format!("WebSocket connect failed: {}", e))?;

        match ws.next().await {
            Some(Ok(Message::Text(json))) => {
                let data: Value = serde_json::from_str(&json).map_err(|e| format!("JSON parse: {}", e))?;
                if data["type"] != "ready" {
                    return Err(format!("Unexpected server message: {}", json));
                }
                eprintln!("[stt] Server ready, model: {}", data["model"]);
            }
            _ => return Err("Expected text ready message".to_string()),
        }

        let lang_msg = serde_json::json!({"type": "config", "language": language});
        SinkExt::send(&mut ws, Message::Text(lang_msg.to_string())).await
            .map_err(|e| format!("WebSocket send config failed: {}", e))?;

        // Spawn task to stream audio chunks
        let (audio_done_tx, mut audio_done_rx) = tokio::sync::oneshot::channel::<()>();
        let ws_sender = Arc::new(tokio::sync::Mutex::new(ws));

        let ws_clone = ws_sender.clone();
        let stream_task = tauri::async_runtime::spawn(async move {
            let mut chunk_count: u64 = 0;
            let mut byte_count: u64 = 0;
            while let Some(chunk) = chunk_rx.recv().await {
                chunk_count += 1;
                byte_count += chunk.len() as u64;
                let b64 = base64::engine::general_purpose::STANDARD.encode(&chunk);
                let audio_msg = serde_json::json!({"type": "audio", "data": b64});
                let mut ws = ws_clone.lock().await;
                if let Err(e) = SinkExt::send(&mut *ws, Message::Text(audio_msg.to_string())).await {
                    eprintln!("[stt] Failed to send audio chunk: {}", e);
                    break;
                }
            }
            eprintln!("[stt] Streaming complete: {} chunks, {} bytes", chunk_count, byte_count);
            let _ = audio_done_tx.send(());
        });

        // Wait for streaming to finish, then send end signal
        let _ = audio_done_rx.await;
        {
            let mut ws = ws_sender.lock().await;
            SinkExt::send(&mut *ws, Message::Text(r#"{"type":"end"}"#.into())).await
                .map_err(|e| format!("WebSocket send end failed: {}", e))?;
        }

        // Receive result(s)
        let mut final_text = String::new();
        let ws_recv = ws_sender.clone();
        loop {
            let msg_result = {
                let mut ws = ws_recv.lock().await;
                tokio::time::timeout(std::time::Duration::from_secs(300), (&mut *ws).next()).await
            };

            let msg = match msg_result {
                Ok(Some(Ok(m))) => m,
                Ok(Some(Err(e))) => return Err(format!("WebSocket read failed: {}", e)),
                Ok(None) => break,
                Err(_) => {
                    let _ = stream_task.await;
                    return Err("Result timeout (5 min)".to_string());
                }
            };

            match msg {
                Message::Text(json) => {
                    let data: Value = serde_json::from_str(&json).map_err(|e| format!("JSON parse: {}", e))?;
                    let msg_type = data["type"].as_str().unwrap_or("");
                    match msg_type {
                        "stt_result" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if !text.is_empty() {
                                final_text = text.to_string();
                                if let Some(ref tx) = event_tx {
                                    let _ = tx.send(StreamEvent::SttResult { text: text.to_string() });
                                }
                            }
                        }
                        "result" => {
                            let text = data["text"].as_str().unwrap_or("");
                            let llm_ms = data["llm_latency_ms"].as_f64();
                            if !text.is_empty() { final_text = text.to_string(); }
                            if let Some(ref tx) = event_tx {
                                let _ = tx.send(StreamEvent::FinalResult { text: final_text.clone(), llm_latency_ms: llm_ms });
                            }
                            let _ = stream_task.await;
                            return Ok(final_text);
                        }
                        "llm_start" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if let Some(ref tx) = event_tx { let _ = tx.send(StreamEvent::LlmStart { text: text.to_string() }); }
                        }
                        "llm_progress" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if let Some(ref tx) = event_tx { let _ = tx.send(StreamEvent::LlmProgress { text: text.to_string() }); }
                        }
                        "done" => {
                            let _ = stream_task.await;
                            return if final_text.is_empty() { Err("No speech detected".to_string()) } else { Ok(final_text) };
                        }
                        "error" => {
                            let _ = stream_task.await;
                            return Err(data["message"].as_str().unwrap_or("Unknown error").to_string());
                        }
                        _ => {}
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }

        let _ = stream_task.await;
        if final_text.is_empty() { Err("Connection closed without result".to_string()) } else { Ok(final_text) }
    }

    /// Fallback: send entire audio as a single WebSocket message (batch mode).
    pub async fn transcribe_ws(&self, audio_data: Vec<u8>, language: &str) -> Result<String, String> {
        let ws_url = self.stt_url.replace("http://", "ws://");
        let url = format!("{}/ws/stream", ws_url);
        let (mut ws, _) = connect_async(&url).await.map_err(|e| format!("WebSocket connect failed: {}", e))?;

        match ws.next().await {
            Some(Ok(Message::Text(json))) => {
                let data: Value = serde_json::from_str(&json).map_err(|e| format!("JSON parse: {}", e))?;
                if data["type"] != "ready" { return Err(format!("Unexpected server message: {}", json)); }
            }
            _ => return Err("Expected text ready message".to_string()),
        }

        let lang_msg = serde_json::json!({"type": "config", "language": language});
        SinkExt::send(&mut ws, Message::Text(lang_msg.to_string())).await
            .map_err(|e| format!("WebSocket send config failed: {}", e))?;

        let pcm_data = if audio_data.len() > 44 && &audio_data[..4] == b"RIFF" { &audio_data[44..] } else { &audio_data[..] };
        let b64 = base64::engine::general_purpose::STANDARD.encode(pcm_data);
        let audio_msg = serde_json::json!({"type": "audio", "data": b64});
        SinkExt::send(&mut ws, Message::Text(audio_msg.to_string())).await
            .map_err(|e| format!("WebSocket send audio failed: {}", e))?;
        SinkExt::send(&mut ws, Message::Text(r#"{"type":"end"}"#.into())).await
            .map_err(|e| format!("WebSocket send end failed: {}", e))?;

        let mut final_text = String::new();
        while let Some(msg) = ws.next().await {
            let msg = msg.map_err(|e| format!("WebSocket read failed: {}", e))?;
            match msg {
                Message::Text(json) => {
                    let data: Value = serde_json::from_str(&json).map_err(|e| format!("JSON parse: {}", e))?;
                    match data["type"].as_str().unwrap_or("") {
                        "result" | "stt_result" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if !text.is_empty() { final_text = text.to_string(); }
                            if data["type"].as_str().unwrap_or("") == "result" { return Ok(final_text); }
                        }
                        "done" => return if final_text.is_empty() { Err("No speech detected".to_string()) } else { Ok(final_text) },
                        "error" => return Err(data["message"].as_str().unwrap_or("Unknown error").to_string()),
                        _ => {}
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
        if final_text.is_empty() { Err("Connection closed without result".to_string()) } else { Ok(final_text) }
    }

    // ── HTTP endpoints ──

    pub async fn get_stt_models(&self) -> Result<Vec<ModelInfo>, String> {
        let client = Client::new();
        let resp = client.get(format!("{}/models", self.stt_url)).send().await.map_err(|e| e.to_string())?;
        let models: Vec<Value> = resp.json().await.map_err(|e| e.to_string())?;
        Ok(models.iter().map(|m| ModelInfo {
            name: m["name"].as_str().unwrap_or("").to_string(),
            is_loaded: m["is_loaded"].as_bool().unwrap_or(false),
            is_available: m["is_available"].as_bool().unwrap_or(true),
        }).collect())
    }

    pub async fn get_platform(&self) -> Result<PlatformInfo, String> {
        let client = Client::new();
        let resp = client.get(format!("{}/platform", self.stt_url)).send().await.map_err(|e| e.to_string())?;
        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        
        Ok(PlatformInfo {
            system: data["system"].as_str().unwrap_or("unknown").to_string(),
            arch: data["arch"].as_str().unwrap_or("unknown").to_string(),
            backend: data["backend"].as_str().unwrap_or("cpu").to_string(),
            gpu: data["gpu"].as_object().map(|g| GPUInfo {
                name: g.get("name").and_then(|v| v.as_str()).map(|s| s.to_string()),
                memory_gb: g.get("memory_gb").and_then(|v| v.as_f64()),
            }),
            recommended_stt: data["recommended"]["stt_model"].as_str().map(|s| s.to_string()),
            available_models: data["available_models"].as_array()
                .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
                .unwrap_or_default(),
        })
    }

    pub async fn switch_stt_model(&self, name: &str) -> Result<String, String> {
        let client = Client::new();
        let params = [("model_name", name)];
        let resp = client.post(format!("{}/models/select", self.stt_url)).form(&params).send().await.map_err(|e| e.to_string())?;
        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data["message"].as_str().unwrap_or("").to_string())
    }

    pub async fn get_llm_models(&self) -> Result<Vec<ModelInfo>, String> {
        let client = Client::new();
        let resp = client.get(format!("{}/llm/models", self.stt_url)).send().await.map_err(|e| e.to_string())?;
        let data: LlmModelsResponse = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data.models)
    }

    pub async fn switch_llm_model(&self, name: &str) -> Result<String, String> {
        let client = Client::new();
        let body = serde_json::json!({"model_name": name});
        let resp = client.post(format!("{}/llm/models/select", self.stt_url)).json(&body).send().await.map_err(|e| e.to_string())?;
        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data["message"].as_str().unwrap_or("").to_string())
    }

    pub async fn get_llm_prompt(&self) -> Result<String, String> {
        let client = Client::new();
        let resp = client.get(format!("{}/llm/prompt", self.stt_url)).send().await.map_err(|e| format!("HTTP error: {}", e))?;
        let data: Value = resp.json().await.map_err(|e| format!("JSON error: {}", e))?;
        Ok(data["prompt"].as_str().unwrap_or("").to_string())
    }

    pub async fn save_llm_prompt(&self, text: &str) -> Result<(), String> {
        let client = Client::new();
        let body = serde_json::json!({"prompt": text});
        client.put(format!("{}/llm/prompt", self.stt_url)).json(&body).send().await.map_err(|e| format!("HTTP error: {}", e))?;
        Ok(())
    }

    pub async fn get_llm_enabled(&self) -> Result<bool, String> {
        let client = Client::new();
        let resp = client.get(format!("{}/llm/enabled", self.stt_url)).send().await.map_err(|e| e.to_string())?;
        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data["enabled"].as_bool().unwrap_or(true))
    }

    pub async fn set_llm_enabled(&self, enabled: bool) -> Result<(), String> {
        let client = Client::new();
        let body = serde_json::json!({"enabled": enabled});
        client.put(format!("{}/llm/enabled", self.stt_url)).json(&body).send().await.map_err(|e| format!("HTTP error: {}", e))?;
        Ok(())
    }
}
