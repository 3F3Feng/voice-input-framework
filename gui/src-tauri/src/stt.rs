use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;

const STT_URL: &str = "http://localhost:6544";
const _LLM_URL: &str = "http://localhost:6545";

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub is_loaded: bool,
}

pub async fn transcribe(audio_data: Vec<u8>, language: &str) -> Result<String, String> {
    let client = Client::new();
    let part = reqwest::multipart::Part::bytes(audio_data)
        .file_name("audio.wav")
        .mime_str("audio/wav")
        .map_err(|e| e.to_string())?;

    let form = reqwest::multipart::Form::new()
        .part("file", part)
        .text("language", language.to_string());

    let resp = client
        .post(format!("{}/transcribe", STT_URL))
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("HTTP error: {}", e))?;

    let data: Value = resp.json().await.map_err(|e| format!("JSON error: {}", e))?;
    Ok(data["text"].as_str().unwrap_or("").to_string())
}

pub async fn get_stt_models() -> Result<Vec<ModelInfo>, String> {
    let client = Client::new();
    let resp = client
        .get(format!("{}/models", STT_URL))
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

pub async fn switch_stt_model(name: &str) -> Result<String, String> {
    let client = Client::new();
    let params = [("model_name", name)];
    let resp = client
        .post(format!("{}/models/select", STT_URL))
        .form(&params)
        .send()
        .await
        .map_err(|e| e.to_string())?;

    let data: Value = resp.json().await.map_err(|e| e.to_string())?;
    Ok(data["message"].as_str().unwrap_or("").to_string())
}

pub async fn get_llm_models() -> Result<Vec<ModelInfo>, String> {
    let client = Client::new();
    let resp = client
        .get("http://localhost:6545/models")
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

pub async fn switch_llm_model(name: &str) -> Result<String, String> {
    let client = Client::new();
    let body = serde_json::json!({"model_name": name});
    let resp = client
        .post("http://localhost:6545/models/select")
        .json(&body)
        .send()
        .await
        .map_err(|e| e.to_string())?;

    let data: Value = resp.json().await.map_err(|e| e.to_string())?;
    Ok(data["message"].as_str().unwrap_or("").to_string())
}
