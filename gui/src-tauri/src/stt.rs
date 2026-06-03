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

impl SttClient {
    pub fn new(host_or_url: &str) -> Self {
        if host_or_url.starts_with("http://") || host_or_url.starts_with("https://") {
            Self { stt_url: host_or_url.to_string() }
        } else {
            Self { stt_url: format!("http://{}:6544", host_or_url) }
        }
    }

    // ── Simple batch transcription (connect, send all, get result) ──

    pub async fn transcribe_ws(&self, audio_data: Vec<u8>, language: &str) -> Result<String, String> {
        let ws_url = self.stt_url.replace("http://", "ws://");
        let url = format!("{}/ws/stream", ws_url);
        let (mut ws, _) = connect_async(&url).await.map_err(|e| format!("WebSocket connect failed: {}", e))?;

        // Wait for ready
        match ws.next().await {
            Some(Ok(Message::Text(json))) => {
                let data: Value = serde_json::from_str(&json).map_err(|e| format!("JSON parse: {}", e))?;
                if data["type"] != "ready" { return Err(format!("Unexpected server message: {}", json)); }
            }
            _ => return Err("Expected text ready message".to_string()),
        }

        // Send config
        let lang_msg = serde_json::json!({"type": "config", "language": language});
        SinkExt::send(&mut ws, Message::Text(lang_msg.to_string())).await
            .map_err(|e| format!("WebSocket send config failed: {}", e))?;

        // Send audio
        let pcm = if audio_data.len() > 44 && &audio_data[..4] == b"RIFF" { &audio_data[44..] } else { &audio_data[..] };
        let b64 = base64::engine::general_purpose::STANDARD.encode(pcm);
        let audio_msg = serde_json::json!({"type": "audio", "data": b64});
        SinkExt::send(&mut ws, Message::Text(audio_msg.to_string())).await
            .map_err(|e| format!("WebSocket send audio failed: {}", e))?;

        // Send end
        SinkExt::send(&mut ws, Message::Text(r#"{"type":"end"}"#.into())).await
            .map_err(|e| format!("WebSocket send end failed: {}", e))?;

        // Wait for result
        while let Some(msg) = ws.next().await {
            let msg = msg.map_err(|e| format!("WebSocket read failed: {}", e))?;
            if let Message::Text(json) = msg {
                let data: Value = serde_json::from_str(&json).map_err(|e| format!("JSON parse: {}", e))?;
                match data["type"].as_str().unwrap_or("") {
                    "result" | "stt_result" => {
                        let text = data["text"].as_str().unwrap_or("");
                        if !text.is_empty() { return Ok(text.to_string()); }
                    }
                    "done" => return Err("No speech detected".to_string()),
                    "error" => return Err(data["message"].as_str().unwrap_or("Unknown error").to_string()),
                    _ => {}
                }
            }
        }
        Err("Connection closed without result".to_string())
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
        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        let models = data["models"].as_array().unwrap_or(&vec![]);
        Ok(models.iter().map(|m| ModelInfo {
            name: m["name"].as_str().unwrap_or("").to_string(),
            is_loaded: m["is_loaded"].as_bool().unwrap_or(false),
            is_available: true,
        }).collect())
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
