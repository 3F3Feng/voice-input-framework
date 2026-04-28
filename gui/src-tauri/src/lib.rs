mod audio;
mod hotkey;
mod stt;

use std::sync::Mutex;
use tauri::State;

pub struct AppState {
    pub stt: Mutex<stt::SttClient>,
    pub recorder: Mutex<audio::AudioRecorder>,
}

#[tauri::command]
async fn set_server_host(state: State<'_, AppState>, host: String) -> Result<(), String> {
    let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
    *stt_client = stt::SttClient::new(&host);
    Ok(())
}

#[tauri::command]
async fn start_recording(state: State<'_, AppState>) -> Result<(), String> {
    let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
    recorder.start()
}

#[tauri::command]
async fn stop_recording(state: State<'_, AppState>) -> Result<Vec<u8>, String> {
    let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
    Ok(recorder.stop())
}

#[tauri::command]
async fn transcribe(
    state: State<'_, AppState>,
    audio_data: Vec<u8>,
    language: Option<String>,
) -> Result<String, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new_from_url(&host);
    let lang = language.unwrap_or_else(|| "auto".into());
    client.transcribe(audio_data, &lang).await
}

#[tauri::command]
async fn get_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new_from_url(&host);
    client.get_stt_models().await
}

#[tauri::command]
async fn switch_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new_from_url(&host);
    client.switch_stt_model(&name).await
}

#[tauri::command]
async fn get_llm_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let (stt_url, llm_url) = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        (stt_client.stt_url.clone(), stt_client.llm_url.clone())
    };
    let client = stt::SttClient { stt_url, llm_url };
    client.get_llm_models().await
}

#[tauri::command]
async fn switch_llm_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new_from_url(&host);
    client.switch_llm_model(&name).await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let default_host = std::env::var("VIF_SERVER_HOST").unwrap_or_else(|_| "localhost".into());

    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState {
            stt: Mutex::new(stt::SttClient::new(&default_host)),
            recorder: Mutex::new(audio::AudioRecorder::new()),
        })
        .setup(|app| {
            let _ = hotkey::setup(app);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            set_server_host,
            start_recording,
            stop_recording,
            transcribe,
            get_models,
            switch_model,
            get_llm_models,
            switch_llm_model,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
