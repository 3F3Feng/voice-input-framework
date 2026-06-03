mod audio;
mod config;
mod hotkey;
mod indicator;
mod input;
mod log;
mod stt;
mod tray;
mod update;

use std::sync::Mutex;
use tauri::{Emitter, Manager, State};
use tauri_plugin_autostart::ManagerExt;

#[macro_export]
macro_rules! log_info {
    ($($arg:tt)*) => {{
        let msg = format!($($arg)*);
        $crate::log::__log_inner("INFO", &msg);
    }};
}

#[macro_export]
macro_rules! log_error {
    ($($arg:tt)*) => {{
        let msg = format!($($arg)*);
        $crate::log::__log_inner("ERROR", &msg);
    }};
}

pub struct AppState {
    pub stt: Mutex<stt::SttClient>,
    pub recorder: Mutex<audio::AudioRecorder>,
    pub config: Mutex<config::VoiceInputConfig>,
    pub indicator_status: std::sync::Arc<Mutex<String>>,
}

#[tauri::command]
async fn set_server_host(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    host: String,
    port: Option<u16>,
) -> Result<(), String> {
    let url = if let Some(port) = port {
        format!("http://{}:{}", host, port)
    } else {
        format!("http://{}", host)
    };
    let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
    *stt_client = stt::SttClient::new(&url);
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    cfg.server.host = host;
    if let Some(port) = port { cfg.server.port = port; }
    cfg.save(&app).ok();
    Ok(())
}

/// Start recording: acquire device, create stream, begin capture, show indicator.
pub fn start_recording_internal(app: &tauri::AppHandle, state: &AppState) -> Result<(), String> {
    let device;
    {
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        device = cfg.audio.device.clone();
    }

    {
        let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
        recorder.create_stream_channel(4096);
        match recorder.start(device) {
            Ok(()) => {
                let _ = indicator::show(app);
                Ok(())
            }
            Err(e) => {
                recorder.reset();
                Err(e)
            }
        }
    }
}

/// Stop recording: capture samples, hide indicator, transcribe.
pub fn stop_recording_internal(app: &tauri::AppHandle, state: &AppState) -> Result<String, String> {
    // Stop recording and get samples
    let (fallback_samples, src_rate) = {
        let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
        // Discard chunk receiver - we'll use batch mode
        let _ = recorder.take_chunk_receiver();
        let (samples, rate) = recorder.stop()?;
        (samples, rate)
    };

    eprintln!("[stop] samples={}, src_rate={}", fallback_samples.len(), src_rate);

    {
        let mut status = state.indicator_status.lock().map_err(|e| e.to_string())?;
        *status = "识别中...".to_string();
    }

    // Clone what we need for async
    let indicator_status = state.indicator_status.clone();
    let app_handle = app.clone();
    let stt_host = state.stt.lock().map(|c| c.stt_url.clone()).unwrap_or_else(|_| "http://localhost:6544".to_string());
    let language = state.config.lock().map(|c| c.audio.language.clone()).unwrap_or_else(|_| "auto".to_string());

    // Spawn transcription task
    tauri::async_runtime::spawn(async move {
        eprintln!("[transcribe] Starting batch transcription");
        let transcribe_start = std::time::Instant::now();

        // Encode audio to WAV
        let wav = audio::encode_wav_resampled(&fallback_samples, src_rate);
        if wav.is_empty() {
            eprintln!("[transcribe] No audio captured");
            if let Ok(mut status) = indicator_status.lock() { *status = String::new(); }
            let _ = indicator::hide(&app_handle);
            let _ = app_handle.emit("transcribe-error", "No audio captured");
            return;
        }

        // Extract PCM data
        let pcm = if wav.len() > 44 && &wav[..4] == b"RIFF" { wav[44..].to_vec() } else { wav };
        eprintln!("[transcribe] Audio size: {} bytes", pcm.len());

        // Connect and transcribe
        let client = stt::SttClient::new(&stt_host);
        let result = client.transcribe_ws(pcm, &language).await;

        let elapsed_ms = transcribe_start.elapsed().as_millis() as u64;

        match result {
            Ok(text) => {
                eprintln!("[transcribe] Done: {} chars in {}ms", text.len(), elapsed_ms);
                indicator::show_result(&app_handle, elapsed_ms);
                tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                if let Ok(mut status) = indicator_status.lock() { *status = String::new(); }
                let _ = indicator::hide(&app_handle);
                let _ = app_handle.emit("transcribe-done", text);
            }
            Err(e) => {
                eprintln!("[transcribe] Error: {}", e);
                if let Ok(mut status) = indicator_status.lock() { *status = String::new(); }
                let _ = indicator::hide(&app_handle);
                let _ = app_handle.emit("transcribe-error", e);
            }
        }
    });

    Ok(String::new())
}

