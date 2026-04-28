mod audio;
mod hotkey;
mod stt;

use std::sync::Mutex;
use tauri::State;

pub struct AppState {
    pub recorder: Mutex<audio::AudioRecorder>,
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
async fn transcribe(audio_data: Vec<u8>, language: Option<String>) -> Result<String, String> {
    let lang = language.unwrap_or_else(|| "auto".into());
    stt::transcribe(audio_data, &lang).await
}

#[tauri::command]
async fn get_models() -> Result<Vec<stt::ModelInfo>, String> {
    stt::get_stt_models().await
}

#[tauri::command]
async fn switch_model(name: String) -> Result<String, String> {
    stt::switch_stt_model(&name).await
}

#[tauri::command]
async fn get_llm_models() -> Result<Vec<stt::ModelInfo>, String> {
    stt::get_llm_models().await
}

#[tauri::command]
async fn switch_llm_model(name: String) -> Result<String, String> {
    stt::switch_llm_model(&name).await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState {
            recorder: Mutex::new(audio::AudioRecorder::new()),
        })
        .setup(|app| {
            let _ = hotkey::setup(app);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
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
