mod audio;
mod config;
mod hotkey;
mod indicator;
mod input;
mod log;
mod permissions;
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
    if let Some(port) = port {
        cfg.server.port = port;
    }
    cfg.save(&app).ok();
    Ok(())
}

/// 录音前的麦克风权限闸门。
///
/// - 已授权 / 非 macOS:放行。
/// - 从未询问:触发一次系统弹窗,本次录音失败并提示用户授权后重试
///   (弹窗期间 cpal 拿到的只有静音,直接录会得到一段空音频)。
/// - 已拒绝 / 受限:系统不会再弹窗,提示去「系统设置」手动打开。
fn check_microphone_permission() -> Result<(), String> {
    use permissions::PermissionStatus;
    match permissions::microphone_status() {
        PermissionStatus::Granted => Ok(()),
        PermissionStatus::NotDetermined => {
            permissions::request_microphone();
            Err("正在申请麦克风权限,请在系统弹窗中点击「允许」,然后重新录音。".to_string())
        }
        PermissionStatus::Denied => Err(
            "未获得麦克风权限。请到「系统设置 → 隐私与安全性 → 麦克风」中勾选 Voice Input。"
                .to_string(),
        ),
        PermissionStatus::Restricted => {
            Err("麦克风权限被系统策略限制(如屏幕使用时间 / MDM),无法录音。".to_string())
        }
    }
}