// ── Audio device commands ──

#[tauri::command]
async fn get_audio_devices(state: State<'_, AppState>) -> Result<Vec<audio::AudioDeviceInfo>, String> {
    let recorder = state.recorder.lock().map_err(|e| e.to_string())?;
    Ok(recorder.list_devices())
}

#[tauri::command]
async fn get_audio_level(state: State<'_, AppState>) -> Result<f32, String> {
    let recorder = state.recorder.lock().map_err(|e| e.to_string())?;
    Ok(recorder.get_level())
}

#[tauri::command]
async fn get_indicator_status(state: State<'_, AppState>) -> Result<String, String> {
    let status = state.indicator_status.lock().map_err(|e| e.to_string())?;
    Ok(status.clone())
}

// ── Transcription ──

#[tauri::command]
async fn get_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    let client = stt::SttClient::new(&host);
    client.get_stt_models().await
}

#[tauri::command]
async fn get_platform_info(state: State<'_, AppState>) -> Result<stt::PlatformInfo, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    let client = stt::SttClient::new(&host);
    client.get_platform().await
}

#[tauri::command]
async fn switch_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    stt::SttClient::new(&host).switch_stt_model(&name).await
}

#[tauri::command]
async fn get_llm_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    stt::SttClient::new(&host).get_llm_models().await
}

#[tauri::command]
async fn switch_llm_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    stt::SttClient::new(&host).switch_llm_model(&name).await
}

// ── Config commands ──

#[tauri::command]
async fn get_config(state: State<'_, AppState>) -> Result<config::VoiceInputConfig, String> {
    Ok(state.config.lock().map_err(|e| e.to_string())?.clone())
}

#[tauri::command]
async fn update_config(app: tauri::AppHandle, state: State<'_, AppState>, new_config: config::VoiceInputConfig) -> Result<(), String> {
    new_config.save(&app)?;
    *state.config.lock().map_err(|e| e.to_string())? = new_config;
    Ok(())
}

#[tauri::command]
async fn import_old_config(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<config::VoiceInputConfig, String> {
    let cfg = config::VoiceInputConfig::load(&app);
    *state.config.lock().map_err(|e| e.to_string())? = cfg.clone();
    Ok(cfg)
}

// ── LLM prompt commands ──

#[tauri::command]
async fn get_llm_prompt(state: State<'_, AppState>) -> Result<String, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    stt::SttClient::new(&host).get_llm_prompt().await
}

#[tauri::command]
async fn save_llm_prompt(state: State<'_, AppState>, text: String) -> Result<(), String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    stt::SttClient::new(&host).save_llm_prompt(&text).await
}

#[tauri::command]
async fn get_llm_enabled(state: State<'_, AppState>) -> Result<bool, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    stt::SttClient::new(&host).get_llm_enabled().await
}

#[tauri::command]
async fn set_llm_enabled(state: State<'_, AppState>, enabled: bool) -> Result<(), String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    stt::SttClient::new(&host).set_llm_enabled(enabled).await
}

// ── Diarize commands ──

#[tauri::command]
async fn get_diarize_speakers(state: State<'_, AppState>) -> Result<u32, String> {
    let cfg = state.config.lock().map_err(|e| e.to_string())?;
    Ok(cfg.diarize.num_speakers)
}

#[tauri::command]
async fn set_diarize_speakers(app: tauri::AppHandle, state: State<'_, AppState>, num_speakers: u32) -> Result<(), String> {
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    cfg.diarize.num_speakers = num_speakers;
    cfg.save(&app)?;
    Ok(())
}

#[tauri::command]
async fn auto_input(text: String) -> Result<(), String> { input::type_text(&text) }

#[tauri::command]
async fn minimize_to_tray(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("main") { let _ = window.hide(); }
    Ok(())
}

#[tauri::command]
async fn check_update(app: tauri::AppHandle) -> Result<update::UpdateInfo, String> {
    eprintln!("[update] Checking for updates...");
    update::check(&app).await
}

#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<String, String> {
    eprintln!("[update] Starting install...");
    update::download_and_install(&app).await
}

