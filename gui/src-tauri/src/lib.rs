mod audio;
mod config;
mod hotkey;
mod indicator;
mod input;
mod stt;
mod tray;

use std::sync::Mutex;
use tauri::{Manager, State};
use tauri_plugin_autostart::ManagerExt;

pub struct AppState {
    pub stt: Mutex<stt::SttClient>,
    pub recorder: Mutex<audio::AudioRecorder>,
    pub config: Mutex<config::VoiceInputConfig>,
}

#[tauri::command]
async fn set_server_host(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    host: String,
) -> Result<(), String> {
    let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
    *stt_client = stt::SttClient::new(&host);
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    cfg.server.host = host;
    cfg.save(&app).ok();
    Ok(())
}

#[tauri::command]
async fn start_recording(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let device = {
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        cfg.audio.device.clone()
    };
    let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
    recorder.start(device)?;
    let _ = indicator::show(&app);
    Ok(())
}

#[tauri::command]
async fn stop_recording(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<String, String> {
    let _ = indicator::hide(&app);
    let wav = {
        let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
        recorder.stop()?
    };

    if wav.is_empty() {
        return Err("No audio captured".to_string());
    }

    // Transcribe directly in Rust - no IPC round-trip
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.transcribe_ws(wav, "auto").await
}

// ── Audio device commands ──

#[tauri::command]
async fn get_audio_devices(
    state: State<'_, AppState>,
) -> Result<Vec<audio::AudioDeviceInfo>, String> {
    let recorder = state.recorder.lock().map_err(|e| e.to_string())?;
    Ok(recorder.list_devices())
}

#[tauri::command]
async fn get_audio_level(state: State<'_, AppState>) -> Result<f32, String> {
    let recorder = state.recorder.lock().map_err(|e| e.to_string())?;
    Ok(recorder.get_level())
}

// ── Transcription ──

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
    let client = stt::SttClient::new(&host);
    let lang = language.unwrap_or_else(|| "auto".into());
    client.transcribe(audio_data, &lang).await
}

#[tauri::command]
async fn get_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.get_stt_models().await
}

#[tauri::command]
async fn switch_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.switch_stt_model(&name).await
}

#[tauri::command]
async fn get_llm_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.get_llm_models().await
}

#[tauri::command]
async fn switch_llm_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.switch_llm_model(&name).await
}

// ── Config commands ──

#[tauri::command]
async fn get_config(state: State<'_, AppState>) -> Result<config::VoiceInputConfig, String> {
    let cfg = state.config.lock().map_err(|e| e.to_string())?;
    Ok(cfg.clone())
}

#[tauri::command]
async fn update_config(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    new_config: config::VoiceInputConfig,
) -> Result<(), String> {
    // Save to file
    new_config.save(&app)?;
    // Update state
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    *cfg = new_config;
    Ok(())
}

#[tauri::command]
async fn import_old_config(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<config::VoiceInputConfig, String> {
    let app_handle = app.clone();
    let cfg = config::VoiceInputConfig::load(&app_handle);
    let mut state_cfg = state.config.lock().map_err(|e| e.to_string())?;
    *state_cfg = cfg.clone();
    Ok(cfg)
}

// ── LLM prompt commands ──

#[tauri::command]
async fn get_llm_prompt(state: State<'_, AppState>) -> Result<String, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.get_llm_prompt().await
}

#[tauri::command]
async fn save_llm_prompt(state: State<'_, AppState>, text: String) -> Result<(), String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.save_llm_prompt(&text).await
}

#[tauri::command]
async fn get_llm_enabled(state: State<'_, AppState>) -> Result<bool, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.get_llm_enabled().await
}

#[tauri::command]
async fn set_llm_enabled(state: State<'_, AppState>, enabled: bool) -> Result<(), String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.set_llm_enabled(enabled).await
}

#[tauri::command]
async fn register_hotkey(app: tauri::AppHandle, shortcut: String) -> Result<(), String> {
    if let Some(keys) = hotkey::parse_hotkey(&shortcut) {
        hotkey::start_listener(app.clone(), keys);
        eprintln!("[hotkey] Re-registered: {}", shortcut);
        Ok(())
    } else {
        Err(format!("Invalid hotkey format: {}", shortcut))
    }
}

#[tauri::command]
async fn get_autostart(app: tauri::AppHandle) -> Result<bool, String> {
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
async fn set_autostart(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    if enabled {
        app.autolaunch().enable().map_err(|e| e.to_string())
    } else {
        app.autolaunch().disable().map_err(|e| e.to_string())
    }
}

#[tauri::command]
async fn auto_input(text: String) -> Result<(), String> {
    input::type_text(&text)
}

#[tauri::command]
async fn transcribe_ws(
    state: State<'_, AppState>,
    audio_data: Vec<u8>,
    language: Option<String>,
) -> Result<String, String> {
    let host = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        stt_client.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    let lang = language.unwrap_or_else(|| "auto".into());
    client.transcribe_ws(audio_data, &lang).await
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .setup(|app| {
            let cfg = config::VoiceInputConfig::load(app.handle());
            let default_host = cfg.server.host.clone();
            let shortcut = cfg.hotkey.key.clone();
            let start_minimized = cfg.ui.start_minimized;
            app.manage(AppState {
                stt: Mutex::new(stt::SttClient::new(&default_host)),
                recorder: Mutex::new(audio::AudioRecorder::new()),
                config: Mutex::new(cfg),
            });
            // Start custom hotkey listener (supports left/right modifier + single key)
            if let Some(keys) = hotkey::parse_hotkey(&shortcut) {
                hotkey::start_listener(app.handle().clone(), keys);
                eprintln!("[hotkey] Started listener for: {}", shortcut);
            }
            let _ = tray::setup(app);

            // Start minimized if configured
            if start_minimized {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }

            // Minimize to tray on close
            if let Some(window) = app.get_webview_window("main") {
                let win = window.clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { .. } = event {
                        let _ = win.hide();
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            set_server_host,
            start_recording,
            stop_recording,
            get_audio_devices,
            get_audio_level,
            transcribe,
            transcribe_ws,
            get_models,
            switch_model,
            get_llm_models,
            switch_llm_model,
            get_config,
            update_config,
            import_old_config,
            get_llm_prompt,
            save_llm_prompt,
            get_llm_enabled,
            set_llm_enabled,
            auto_input,
            register_hotkey,
            get_autostart,
            set_autostart,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