/// Start recording: acquire device, create stream, begin capture, show indicator.
/// Extracted so both Tauri commands and the hotkey thread can call the same logic.
pub fn start_recording_internal(app: &tauri::AppHandle, state: &AppState) -> Result<(), String> {
    // 录音是麦克风权限真正被需要的时刻,在这里拦截。缺权限时 cpal 照样能开流,
    // 但只会送来静音——与其转录一段空音频,不如直接报错说清楚原因。
    check_microphone_permission()?;

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

/// Stop recording: capture samples, hide indicator, spawn transcription.
/// Extracted so both Tauri commands and the hotkey thread use the same code path.
pub fn stop_recording_internal(app: &tauri::AppHandle, state: &AppState) -> Result<String, String> {
    let (chunk_rx, fallback_samples, src_rate) = {
        let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
        let chunk_rx = recorder.take_chunk_receiver();
        let (samples, rate) = recorder.stop()?;
        (chunk_rx, samples, rate)
    };

    let has_window = app.get_webview_window(indicator::INDICATOR_LABEL).is_some();
    eprintln!("[stop] indicator window exists: {}", has_window);

    {
        let mut status = state.indicator_status.lock().map_err(|e| e.to_string())?;
        *status = "识别中...".to_string();
    }

    log_info!(
        "[stop] chunks={}, fallback_samples={}, src_rate={}",
        chunk_rx.is_some(),
        fallback_samples.len(),
        src_rate
    );

    let (host, language) = {
        let stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        (stt_client.stt_url.clone(), cfg.audio.language.clone())
    };

    let indicator_status = state.indicator_status.clone();
    let app_handle = app.clone();

    // Use tauri::async_runtime::spawn to run transcription from any thread.
    // This uses Tauri's internal global tokio runtime handle, so it works
    // even when called from the hotkey listener (a std::thread, not tokio).
    tauri::async_runtime::spawn(async move {
        eprintln!("[transcribe] Background task started, host={}", host);
        let transcribe_start = std::time::Instant::now();
        let result = run_transcription(
            &app_handle,
            &indicator_status,
            &host,
            &language,
            chunk_rx,
            fallback_samples,
            src_rate,
        )
        .await;
        let elapsed_ms = transcribe_start.elapsed().as_millis() as u64;

        match result {
            Ok(text) => {
                eprintln!(
                    "[transcribe] Done: {} chars in {}ms",
                    text.len(),
                    elapsed_ms
                );
                // Show processing time on indicator for 500ms before hiding
                indicator::show_result(&app_handle, elapsed_ms);
                tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                if let Ok(mut status) = indicator_status.lock() {
                    *status = String::new();
                }
                let _ = indicator::hide(&app_handle);
                let _ = app_handle.emit("transcribe-done", text);
            }
            Err(e) => {
                eprintln!("[transcribe] Error: {}", e);
                if let Ok(mut status) = indicator_status.lock() {
                    *status = String::new();
                }
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
async fn stop_recording(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<String, String> {
    stop_recording_internal(&app, &state)
}

async fn run_transcription(
    app_handle: &tauri::AppHandle,
    indicator_status: &std::sync::Arc<Mutex<String>>,
    host: &str,
    language: &str,
    chunk_rx: Option<tokio::sync::mpsc::Receiver<Vec<u8>>>,
    fallback_samples: Vec<f32>,
    src_rate: u32,
) -> Result<String, String> {
    let client = stt::SttClient::new(host);
    eprintln!(
        "[transcribe] Starting transcription, host={}, lang={}",
        host, language
    );

    let (event_tx, mut event_rx) = tokio::sync::mpsc::unbounded_channel::<stt::StreamEvent>();

    let indicator_status_fwd = indicator_status.clone();
    let app_fwd = app_handle.clone();
    let event_forwarder = tokio::spawn(async move {
        while let Some(event) = event_rx.recv().await {
            match &event {
                stt::StreamEvent::LlmStart { .. } | stt::StreamEvent::LlmProgress { .. } => {
                    if let Ok(mut status) = indicator_status_fwd.lock() {
                        *status = "LLM 处理中...".to_string();
                    }
                }
                _ => {}
            }
            let _ = app_fwd.emit("transcribe-progress", &event);
        }
    });

    let result = if let Some(rx) = chunk_rx {
        eprintln!("[transcribe] Using streaming mode");
        client.transcribe_stream(rx, language, Some(event_tx)).await
    } else {
        eprintln!(
            "[transcribe] Using fallback batch mode ({} samples)",
            fallback_samples.len()
        );
        let wav = audio::encode_wav_resampled(&fallback_samples, src_rate);
        if wav.is_empty() {
            return Err("No audio captured".to_string());
        }
        let (tx, rx) = tokio::sync::mpsc::channel(1);
        let pcm = if wav.len() > 44 && &wav[..4] == b"RIFF" {
            wav[44..].to_vec()
        } else {
            wav
        };
        let _ = tx.send(pcm).await;
        drop(tx);
        client.transcribe_stream(rx, language, Some(event_tx)).await
    };

    let _ = event_forwarder.await;
    result
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

#[tauri::command]
async fn get_indicator_status(state: State<'_, AppState>) -> Result<String, String> {
    let status = state.indicator_status.lock().map_err(|e| e.to_string())?;
    Ok(status.clone())
}

// ── Transcription ──

#[tauri::command]
async fn get_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    let client = stt::SttClient::new(&host);
    client.get_stt_models().await
}

#[tauri::command]
async fn switch_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).switch_stt_model(&name).await
}

#[tauri::command]
async fn get_llm_models(state: State<'_, AppState>) -> Result<Vec<stt::ModelInfo>, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).get_llm_models().await
}

#[tauri::command]
async fn switch_llm_model(state: State<'_, AppState>, name: String) -> Result<String, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).switch_llm_model(&name).await
}

// ── Config commands ──

#[tauri::command]
async fn get_config(state: State<'_, AppState>) -> Result<config::VoiceInputConfig, String> {
    Ok(state.config.lock().map_err(|e| e.to_string())?.clone())
}

#[tauri::command]
async fn update_config(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    new_config: config::VoiceInputConfig,
) -> Result<(), String> {
    new_config.save(&app)?;
    *state.config.lock().map_err(|e| e.to_string())? = new_config;
    Ok(())
}

#[tauri::command]
async fn import_old_config(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<config::VoiceInputConfig, String> {
    let cfg = config::VoiceInputConfig::load(&app);
    *state.config.lock().map_err(|e| e.to_string())? = cfg.clone();
    Ok(cfg)
}

// ── LLM prompt commands ──

#[tauri::command]
async fn get_llm_prompt(state: State<'_, AppState>) -> Result<String, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).get_llm_prompt().await
}

#[tauri::command]
async fn save_llm_prompt(state: State<'_, AppState>, text: String) -> Result<(), String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).save_llm_prompt(&text).await
}

#[tauri::command]
async fn get_llm_enabled(state: State<'_, AppState>) -> Result<bool, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).get_llm_enabled().await
}

#[tauri::command]
async fn set_llm_enabled(state: State<'_, AppState>, enabled: bool) -> Result<(), String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).set_llm_enabled(enabled).await
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

// ── Diarize commands ──

#[tauri::command]
async fn auto_input(text: String) -> Result<(), String> {
    input::type_text(&text)
}

// ── Permission commands (macOS TCC) ──

/// 一次性查询三项权限,不弹窗。前端进入设置面板 / 启动时调用。
#[tauri::command]
async fn get_permissions() -> Result<permissions::PermissionReport, String> {
    Ok(permissions::report())
}

/// 申请某项权限并等待结果。
///
/// 仅当该权限「从未询问过」时系统才会弹窗;已拒绝的项系统不再弹窗,前端会改为
/// 引导用户点「打开设置」。最多等 30 秒后返回当前状态,不会无限挂住。
#[tauri::command]
async fn request_permission(
    permission: permissions::Permission,
) -> Result<permissions::PermissionStatus, String> {
    permissions::request(permission);
    Ok(permissions::await_status(permission, std::time::Duration::from_secs(30)).await)
}

/// 打开对应的「系统设置 → 隐私与安全性」子面板。
#[tauri::command]
async fn open_permission_settings(permission: permissions::Permission) -> Result<(), String> {
    permissions::open_settings(permission)
}

#[tauri::command]
async fn minimize_to_tray(app: tauri::AppHandle) -> Result<(), String> {
    hotkey::reset_state();
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
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
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    let lang = language.unwrap_or_else(|| "auto".into());
    stt::SttClient::new(&host)
        .transcribe_ws(audio_data, &lang)
        .await
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
            // macOS:以 accessory(菜单栏应用)身份运行。
            //
            // 这不只是"不要 Dock 图标"的外观选择 —— 它决定了悬浮胶囊能否
            // 出现在别的应用的全屏 Space 上。常规(regular)应用的普通 NSWindow
            // 在 macOS 上无法加入其它应用的全屏 Space:实测即使
            // collectionBehavior 设成 CanJoinAllSpaces|FullScreenAuxiliary(0x101)
            // 且层级提到 25,isOnActiveSpace 在全屏场景下仍然是 false。
            // accessory 应用则不受此限制。
            //
            // 代价:Dock 图标和应用菜单栏消失。本应用由快捷键 + 托盘驱动
            // (ui.use_tray 默认开启),主窗口通过托盘菜单打开,因此代价可接受。
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

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

            // 启动时只查询三项权限并记录,不一次性把三个弹窗全甩给用户。
            // 唯一在启动时主动申请的是「输入监控」——全局快捷键监听器马上就要
            // 用它,没有它 CGEventTap 直接创建失败,快捷键完全不工作。
            // 麦克风在开始录音时申请,辅助功能在第一次自动输入时申请。
            let perms = permissions::report();
            log_info!(
                "[perm] 麦克风={:?} 输入监控={:?} 辅助功能={:?}",
                perms.microphone,
                perms.input_monitoring,
                perms.accessibility
            );
            if perms.input_monitoring == permissions::PermissionStatus::NotDetermined {
                // 只在「从未询问」时弹窗:已拒绝时系统不会再弹,重复调用只会
                // 每次启动都骚扰用户却毫无效果。
                log_info!("[perm] 申请输入监控权限(全局快捷键需要)");
                permissions::request_input_monitoring();
            } else if !perms.input_monitoring.is_granted() {
                log_error!("[perm] 缺少输入监控权限,全局快捷键将不工作;请在设置中授权");
            }

            if let Some(keys) = hotkey::parse_hotkey(&shortcut) {
                hotkey::start_listener(app.handle().clone(), keys);
                eprintln!("[hotkey] Started listener for: {}", shortcut);
            }

            if start_minimized {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
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
            set_server_host,
            start_recording,
            stop_recording,
            get_audio_devices,
            get_audio_level,
            get_indicator_status,
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
            get_permissions,
            request_permission,
            open_permission_settings,
            minimize_to_tray,
            register_hotkey,
            get_autostart,
            set_autostart,
            check_update,
            install_update,
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