#[tauri::command]
async fn transcribe_ws(
    state: State<'_, AppState>,
    audio_data: Vec<u8>,
    language: Option<String>,
) -> Result<String, String> {
    let host = { let c = state.stt.lock().map_err(|e| e.to_string())?; c.stt_url.clone() };
    let lang = language.unwrap_or_else(|| "auto".into());
    stt::SttClient::new(&host).transcribe_ws(audio_data, &lang).await
}

#[tauri::command]
async fn start_recording(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    start_recording_internal(&app, &state)
}

#[tauri::command]
async fn stop_recording(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<String, String> {
    stop_recording_internal(&app, &state)
}

#[tauri::command]
async fn register_hotkey(app: tauri::AppHandle, shortcut: String) -> Result<(), String> {
    if let Some(keys) = hotkey::parse_hotkey(&shortcut) {
        hotkey::start_listener(app.clone(), keys);
        eprintln!("[hotkey] Re-registered: {}", shortcut);
        Ok(())
    } else { Err(format!("Invalid hotkey format: {}", shortcut)) }
}

#[tauri::command]
async fn get_autostart(app: tauri::AppHandle) -> Result<bool, String> {
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
async fn set_autostart(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    if enabled { app.autolaunch().enable().map_err(|e| e.to_string()) }
    else { app.autolaunch().disable().map_err(|e| e.to_string()) }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(tauri_plugin_autostart::MacosLauncher::LaunchAgent, Some(vec![])))
        .setup(|app| {
            let cfg = config::VoiceInputConfig::load(app.handle());
            let default_host = cfg.server.host.clone();
            let shortcut = cfg.hotkey.key.clone();
            let start_minimized = cfg.ui.start_minimized;

            app.manage(AppState {
                stt: Mutex::new(stt::SttClient::new(&default_host)),
                recorder: Mutex::new(audio::AudioRecorder::new()),
                config: Mutex::new(cfg),
                indicator_status: std::sync::Arc::new(Mutex::new(String::new())),
            });

            log::init(app.handle());

            let _ = tray::setup(app);

            if let Some(keys) = hotkey::parse_hotkey(&shortcut) {
                hotkey::start_listener(app.handle().clone(), keys);
                eprintln!("[hotkey] Started listener for: {}", shortcut);
            }

            if start_minimized {
                if let Some(w) = app.get_webview_window("main") { let _ = w.hide(); }
            }

            if let Some(window) = app.get_webview_window("main") {
                let win = window.clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { .. } = event {
                        hotkey::reset_state();
                        let _ = win.hide();
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            set_server_host, start_recording, stop_recording,
            get_audio_devices, get_audio_level, get_indicator_status,
            transcribe_ws, get_models, get_platform_info, switch_model,
            get_llm_models, switch_llm_model,
            get_config, update_config, import_old_config,
            get_llm_prompt, save_llm_prompt, get_llm_enabled, set_llm_enabled,
            auto_input, minimize_to_tray,
            get_diarize_speakers, set_diarize_speakers,
            register_hotkey, get_autostart, set_autostart,
            check_update, install_update,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
