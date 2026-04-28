use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub is_loaded: bool,
}

pub struct SttClient {
    pub stt_url: String,
    pub llm_url: String,
}

impl SttClient {
    pub fn new(host: &str) -> Self {
        Self {
            stt_url: format!("http://{}:6544", host),
            llm_url: format!("http://{}:6545", host),
        }
    }

    /// Create from full URL (for commands that read host from state)
    pub fn new_from_url(url: &str) -> Self {
        Self {
            stt_url: url.to_string(),
            llm_url: url.replace(":6544", ":6545"),
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

    pub async fn get_llm_models(&self) -> Result<Vec<ModelInfo>, String> {
        let client = Client::new();
        let resp = client
            .get(format!("{}/models", self.llm_url))
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

    pub async fn switch_llm_model(&self, name: &str) -> Result<String, String> {
        let client = Client::new();
        let body = serde_json::json!({"model_name": name});
        let resp = client
            .post(format!("{}/models/select", self.llm_url))
            .json(&body)
            .send()
            .await
            .map_err(|e| e.to_string())?;

        let data: Value = resp.json().await.map_err(|e| e.to_string())?;
        Ok(data["message"].as_str().unwrap_or("").to_string())
    }
}
