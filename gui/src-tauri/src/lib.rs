mod hotkey;
mod stt;

use tauri::Manager;

#[tauri::command]
async fn transcribe(audio_data: Vec<u8>, language: Option<String>) -> Result<String, String> {
    stt::transcribe(audio_data, &language.unwrap_or_else(|| "auto".into())).await
}

#[tauri::command]
async fn get_models() -> Result<Vec<stt::ModelInfo>, String> {
    stt::get_stt_models().await
}

#[tauri::command]
async fn switch_model(name: String) -> Result<String, String> {
    stt::switch_stt_model(&name).await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let _ = hotkey::setup(app);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![transcribe, get_models, switch_model])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
