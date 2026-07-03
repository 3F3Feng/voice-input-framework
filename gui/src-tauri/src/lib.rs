mod audio;
mod config;
mod hotkey;
mod indicator;
mod input;
mod log;
mod stt;
mod tray;
mod update;

use std::sync::{Mutex, OnceLock};
use tauri::{Emitter, Manager, State};
use tauri_plugin_autostart::ManagerExt;

// IPv6 support cache: OnceLock ensures we only test once per app launch
static IPV6_SUPPORTED: OnceLock<bool> = OnceLock::new();

/// Test if IPv6 localhost works (cached - only runs once per app launch)
fn is_ipv6_supported() -> bool {
    *IPV6_SUPPORTED.get_or_init(|| {
        // Try to connect to IPv6 localhost
        use std::net::TcpStream;
        use std::time::Duration;
        
        // Try IPv6 localhost first
        let result = TcpStream::connect_timeout(
            &"[::1]:0".parse().unwrap(),
            Duration::from_millis(100),
        );
        
        // If connection refused, IPv6 is working (just no server)
        // If other error, IPv6 might not be supported
        let supported = match result {
            Err(e) => {
                // Connection refused means IPv6 works, just no server listening
                e.kind() == std::io::ErrorKind::ConnectionRefused ||
                e.kind() == std::io::ErrorKind::TimedOut
            }
            Ok(_) => true,
        };
        
        eprintln!("[network] IPv6 support: {}", supported);
        supported
    })
}

/// Resolve host, preferring IPv4 if IPv6 is not supported
fn resolve_host(host: &str) -> String {
    // If it's localhost and IPv6 is not supported, use 127.0.0.1
    if host == "localhost" && !is_ipv6_supported() {
        eprintln!("[network] IPv6 not available, using 127.0.0.1 instead of localhost");
        return "127.0.0.1".to_string();
    }
    host.to_string()
}

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

pub struct ActiveTranscription {
    pub result_rx: tokio::sync::oneshot::Receiver<Result<String, String>>,
}

pub struct AppState {
    pub stt: Mutex<stt::SttClient>,
    pub recorder: Mutex<audio::AudioRecorder>,
    pub config: Mutex<config::VoiceInputConfig>,
    pub indicator_status: std::sync::Arc<Mutex<String>>,
    pub active_transcription: Mutex<Option<ActiveTranscription>>,
}

#[tauri::command]
async fn set_server_host(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    host: String,
    port: Option<u16>,
) -> Result<(), String> {
    // Resolve host (handles IPv6/IPv4 fallback)
    let resolved_host = resolve_host(&host);
    
    let url = if let Some(port) = port {
        format!("http://{}:{}", resolved_host, port)
    } else {
        format!("http://{}", resolved_host)
    };
    let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
    *stt_client = stt::SttClient::new(&url);
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    cfg.server.host = resolved_host.clone();
    if let Some(port) = port { cfg.server.port = port; }
    cfg.save(&app).ok();
    
    eprintln!("[network] Server set to: {}", url);
    Ok(())
}

/// Start recording: acquire device, create stream, begin capture, show indicator.
/// If streaming mode is on, also connect WebSocket and start forwarding audio chunks.
/// Extracted so both Tauri commands and the hotkey thread can call the same logic.
pub fn start_recording_internal(app: &tauri::AppHandle, state: &AppState) -> Result<(), String> {
    let device;
    let use_streaming;
    {
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        device = cfg.audio.device.clone();
        use_streaming = cfg.audio.use_streaming;
    }
    {
        let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
        recorder.create_stream_channel(4096);
        match recorder.start(device) {
            Ok(()) => {
                let _ = indicator::show(app);

                if use_streaming {
                    // 流式传输：录音同时建立 WS 连接，开始发 chunk
                    let host = state.stt.lock().map_err(|e| e.to_string())?.stt_url.clone();
                    let language = state.config.lock().map_err(|e| e.to_string())?.audio.language.clone();
                    if let Some(chunk_rx) = recorder.take_chunk_receiver() {
                        let (result_tx, result_rx) = tokio::sync::oneshot::channel();
                        *state.active_transcription.lock().map_err(|e| e.to_string())? = Some(ActiveTranscription { result_rx });

                        tauri::async_runtime::spawn(async move {
                            let client = stt::SttClient::new(&host);
                            eprintln!("[timing] Streaming task started (WS connect + real-time chunks)...");
                            let result = client.transcribe_stream(chunk_rx, &language, None).await;
                            eprintln!("[timing] Streaming task finished");
                            let _ = result_tx.send(result);
                        });
                    }
                }

                Ok(())
            }
            Err(e) => {
                recorder.reset();
                Err(e)
            }
        }
    }
}

