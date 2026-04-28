use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;

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
    pub fn new(host: &str) -> Self {
        Self {
            stt_url: format!("http://{}:6544", host),
        }
    }

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
