use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub is_loaded: bool,
}

/// STT server response for LLM models endpoint
#[derive(Debug, Deserialize)]
struct LlmModelsResponse {
    models: Vec<ModelInfo>,
    #[serde(default)]
    current_model: Option<String>,
    #[serde(default)]
    enabled: Option<bool>,
}

pub struct SttClient {
    pub stt_url: String,
}

impl SttClient {
    pub fn new(host_or_url: &str) -> Self {
        // Accept either a bare hostname or a full URL
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

    // ── HTTP transcription (legacy) ──

    pub async fn transcribe(&self, audio_data: Vec<u8>, language: &str) -> Result<String, String> {
        let client = Client::new();
        let part = reqwest::multipart::Part::bytes(audio_data)
            .file_name("audio.wav")
            .mime_str("audio/wav")
            .map_err(|e| e.to_string())?;

        let form = reqwest::multipart::Form::new()
            .part("file", part)
            .text("language", language.to_string());

        let resp = client
            .post(format!("{}/transcribe", self.stt_url))
            .multipart(form)
            .send()
            .await
            .map_err(|e| format!("HTTP error: {}", e))?;

        let data: Value = resp.json().await.map_err(|e| format!("JSON error: {}", e))?;
        Ok(data["text"].as_str().unwrap_or("").to_string())
    }

    // ── WebSocket transcription (streaming) ──

    pub async fn transcribe_ws(&self, audio_data: Vec<u8>, language: &str) -> Result<String, String> {
        let ws_url = self.stt_url.replace("http://", "ws://");
        let url = format!("{}/ws/stream", ws_url);

        let (mut ws, _) = connect_async(&url)
            .await
            .map_err(|e| format!("WebSocket connect failed: {}", e))?;

        // Wait for "ready" message from server
        match ws.next().await {
            Some(Ok(Message::Text(json))) => {
                let data: Value = serde_json::from_str(&json)
                    .map_err(|e| format!("JSON parse: {}", e))?;
                if data["type"] != "ready" {
                    return Err(format!("Unexpected server message: {}", json));
                }
            }
            _ => return Err("Expected text ready message".to_string()),
        }

        // Strip WAV header if present (WebSocket expects raw PCM)
        let pcm_data = if audio_data.len() > 44 && &audio_data[..4] == b"RIFF" {
            &audio_data[44..]
        } else {
            &audio_data[..]
        };

        // Send audio data in chunks, base64-encoded in JSON messages
        for chunk in pcm_data.chunks(4096) {
            let b64 = base64::engine::general_purpose::STANDARD.encode(chunk);
            let msg = serde_json::json!({"type": "audio", "data": b64});
            SinkExt::send(&mut ws, Message::Text(msg.to_string()))
                .await
                .map_err(|e| format!("WebSocket send audio failed: {}", e))?;
        }

        // Signal end of audio
        SinkExt::send(&mut ws, Message::Text(r#"{"type":"end"}"#.into()))
            .await
            .map_err(|e| format!("WebSocket send end failed: {}", e))?;

        // Receive result(s)
        let mut final_text = String::new();
        while let Some(msg) = ws.next().await {
            let msg = msg.map_err(|e| format!("WebSocket read failed: {}", e))?;
            match msg {
                Message::Text(json) => {
                    let data: Value = serde_json::from_str(&json)
                        .map_err(|e| format!("JSON parse: {}", e))?;

                    let msg_type = data["type"].as_str().unwrap_or("");
                    match msg_type {
                        "result" | "stt_result" => {
                            let text = data["text"].as_str().unwrap_or("");
                            if !text.is_empty() {
                                final_text = text.to_string();
                                // For "result", this is the final LLM-processed text
                                if msg_type == "result" {
                                    return Ok(final_text);
                                }
                            }
                        }
                        "error" => {
                            return Err(data["message"].as_str().unwrap_or("Unknown error").to_string());
                        }
                        "llm_start" | "llm_progress" => {
                            // LLM is processing, wait for "result"
                        }
                        _ => {}
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }

        if final_text.is_empty() {
            Err("Connection closed without result".to_string())
        } else {
            Ok(final_text)
        }
    }

    // ── STT model endpoints ──

    pub async fn get_stt_models(&self) -> Result<Vec<ModelInfo>, String> {
        let client = Client::new();
        let resp = client
            .get(format!("{}/models", self.stt_url))
            .send()
            .await
            .map_err(|e| e.to_string())?;

        let models: Vec<Value> = resp.json().await.map_err(|e| e.to_string())?;
        Ok(models
            .iter()
            .map(|m| ModelInfo {
                name: m["name"].as_str().unwrap_or("").to_string(),
                is_loaded: m["is_loaded"].as_bool().unwrap_or(false),
            })
            .collect())
    }

    pub async fn switch_stt_model(&self, name: &str) -> Result<String, String> {
        let client = Client::new();
        let params = [("model_name", name)];
        let resp = client
            .post(format!("{}/models/select", self.stt_url))
            .form(&params)
            .send()
            .await
            .map_err(|e| e.to_string())?;

        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data["message"].as_str().unwrap_or("").to_string())
    }

    // ── LLM endpoints (routed through STT server) ──

    pub async fn get_llm_models(&self) -> Result<Vec<ModelInfo>, String> {
        let client = Client::new();
        let resp = client
            .get(format!("{}/llm/models", self.stt_url))
            .send()
            .await
            .map_err(|e| e.to_string())?;

        let data: LlmModelsResponse = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data.models)
    }

    pub async fn switch_llm_model(&self, name: &str) -> Result<String, String> {
        let client = Client::new();
        let body = serde_json::json!({"model_name": name});
        let resp = client
            .post(format!("{}/llm/models/select", self.stt_url))
            .json(&body)
            .send()
            .await
            .map_err(|e| e.to_string())?;

        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data["message"].as_str().unwrap_or("").to_string())
    }

    pub async fn get_llm_prompt(&self) -> Result<String, String> {
        let client = Client::new();
        let resp = client
            .get(format!("{}/llm/prompt", self.stt_url))
            .send()
            .await
            .map_err(|e| format!("HTTP error: {}", e))?;

        let data: Value = resp.json().await.map_err(|e| format!("JSON error: {}", e))?;
        Ok(data["prompt"].as_str().unwrap_or("").to_string())
    }

    pub async fn save_llm_prompt(&self, text: &str) -> Result<(), String> {
        let client = Client::new();
        let body = serde_json::json!({"prompt": text});
        client
            .put(format!("{}/llm/prompt", self.stt_url))
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("HTTP error: {}", e))?;
        Ok(())
    }

    pub async fn get_llm_enabled(&self) -> Result<bool, String> {
        let client = Client::new();
        let resp = client
            .get(format!("{}/llm/enabled", self.stt_url))
            .send()
            .await
            .map_err(|e| e.to_string())?;

        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data["enabled"].as_bool().unwrap_or(true))
    }

    pub async fn set_llm_enabled(&self, enabled: bool) -> Result<(), String> {
        let client = Client::new();
        let body = serde_json::json!({"enabled": enabled});
        client
            .put(format!("{}/llm/enabled", self.stt_url))
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("HTTP error: {}", e))?;
        Ok(())
    }
}