/// Stop recording: stop audio capture, wait for transcription result.
/// In streaming mode: the WS task already started during recording,
/// just close the channel (stop drops stream → sender drops) and wait.
/// In batch mode: collect audio and transcribe.
/// Extracted so both Tauri commands and the hotkey thread use the same code path.
pub fn stop_recording_internal(app: &tauri::AppHandle, state: &AppState) -> Result<String, String> {
    let stop_start = std::time::Instant::now();

    // 1. Stop recorder (drops audio stream → channel sender drops → WS gets end signal)
    let (fallback_samples, src_rate) = {
        let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
        let (samples, rate) = recorder.stop()?;
        (samples, rate)
    };
    eprintln!("[timing] Audio stopped: {}ms", stop_start.elapsed().as_millis());

    // 2. Take the streaming result receiver (if streaming was active)
    let result_rx = {
        let mut active = state.active_transcription.lock().map_err(|e| e.to_string())?;
        active.take().map(|a| a.result_rx)
    };

    let use_streaming = {
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        cfg.audio.use_streaming
    };

    {
        let mut status = state.indicator_status.lock().map_err(|e| e.to_string())?;
        *status = "识别中...".to_string();
    }

    let (host, language) = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        (stt_client.stt_url.clone(), cfg.audio.language.clone())
    };

    let indicator_status = state.indicator_status.clone();
    let app_handle = app.clone();

    tauri::async_runtime::spawn(async move {
        let total_start = std::time::Instant::now();

        let result = if let Some(rx) = result_rx {
            // ── 真正的边录边发：WS 已在录音期间运行，只需等结果 ──
            eprintln!("[timing] Streaming transcript: waiting for result...");
            match tokio::time::timeout(
                std::time::Duration::from_secs(600),
                rx,
            ).await {
                Ok(Ok(result)) => result,
                Ok(Err(_)) => Err("转录取消 (channel dropped)".to_string()),
                Err(_) => Err("转录超时".to_string()),
            }
        } else if use_streaming {
            // 理论上不会走到这里（start 时设了 active_transcription）
            Err("流式传输未启动 (audio too short?)".to_string())
        } else {
            // ── Batch 模式：收集一次性发 ──
            eprintln!("[timing] Batch transcription...");
            let wav = audio::encode_wav_resampled(&fallback_samples, src_rate);
            if wav.is_empty() {
                Err("No audio captured".to_string())
            } else {
                let pcm = if wav.len() > 44 && &wav[..4] == b"RIFF" {
                    wav[44..].to_vec()
                } else {
                    wav
                };
                let client = stt::SttClient::new(&host);
                let t_ws = std::time::Instant::now();
                let result = client.transcribe_ws(pcm, &language).await;
                eprintln!("[timing] Batch result: {}ms", t_ws.elapsed().as_millis());
                result
            }
        };

        let elapsed_ms = total_start.elapsed().as_millis() as u64;

        match result {
            Ok(text) => {
                eprintln!("[timing] TOTAL client: {}ms, result: {} chars", elapsed_ms, text.len());
                // 先发结果——文字立刻显示到文本框
                let _ = app_handle.emit("transcribe-done", text);
                // 更新胶囊状态——polling 会自动读到并显示
                let result_str = format!("✓ {}ms", elapsed_ms);
                if let Ok(mut status) = indicator_status.lock() { *status = result_str; }
                indicator::show_result(&app_handle, elapsed_ms);
                // 保持 1.5s 让用户看到处理耗时
                tokio::time::sleep(std::time::Duration::from_millis(1500)).await;
                if let Ok(mut status) = indicator_status.lock() { *status = String::new(); }
                let _ = indicator::hide(&app_handle);
            }
            Err(e) => {
                eprintln!("[timing] Error after {}ms: {}", elapsed_ms, e);
                if let Ok(mut status) = indicator_status.lock() { *status = String::new(); }
                let _ = indicator::hide(&app_handle);
                let _ = app_handle.emit("transcribe-error", e);
            }
        }
    });

    Ok(String::new())
}

#[tauri::command]
async fn start_recording(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    start_recording_internal(&app, &state)
}

#[tauri::command]
async fn stop_recording(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<String, String> {
    stop_recording_internal(&app, &state)
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

// ── Diarize commands ──



#[tauri::command]
async fn auto_input(text: String) -> Result<(), String> { input::type_text(&text) }

#[tauri::command]
async fn minimize_to_tray(app: tauri::AppHandle) -> Result<(), String> {
    hotkey::reset_state();
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
                active_transcription: Mutex::new(None),
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
            register_hotkey, get_autostart, set_autostart,
            check_update, install_update,
        ])
        .run(tauri::generate_context!())
        .unwrap_or_else(|e| {
            let msg = format!("Fatal startup error: {:?}", e);
            eprintln!("{}", msg);
            // Write to log file so user can diagnose silent startup crashes (Windows GUI app
            // has no visible console output by default).
            if let Ok(cwd) = std::env::current_dir() {
                let log_path = cwd.join("vif_startup_error.log");
                let _ = std::fs::write(&log_path, &msg);
            }
            // Try to show a message box on Windows so the user sees the error
            #[cfg(target_os = "windows")]
            {
                use std::os::windows::process::CommandExt;
                let _ = std::process::Command::new("mshta.exe")
                    .arg(format!(
                        "javascript:alert('{}');close()",
                        msg.replace('\\', "\\\\").replace('\'', "\\'")
                    ))
                    .creation_flags(0x08000000) // CREATE_NO_WINDOW
                    .spawn();
            }
            std::process::exit(1);
        });
}
