mod audio;
mod config;
mod heartbeat;
mod history;
mod hotkey;
mod indicator;
mod input;
mod log;
mod permissions;
mod server_manager;
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

/// 值得注意、但不是故障的事(比如回落到默认麦克风)。以前这类提示只能用
/// `log_error!`,日志页里一片红,真正的错误反而不显眼。
#[macro_export]
macro_rules! log_warn {
    ($($arg:tt)*) => {{
        let msg = format!($($arg)*);
        $crate::log::__log_inner("WARN", &msg);
    }};
}

pub struct AppState {
    pub stt: Mutex<stt::SttClient>,
    pub recorder: Mutex<audio::AudioRecorder>,
    pub config: Mutex<config::VoiceInputConfig>,
    pub indicator_status: std::sync::Arc<Mutex<String>>,
    /// 本地 STT / LLM 子进程的管理器。远程模式下它就是个空壳,不做任何事。
    pub servers: std::sync::Arc<Mutex<server_manager::ServerManager>>,
    /// 后台心跳最近一次看到的 STT 服务状态(见 `heartbeat`)。开始录音前看它。
    pub stt_health: Mutex<heartbeat::SttHealth>,
}

#[tauri::command]
async fn set_server_host(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    host: String,
    port: Option<u16>,
) -> Result<(), String> {
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    cfg.server.host = host;
    if let Some(port) = port {
        cfg.server.port = port;
    }
    // 本地管理模式下这个输入框改的是「远程地址」,只存不用——客户端仍然连
    // 本地端口。切回远程模式时 `set_server_mode` 会重新指向它。
    if cfg.server.mode == config::ServerMode::Remote {
        // 地址一律由 `effective_stt_url()` 推导,不在这里再拼一遍:以前这里
        // 自己 `format!("http://{host}:{port}")`,用户填完整 URL 时会拼出
        // `http://1.2.3.4:6544:6544`。
        let url = cfg.server.effective_stt_url();
        let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        *stt_client = stt::SttClient::new(&url);
    }
    cfg.save(&app).ok();
    Ok(())
}

/// 按当前模式解析该连的 STT 地址,并把客户端指过去。返回连的是哪儿。
///
/// 前端需要这个命令是因为「连哪儿」不是前端能算的:本地管理模式下地址来自
/// `server.local.stt_port`,远程模式才是 `host` / `port`,而 `host` 还可能本身
/// 就是一条完整 URL。前端曾经拿远程那对字段自己拼,于是本地模式下服务在
/// 127.0.0.1 上跑着,客户端却一直去敲用户填的远程地址。
///
/// 只重指客户端,不碰配置,也不发请求——通不通由调用方紧接着拉一次模型列表
/// 来判断。
#[tauri::command]
async fn connect_effective_server(state: State<'_, AppState>) -> Result<String, String> {
    let url = {
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        cfg.server.effective_stt_url()
    };
    {
        let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        *stt_client = stt::SttClient::new(&url);
    }
    Ok(url)
}

/// 把主窗口显示出来并前置。
///
/// macOS 上本应用以 accessory(菜单栏应用)身份运行,这类应用**不会自动激活
/// 自己**:窗口即使是 visible 的也只是待在别人后面,而且没有 Dock 图标可点。
/// 所以除了 show/unminimize/set_focus,还要显式 activate 一次 NSApp。
///
/// AppKit 只能在主线程操作,因此整段都通过 run_on_main_thread 转发。
pub(crate) fn show_main_window(app: &tauri::AppHandle) {
    let app = app.clone();
    let _ = app.clone().run_on_main_thread(move || {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }

        // accessory 应用必须自己抢一次激活,否则上面的 set_focus 只是把窗口
        // 排到本应用内部的最前,整个应用仍然不是前台,用户看不到它。
        #[cfg(target_os = "macos")]
        unsafe {
            use objc2::runtime::AnyObject;
            use objc2::{class, msg_send};

            let ns_app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
            if !ns_app.is_null() {
                let _: () = msg_send![ns_app, activateIgnoringOtherApps: true];
            }
        }
    });
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

/// 录音相关、不打断流程但必须让用户知道的事(麦克风回落、录音中断开、
/// 录满 5 分钟自动停止):记进日志,再发给前端弹 toast。
///
/// 这些以前要么只 `eprintln`,要么干脆不说 —— 打包后的应用没有终端,
/// 等于没人看得见。
pub(crate) fn emit_app_warning(app: &tauri::AppHandle, msg: &str) {
    log_warn!("{}", msg);
    let _ = app.emit("app-warning", msg.to_string());
}

/// Start recording: acquire device, create stream, begin capture, show indicator.
/// Extracted so both Tauri commands and the hotkey thread can call the same logic.
pub fn start_recording_internal(app: &tauri::AppHandle, state: &AppState) -> Result<(), String> {
    // 录音是麦克风权限真正被需要的时刻,在这里拦截。缺权限时 cpal 照样能开流,
    // 但只会送来静音——与其转录一段空音频,不如直接报错说清楚原因。
    check_microphone_permission()?;

    // 服务连不上 / 模型还在加载 / 加载失败时不开始录音,直接说原因(R13)。
    // 以前快捷键路径不看这些,说完一整句才报连不上,这段话白说了。
    {
        let url = state
            .stt
            .lock()
            .map(|c| c.stt_url.clone())
            .unwrap_or_default();
        if let Ok(health) = state.stt_health.lock() {
            heartbeat::recording_gate(&health, &url)?;
        }
    }

    let device;
    {
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        device = cfg.audio.device.clone();
    }
    {
        let mut recorder = state.recorder.lock().map_err(|e| e.to_string())?;
        // 已经在录了就原地拒绝,一个字节的状态都别动。
        // 下面那两步都是破坏性的:`create_stream_channel` 会把正在跑的回调手里
        // 那个 sender 换掉(流式分片从此进不来),而 `start()` 认出重复启动返回
        // Err 之后,错误分支的 `reset()` 会连采样缓冲一起清空。
        // 真实场景:快捷键正按着录音,用户又去设置面板点了一下「录音」,
        // 松手时只剩一句 "No audio captured"。
        if recorder.is_recording() {
            return Err("正在录音中,请先结束当前录音。".to_string());
        }
        recorder.create_stream_channel();
        let warn_app = app.clone();
        let on_warning: audio::WarningSink =
            std::sync::Arc::new(move |msg: String| emit_app_warning(&warn_app, &msg));
        match recorder.start(device, on_warning) {
            Ok(note) => {
                if let Some(note) = note {
                    emit_app_warning(app, &note);
                }
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
                if let Ok(mut status) = indicator_status.lock() {
                    *status = String::new();
                }
                indicator::hide_later(&app_handle, std::time::Duration::from_millis(500)).await;
                tray::remember_result(&app_handle, &text);
                let _ = app_handle.emit("transcribe-done", text);
            }
            Err(e) => {
                eprintln!("[transcribe] Error: {}", e);
                if let Ok(mut status) = indicator_status.lock() {
                    *status = String::new();
                }
                // 胶囊先停在失败状态(红点 + 一句原因;静音是灰点「没听到声音」)
                // 再关。以前这里立刻关掉,原因只进主窗口 —— 窗口藏着时用户什么
                // 都看不到。toast 立刻发,不必等胶囊。
                indicator::show_failure(&app_handle, &e);
                let _ = app_handle.emit("transcribe-error", e);
                indicator::hide_later(&app_handle, indicator::FAILURE_LINGER).await;
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
    chunk_rx: Option<tokio::sync::mpsc::UnboundedReceiver<Vec<u8>>>,
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
                // 后处理没做成、退回了原文:结果照常输出,但要让用户知道这次没经过
                // LLM。以前服务端静默吞掉,用户只会觉得「后处理怎么没效果」。
                stt::StreamEvent::FinalResult {
                    llm_error: Some(reason),
                    ..
                } => {
                    log_error!("[transcribe] LLM 后处理失败,已使用原文: {}", reason);
                    let _ = app_fwd.emit("transcribe-warning", reason.clone());
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
        // 以前判的是 `wav.is_empty()`,可 WAV 至少有 44 字节的头,永远不空,
        // 一个采样都没有也会被送去转写。
        if fallback_samples.is_empty() {
            return Err(stt::NO_SPEECH.to_string());
        }
        let wav = audio::encode_wav_resampled(&fallback_samples, src_rate);
        let (tx, rx) = tokio::sync::mpsc::unbounded_channel();
        let pcm = if wav.len() > 44 && &wav[..4] == b"RIFF" {
            wav[44..].to_vec()
        } else {
            wav
        };
        let _ = tx.send(pcm);
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

/// 某个 STT 模型的加载状态。切换是异步的(服务端立即返回、后台加载),
/// 前端轮询它,等真正加载完才说「已切换」,加载失败时把原因带回来。
#[tauri::command]
async fn get_model_status(
    state: State<'_, AppState>,
    name: String,
) -> Result<serde_json::Value, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).get_model_status(&name).await
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

/// 版本号与构建标识。
///
/// **版本号只有一个来源:`gui/src-tauri/Cargo.toml` 的 `package.version`。**
/// `tauri.conf.json` 里不再写 `version`(Tauri 缺省就回落到 Cargo.toml),
/// `gui/package.json` 的 `version` 字段也删掉了 —— 那两处从来没人读,却总
/// 和真版本号对不上,界面上一度显示的还是配置文件的 schema 版本「2.0」。
#[derive(Debug, Clone, serde::Serialize)]
pub struct BuildInfo {
    /// 应用版本,来自 `CARGO_PKG_VERSION`。
    pub version: String,
    /// 每次构建都不同的 UUID,见 `build.rs`。
    pub build_id: String,
    /// 构建时刻,`YYYY-MM-DD HH:MM UTC`。
    pub built_at: String,
}

impl BuildInfo {
    pub fn current() -> Self {
        Self {
            version: env!("CARGO_PKG_VERSION").to_string(),
            build_id: env!("VIF_BUILD_ID").to_string(),
            built_at: env!("VIF_BUILD_TIME").to_string(),
        }
    }
}

#[tauri::command]
async fn get_build_info() -> Result<BuildInfo, String> {
    Ok(BuildInfo::current())
}

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

/// 选一个音频文件转写(F21)。返回 `None` 表示用户取消了选择。
///
/// 服务端一直有 `/transcribe`,界面上却没有入口;要转一段录音只能用仓库里另一个
/// Python 小工具。现在 WAV(任意采样率 / 声道)都能直接转,其它格式服务端会说清楚
/// 要先转成 WAV。
#[tauri::command]
async fn pick_and_transcribe_file(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Option<serde_json::Value>, String> {
    use tauri_plugin_dialog::DialogExt;

    let (tx, rx) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title("选择要转写的音频")
        .add_filter("WAV 音频", &["wav", "wave"])
        .add_filter("所有文件", &["*"])
        .pick_file(move |picked| {
            let _ = tx.send(picked);
        });
    let Some(picked) = rx.await.map_err(|e| e.to_string())? else {
        return Ok(None);
    };
    let path = picked
        .into_path()
        .map_err(|e| format!("读不到选中的文件: {}", e))?;
    let file_name = path
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "audio.wav".into());
    let meta = std::fs::metadata(&path).map_err(|e| format!("读不到 {}: {}", file_name, e))?;
    // 和服务端的上传上限一致(shared/constants.py 的 MAX_UPLOAD_SIZE),免得传半天才被拒。
    const MAX_UPLOAD: u64 = 100 * 1024 * 1024;
    if meta.len() > MAX_UPLOAD {
        return Err(format!("{} 超过 100 MB,请先切成小段再转写", file_name));
    }
    let bytes = tokio::fs::read(&path)
        .await
        .map_err(|e| format!("读不到 {}: {}", file_name, e))?;
    let (host, language) = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        (c.stt_url.clone(), cfg.audio.language.clone())
    };
    let text = stt::SttClient::new(&host)
        .transcribe_file(bytes, &file_name, &language)
        .await?;
    Ok(Some(serde_json::json!({ "file": file_name, "text": text })))
}

#[tauri::command]
async fn get_vocabulary(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).get_vocabulary().await
}

#[tauri::command]
async fn save_vocabulary(
    state: State<'_, AppState>,
    entries: Vec<String>,
) -> Result<serde_json::Value, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).save_vocabulary(&entries).await
}

#[tauri::command]
async fn reset_llm_prompt(state: State<'_, AppState>) -> Result<String, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    stt::SttClient::new(&host).reset_llm_prompt().await
}

/// 读后处理开关。前端每次连上服务都会调一次(`loadModels`)。
///
/// 顺手把读到的权威值写进本地缓存:这是除了启动对账之外,最频繁的一个能让缓存
/// 跟上权威的时机。缓存越新,下次冷启动时「要不要拉 LLM 服务」这个决定就越准,
/// 需要对账去纠正的次数也就越少。
#[tauri::command]
async fn get_llm_enabled(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<bool, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    let enabled = stt::SttClient::new(&host).get_llm_enabled().await?;
    cache_llm_enabled(&app, &state, enabled);
    Ok(enabled)
}

/// 后处理开关的完整状态:开没开、这台服务支不支持、不支持的原因(F17)。
///
/// 前端连上服务时调它,不支持的平台上把开关置灰并把原因写在旁边——以前在
/// Windows / Linux 上照样能拨,拨了之后要等满 30 秒才说「还在加载模型」。
#[tauri::command]
async fn get_llm_status(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<stt::LlmStatus, String> {
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    let status = stt::SttClient::new(&host).get_llm_status().await?;
    cache_llm_enabled(&app, &state, status.enabled);
    Ok(status)
}

/// 打开后处理时,等 LLM 服务加载完模型的上限。
///
/// 超时不代表「失败了」,只代表「还没好」——进程还在跑,模型还在读。取 30 秒是
/// 个取舍:用户说「开起来就几秒」,真等到几十秒说明这台机器或这个模型不对劲,
/// 与其把开关一直锁着不动,不如放开,让他看着「服务器」面板等它变成「运行中」
/// 再拨一次(那时 `start` 会直接采纳,一秒就成)。
const LLM_READY_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

/// 打开后处理时 LLM 超过 `LLM_READY_TIMEOUT` 还没加载完,后台最多再等多久。
/// 首次使用要下载好几 GB,慢网下十分钟不算离谱。
const LLM_LATE_READY_LIMIT: std::time::Duration = std::time::Duration::from_secs(600);

/// 「还在加载」这类返回的固定开头,前端据此显示成提示而不是错误。
pub(crate) const LLM_STILL_LOADING: &str = "LLM 服务还在加载模型";

/// 后处理开关的操作代数,见 `set_llm_enabled`。
static LLM_TOGGLE_GEN: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// 打开后处理超时之后,在后台接着等 LLM 服务。
///
/// - 等到了:替用户把后处理开上,通知前端(`llm-enabled-late`)把开关拨上;
/// - 加载失败 / 等太久:把刚拉起的进程收回去(只收自己拉起的),通知前端原因;
/// - 用户中途又拨了开关:这一次作废,什么都不做。
async fn finish_llm_enable_late(
    app: tauri::AppHandle,
    servers: std::sync::Arc<Mutex<server_manager::ServerManager>>,
    cfg: config::ServerConfig,
    host: String,
    gen: u64,
) {
    let stale = || LLM_TOGGLE_GEN.load(std::sync::atomic::Ordering::SeqCst) != gen;
    let started = std::time::Instant::now();
    let outcome = loop {
        if stale() {
            return;
        }
        match server_manager::wait_ready(
            &cfg,
            server_manager::ServerKind::Llm,
            std::time::Duration::from_secs(5),
        )
        .await
        {
            server_manager::Readiness::Ready => break Ok(()),
            server_manager::Readiness::Failed(reason) => {
                break Err(format!("LLM 模型加载失败:{}", reason))
            }
            server_manager::Readiness::TimedOut if started.elapsed() >= LLM_LATE_READY_LIMIT => {
                break Err(format!(
                    "LLM 服务加载超过 {} 分钟仍未就绪,已停止",
                    LLM_LATE_READY_LIMIT.as_secs() / 60
                ))
            }
            server_manager::Readiness::TimedOut => {}
        }
    };
    if stale() {
        return;
    }
    let outcome = match outcome {
        Ok(()) => stt::SttClient::new(&host).set_llm_enabled(true).await,
        Err(e) => Err(e),
    };
    match outcome {
        Ok(()) => {
            let state = app.state::<AppState>();
            cache_llm_enabled(&app, &state, true);
            log_info!("[llm] LLM 服务加载完成,后处理已自动开启");
            let _ = app.emit(
                "llm-enabled-late",
                serde_json::json!({ "ok": true, "message": "LLM 服务加载好了,后处理已开启" }),
            );
        }
        Err(e) => {
            rollback_llm_start(&servers, &cfg).await;
            log_error!("[llm] {}", e);
            let _ = app.emit(
                "llm-enabled-late",
                serde_json::json!({ "ok": false, "message": e }),
            );
        }
    }
}

/// 自动启动后等 STT 就绪的上限,只用于开关对账。MLX 首次加载模型实测 10–30 秒,
/// 给得宽一点:等不到只是跳过对账,不影响任何别的事。
const STT_READY_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(120);

/// 把后处理开关的状态写进本地配置。
///
/// **权威在 STT 服务的 `/llm/enabled`**(它自己持久化在 `stt_state.json` 里)。
/// 这里存的只是一份缓存,用来回答一个 STT 还没起来时问不到的问题:**启动时要不要
/// 拉起 LLM 服务**。客户端配置里本来就有 `llm.enabled` 这个字段,只是从来没人读过
/// ——现在给了它唯一的、明确的职责,而不是让它继续当第二份说不清谁说了算的真相。
///
/// 缓存会不会和权威走散?会。所以 `auto_start` 在 STT 健康之后必定拿权威对一次账
/// (见 `reconcile_llm_after_start`),不一致时一律改缓存、跟着权威走。
fn cache_llm_enabled(app: &tauri::AppHandle, state: &AppState, enabled: bool) {
    let Ok(mut cfg) = state.config.lock() else {
        log_error!("[llm] 配置锁不可用,开关状态没能写进配置");
        return;
    };
    // 远程模式下这个值说的是**别人那台服务器**的开关,拿它去决定本机启动时拉不拉
    // LLM 服务是张冠李戴。缓存只记本地那台服务说过的话,所以远程模式下一个字都
    // 不写——这也让「远程模式下这个开关的行为和改动前逐字相同」成立。
    if cfg.server.mode != config::ServerMode::Local {
        return;
    }
    if cfg.llm.enabled == enabled {
        return;
    }
    cfg.llm.enabled = enabled;
    if let Err(e) = cfg.save(app) {
        log_error!("[llm] 开关状态没能写进配置: {}", e);
    }
}

/// 开 / 关 LLM 后处理。本地管理模式下顺带管 LLM 服务的启停。
///
/// 两个方向的顺序是**反过来**的,这不是随手写的:
///
/// - 开:先把 LLM 服务拉起来**并等它真的能应答**,再去翻 STT 的标志位。反过来
///   就会留下一个「后处理已开、LLM 端口还是死的」的窗口——那几秒里转录会白等
///   一次反代超时。任何一步失败都直接返回错误,标志位保持原样:绝不能出现
///   界面上写着「已启用」而服务并没有起来的状态。
/// - 关:先关标志位,STT 立刻不会再往 LLM 端口发东西,然后才动进程。
///
/// 远程模式下没有本地进程可管,两个分支都退化成「只翻标志位」,和改动前逐字
/// 相同(见 `local_managed`)。
/// 开启后处理的中途失败时,把本次拉起的 LLM 服务收回去。
///
/// 只收 `ServerOwner::App` 那一档,和 `plan_llm_shutdown` 用的是同一条规则——
/// 端口上那个服务完全可能是 `start` 采纳来的、用户自己在终端里跑的进程。
async fn rollback_llm_start(
    servers: &std::sync::Arc<Mutex<server_manager::ServerManager>>,
    cfg: &config::ServerConfig,
) {
    let status = server_manager::status(servers, cfg, server_manager::ServerKind::Llm).await;
    if server_manager::plan_llm_shutdown(&status) != server_manager::LlmShutdownPlan::Stop {
        return;
    }
    match server_manager::stop(servers, cfg, server_manager::ServerKind::Llm) {
        Ok(msg) => log_info!("[llm] 后处理没能开起来,已把刚拉起的 LLM 服务收回去:{}", msg),
        Err(e) => log_error!("[llm] 后处理没能开起来,收回 LLM 服务也失败了:{}", e),
    }
}

#[tauri::command]
async fn set_llm_enabled(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    enabled: bool,
) -> Result<String, String> {
    let cfg = server_config_snapshot(&state)?;
    let host = {
        let c = state.stt.lock().map_err(|e| e.to_string())?;
        c.stt_url.clone()
    };
    let local_managed = cfg.mode == config::ServerMode::Local;
    let servers = state.servers.clone();
    let mut notes: Vec<String> = Vec::new();
    // 每拨一次开关换一代,让还在后台等「晚到的就绪」的上一次操作知道自己作废了。
    let my_gen = LLM_TOGGLE_GEN.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;

    if enabled {
        // 不支持的平台先拦下来,别去拉起一个注定加载失败的 LLM 进程(F17)。
        // 问不到(老服务端 / 暂时不通)就照旧往下走,由后面的步骤报错。
        if let Ok(st) = stt::SttClient::new(&host).get_llm_status().await {
            if !st.supported {
                return Err(st
                    .reason
                    .unwrap_or_else(|| "这台机器不支持 LLM 后处理".to_string()));
            }
        }
        if local_managed {
            let msg = server_manager::start(&servers, &cfg, server_manager::ServerKind::Llm)
                .await
                .map_err(|e| format!("LLM 服务启动失败,后处理未开启:{}", e))?;
            log_info!("[llm] {}", msg);
            match server_manager::wait_ready(
                &cfg,
                server_manager::ServerKind::Llm,
                LLM_READY_TIMEOUT,
            )
            .await
            {
                server_manager::Readiness::Ready => {}
                // 模型加载失败(非 Apple 平台没有 mlx_lm、下载断了、内存不够)不会自己
                // 好起来。以前这里分不出失败和加载中,要白等满 30 秒才说「还在加载」,
                // 并且留下一个永远加载不完的进程。现在立刻把原因说出来,并把刚拉起的
                // 进程收回去(照样只收自己拉起的)。
                server_manager::Readiness::Failed(reason) => {
                    rollback_llm_start(&servers, &cfg).await;
                    return Err(format!("LLM 模型加载失败,后处理未开启:{}", reason));
                }
                server_manager::Readiness::TimedOut => {
                    // 以前到这里就撂下不管:开关拨回「关」,进程接着加载,加载完了也没人
                    // 去开后处理、也没人停它,几个 G 的模型白占到应用退出。现在后台接着等,
                    // 好了就替用户把后处理开上,失败或等太久就把进程收回去。
                    tauri::async_runtime::spawn(finish_llm_enable_late(
                        app.clone(),
                        servers.clone(),
                        cfg.clone(),
                        host.clone(),
                        my_gen,
                    ));
                    return Err(format!(
                        "{}(已等 {} 秒),加载好后会自动开启后处理。",
                        LLM_STILL_LOADING,
                        LLM_READY_TIMEOUT.as_secs()
                    ));
                }
            }
            notes.push("LLM 服务已就绪".into());
        }
        if let Err(e) = stt::SttClient::new(&host).set_llm_enabled(true).await {
            // 服务已经起来了、模型也加载完了,偏偏最后这一步没成。直接返回错误
            // 会留下一个谁也不会去停的进程:前端会把开关拨回「关」,而「关」那条
            // 分支只在用户主动拨动时才跑——开关看着已经是关的,用户没有理由再碰它,
            // 于是几个 G 的模型就这么占到应用退出为止。
            //
            // 收的时候照样只收自己拉起的:`start` 有可能是采纳了用户在终端里
            // 跑着的那个服务,那不是我们能动的东西。
            if local_managed {
                rollback_llm_start(&servers, &cfg).await;
            }
            return Err(e);
        }
        notes.push("LLM 后处理已启用".into());
    } else {
        stt::SttClient::new(&host).set_llm_enabled(false).await?;
        notes.push("LLM 后处理已禁用".into());
        if local_managed {
            let status =
                server_manager::status(&servers, &cfg, server_manager::ServerKind::Llm).await;
            match server_manager::plan_llm_shutdown(&status) {
                server_manager::LlmShutdownPlan::Stop => {
                    match server_manager::stop(&servers, &cfg, server_manager::ServerKind::Llm) {
                        Ok(msg) => {
                            log_info!("[llm] {}", msg);
                            notes.push(msg);
                        }
                        // 停不掉不该把「后处理已关」这件已经做成的事翻回去:标志位
                        // 关了,后处理就是关的,只是内存还占着。如实说出来即可。
                        Err(e) => notes.push(format!("LLM 服务没能停掉:{}", e)),
                    }
                }
                plan => {
                    if let Some(reason) = plan.keep_reason() {
                        log_info!("[llm] {}", reason);
                        notes.push(reason.into());
                    }
                }
            }
        }
    }

    cache_llm_enabled(&app, &state, enabled);
    Ok(notes.join("；"))
}

/// STT 起来之后,拿服务端的权威标志和启动时用的本地缓存对一次账。
///
/// 启动那一刻 STT 还没起来,`/llm/enabled` 根本问不到,只能先信缓存;缓存要是
/// 过期了(上次是在远程模式下拨的开关、有人直接改了服务端的状态文件、上次退出
/// 时写配置失败……),唯一能发现的时机就是这里。权威说开着就把 LLM 补起来,
/// 权威说关着就把刚拉起的收回去——收的时候照样只收自己拉起的,规则不放松。
async fn reconcile_llm_after_start(
    app: &tauri::AppHandle,
    servers: std::sync::Arc<Mutex<server_manager::ServerManager>>,
    cfg: config::ServerConfig,
    started_llm: bool,
) {
    // 对账只要 STT 的 HTTP 接口能答话:`/llm/enabled` 不依赖 STT 模型。所以模型
    // 加载失败也照样对账,只有等到超时(服务压根没起来)才跳过。
    match server_manager::wait_ready(&cfg, server_manager::ServerKind::Stt, STT_READY_TIMEOUT).await
    {
        server_manager::Readiness::Ready | server_manager::Readiness::Failed(_) => {}
        server_manager::Readiness::TimedOut => {
            log_error!(
                "[llm] STT 服务没能在 {} 秒内就绪,后处理开关的对账跳过",
                STT_READY_TIMEOUT.as_secs()
            );
            return;
        }
    }
    let truth = match stt::SttClient::new(&cfg.effective_stt_url())
        .get_llm_enabled()
        .await
    {
        Ok(v) => v,
        Err(e) => {
            log_error!("[llm] 读不到 STT 的后处理开关,对账跳过: {}", e);
            return;
        }
    };

    match server_manager::plan_llm_reconcile(started_llm, truth) {
        server_manager::LlmReconcile::InSync => {}
        server_manager::LlmReconcile::StartLlm => {
            log_info!("[llm] 服务端的后处理开关是开的,补起 LLM 服务");
            match server_manager::start(&servers, &cfg, server_manager::ServerKind::Llm).await {
                Ok(msg) => log_info!("[server] {}", msg),
                Err(e) => log_error!("[server] 补起 LLM 服务失败: {}", e),
            }
        }
        server_manager::LlmReconcile::StopLlm => {
            log_info!("[llm] 服务端的后处理开关是关的,把刚拉起的 LLM 服务收回去");
            let status =
                server_manager::status(&servers, &cfg, server_manager::ServerKind::Llm).await;
            if server_manager::plan_llm_shutdown(&status) == server_manager::LlmShutdownPlan::Stop {
                match server_manager::stop(&servers, &cfg, server_manager::ServerKind::Llm) {
                    Ok(msg) => log_info!("[server] {}", msg),
                    Err(e) => log_error!("[server] 收回 LLM 服务失败: {}", e),
                }
            }
        }
    }

    // 缓存一律跟着权威走,下次启动就不会再错一遍。
    let state = app.state::<AppState>();
    cache_llm_enabled(app, &state, truth);
}

#[tauri::command]
async fn register_hotkey(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    shortcut: String,
) -> Result<(), String> {
    // 分不分左右由配置说了算。这个开关以前只存不读 —— 前端有、配置文件里有、
    // 迁移代码里也有,就是没有任何地方拿它做过判断。
    let distinguish = state
        .config
        .lock()
        .map(|c| c.hotkey.distinguish_left_right)
        .unwrap_or(true);
    // 失败原因原样交给前端显示。以前只回一句英文「Invalid hotkey format」,
    // 前端再把它吞成「更新失败」——用户不知道是 Cmd 不支持还是自己录错了。
    let keys = hotkey::parse_hotkey_checked(&shortcut, distinguish)?;
    hotkey::start_listener(app.clone(), keys);
    eprintln!("[hotkey] Re-registered: {}", shortcut);
    wait_listener_started().await
}

/// 等新监听器报告起没起来(见 `hotkey::start_status`)。
///
/// 以前注册只管 spawn:macOS 上缺「输入监控」时 CGEventTap 建不起来,快捷键是哑的,
/// 可 `register_hotkey` 照样说成功。一秒内没报上来就当作成功(不拿「不知道」去吓用户)。
async fn wait_listener_started() -> Result<(), String> {
    let started = std::time::Instant::now();
    loop {
        match hotkey::start_status() {
            Some(Err(e)) => return Err(e),
            // Linux 的 rdev 只能先乐观报成功,真失败会在几毫秒内改口,多看一小会儿。
            Some(Ok(())) if !cfg!(target_os = "linux") || started.elapsed().as_millis() >= 300 => {
                return Ok(())
            }
            _ if started.elapsed().as_secs() >= 1 => return Ok(()),
            _ => tokio::time::sleep(std::time::Duration::from_millis(50)).await,
        }
    }
}

/// 启动时建的那个快捷键监听器起没起来。前端启动后问一次,起不来就在主界面上说,
/// 而不是让用户按了快捷键没反应才去日志里找原因。
#[tauri::command]
async fn get_hotkey_status() -> Result<(), String> {
    wait_listener_started().await
}

/// 只校验、不注册。录制一结束前端就拿它问一次,用的是和注册同一个解析器:
/// 录得下来却注册不了的组合当场就能说出原因,而不是等用户点了「应用」才失败。
#[tauri::command]
fn validate_hotkey(shortcut: String) -> Result<(), String> {
    // 分不分左右不影响合法性,随便给一个即可。
    hotkey::parse_hotkey_checked(&shortcut, true).map(|_| ())
}

/// 切换「按住说话 / 按一下开始、再按一下结束」,立即生效并存进配置。
#[tauri::command]
async fn set_hotkey_toggle(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    toggle: bool,
) -> Result<(), String> {
    let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
    cfg.hotkey.toggle = toggle;
    cfg.save(&app)?;
    hotkey::set_toggle_mode(toggle);
    Ok(())
}

/// 设置页开始 / 结束录制新快捷键时调用,录制期间旧快捷键不触发录音。
#[tauri::command]
async fn set_hotkey_suspended(suspended: bool) -> Result<(), String> {
    hotkey::set_suspended(suspended);
    Ok(())
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

/// 把文字输入到「用户正在用的那个窗口」。
///
/// 主窗口上的「⌨️ 输入」按钮以前直接调 `type_text`:可用户点按钮的那一下,
/// 焦点就在本应用自己的窗口上,字全敲给了自己(一个没有输入框的界面),
/// 目标应用里什么都没出现。`hand_back_focus` 为 true 时先把主窗口藏起来、
/// 把前台还给上一个应用,等焦点落稳再输入。
#[tauri::command]
async fn auto_input(
    app: tauri::AppHandle,
    text: String,
    hand_back_focus: Option<bool>,
) -> Result<(), String> {
    if hand_back_focus.unwrap_or(false) {
        if let Some(window) = app.get_webview_window("main") {
            // 托盘没建成时藏起来就再也找不回来了(R20),最小化同样能把焦点让出去。
            if tray::available() {
                let _ = window.hide();
            } else {
                let _ = window.minimize();
            }
        }
        // macOS:只藏窗口不够,本应用仍是前台应用;`hide:` 会让系统把前台
        // 交还给上一个应用。AppKit 只能在主线程调用。
        #[cfg(target_os = "macos")]
        {
            let _ = app.run_on_main_thread(|| unsafe {
                use objc2::runtime::AnyObject;
                use objc2::{class, msg_send};
                let ns_app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
                if !ns_app.is_null() {
                    let nil: *mut AnyObject = std::ptr::null_mut();
                    let _: () = msg_send![ns_app, hide: nil];
                }
            });
        }
        // 前台切换是异步的,马上敲字会落进半路上的窗口。
        tokio::time::sleep(std::time::Duration::from_millis(350)).await;
        let result = deliver_text(&app, &text).await;
        // `hide:` 之后整个应用处于「隐藏」状态,之后悬浮胶囊 orderFront 也出不来。
        // 输完就解除隐藏,但不抢前台(主窗口本身已经藏起来了)。
        #[cfg(target_os = "macos")]
        {
            let _ = app.run_on_main_thread(|| unsafe {
                use objc2::runtime::AnyObject;
                use objc2::{class, msg_send};
                let ns_app: *mut AnyObject = msg_send![class!(NSApplication), sharedApplication];
                if !ns_app.is_null() {
                    let _: () = msg_send![ns_app, unhideWithoutActivation];
                }
            });
        }
        return result;
    }
    deliver_text(&app, &text).await
}

/// 按配置的方式把文字送进当前焦点窗口。
///
/// 粘贴方式是默认的:逐字模拟键盘时,文本里的换行会变成回车键 —— 在聊天软件里
/// 等于把没说完的话提前发出去;长文本逐字敲也慢,还会被激活的输入法截走。
async fn deliver_text(app: &tauri::AppHandle, text: &str) -> Result<(), String> {
    use tauri_plugin_clipboard_manager::ClipboardExt;

    if text.is_empty() {
        return Ok(());
    }
    let method = app
        .state::<AppState>()
        .config
        .lock()
        .map(|c| c.ui.input_method)
        .unwrap_or_default();
    if method == config::InputMethod::Type {
        return input::type_text(text);
    }
    if method == config::InputMethod::Copy {
        return app
            .clipboard()
            .write_text(text.to_string())
            .map_err(|e| format!("写剪贴板失败: {}", e));
    }

    let previous = app.clipboard().read_text().ok();
    app.clipboard()
        .write_text(text.to_string())
        .map_err(|e| format!("写剪贴板失败: {}", e))?;
    // 剪贴板变更在有的平台上是异步生效的,紧接着粘贴可能贴出旧内容。
    tokio::time::sleep(std::time::Duration::from_millis(60)).await;
    let pasted = input::press_paste();
    // 等目标应用把剪贴板读走再还原;还原前确认剪贴板里还是我们写的那段 ——
    // 这几百毫秒里用户自己复制了别的东西,就别把它覆盖掉。
    tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    if let Some(prev) = previous {
        if app.clipboard().read_text().ok().as_deref() == Some(text) {
            let _ = app.clipboard().write_text(prev);
        }
    }
    pasted
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
async fn check_update(app: tauri::AppHandle) -> Result<update::UpdateInfo, String> {
    eprintln!("[update] Checking for updates...");
    update::check(&app).await
}

#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<String, String> {
    eprintln!("[update] Starting install...");
    update::download_and_install(&app).await
}

// ── 本地服务器管理 ──
//
// 所有命令都遵循同一个套路:先把配置 clone 出来(`config` 是 `std::sync::Mutex`,
// guard 不是 Send,跨 await 持有会让 Future 不满足 tauri 的约束),再去做
// 探测 / 拉起这些耗时的事。

/// 从状态里取一份服务器配置快照。
fn server_config_snapshot(state: &AppState) -> Result<config::ServerConfig, String> {
    Ok(state
        .config
        .lock()
        .map_err(|e| e.to_string())?
        .server
        .clone())
}

/// 两个服务的完整状态,前端轮询这一个命令就够。
#[tauri::command]
async fn get_server_report(
    state: State<'_, AppState>,
) -> Result<server_manager::ServerReport, String> {
    let cfg = server_config_snapshot(&state)?;
    let servers = state.servers.clone();
    Ok(server_manager::report(&servers, &cfg).await)
}

/// 启动一个服务。端口上已有健康服务时只会「采纳」,不会重复拉起。
#[tauri::command]
async fn start_server(
    state: State<'_, AppState>,
    kind: server_manager::ServerKind,
) -> Result<String, String> {
    let cfg = server_config_snapshot(&state)?;
    let servers = state.servers.clone();
    let msg = server_manager::start(&servers, &cfg, kind).await?;
    log_info!("[server] {}", msg);
    Ok(msg)
}

/// 停止一个服务。只停本应用拉起 / 认领的,以及校验过确实属于本项目的外部进程;
/// 认不出身份的一律拒绝。
#[tauri::command]
async fn stop_server(
    state: State<'_, AppState>,
    kind: server_manager::ServerKind,
) -> Result<String, String> {
    let cfg = server_config_snapshot(&state)?;
    let servers = state.servers.clone();
    let msg = server_manager::stop(&servers, &cfg, kind)?;
    log_info!("[server] {}", msg);
    Ok(msg)
}

#[tauri::command]
async fn restart_server(
    state: State<'_, AppState>,
    kind: server_manager::ServerKind,
) -> Result<String, String> {
    let cfg = server_config_snapshot(&state)?;
    let servers = state.servers.clone();
    let msg = server_manager::restart(&servers, &cfg, kind).await?;
    log_info!("[server] {}", msg);
    Ok(msg)
}

/// 切换「本地管理 / 远程连接」。
///
/// 切换后必须立刻把 STT 客户端指向新地址,否则 UI 显示的是一套、实际连的是
/// 另一套。注意**不动** `host` 字段:用户在远程模式填的地址要留着。
#[tauri::command]
async fn set_server_mode(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    mode: config::ServerMode,
) -> Result<String, String> {
    let url = {
        let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
        cfg.server.mode = mode;
        let url = cfg.server.effective_stt_url();
        cfg.save(&app)?;
        url
    };
    {
        let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        *stt_client = stt::SttClient::new(&url);
    }
    log_info!("[server] 模式切换为 {:?},连接 {}", mode, url);
    Ok(url)
}

/// 保存本地管理模式的路径 / 端口 / 模型设置。
///
/// 这里做一次存在性校验并把问题原样返回,好过存下去之后在「启动」时才报错。
#[tauri::command]
async fn set_local_server_config(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    local: config::LocalServerConfig,
) -> Result<server_manager::LocalPathReport, String> {
    let url = {
        let mut cfg = state.config.lock().map_err(|e| e.to_string())?;
        cfg.server.local = local;
        let url = cfg.server.effective_stt_url();
        cfg.save(&app)?;
        url
    };
    // 端口可能改了,客户端得跟着走。
    {
        let mut stt_client = state.stt.lock().map_err(|e| e.to_string())?;
        *stt_client = stt::SttClient::new(&url);
    }
    let cfg = server_config_snapshot(&state)?;
    let servers = state.servers.clone();
    Ok(server_manager::report(&servers, &cfg).await.local_paths)
}

/// 心跳最近一次看到的 STT 服务状态。前端启动时先拉一次:`stt-health` 事件只在
/// 状态变化时才发,webview 起来之前发过的那些它收不到。
#[tauri::command]
async fn get_stt_health(state: State<'_, AppState>) -> Result<heartbeat::SttHealth, String> {
    Ok(state.stt_health.lock().map_err(|e| e.to_string())?.clone())
}

/// 自动探测仓库 / 解释器路径。探测不到时 `problem` 里是给用户看的原因。
#[tauri::command]
async fn detect_local_server() -> Result<server_manager::DetectResult, String> {
    Ok(server_manager::detect())
}

// ── 应用外壳:托盘、诊断、退出 ──

/// 前端按连接状态更新托盘里的状态行(已连接 · 模型 / 连接中 / 未连接)。
/// 状态只有前端知道得全(连接重试循环在那边),所以由它来推。
#[tauri::command]
async fn set_tray_status(app: tauri::AppHandle, text: String) -> Result<(), String> {
    tray::set_status(&app, &text);
    Ok(())
}

/// 托盘有没有建成。建不成时前端要告诉用户:关窗只是最小化,退出在「关于」里。
#[tauri::command]
async fn tray_available() -> Result<bool, String> {
    Ok(tray::available())
}

/// 从界面退出。托盘建不成(典型:Linux 缺 AppIndicator)时,托盘菜单里那个
/// 「退出」根本不存在,关窗按钮又只是最小化——没有这个按钮就退不出去。
#[tauri::command]
async fn quit_app(app: tauri::AppHandle) -> Result<(), String> {
    app.exit(0);
    Ok(())
}

/// 「复制诊断信息」的内容:版本、构建、系统、连接方式、日志文件位置和最近的日志。
/// 用户报问题时一键粘过来,不用我们再一条条问「什么版本、什么系统」。
#[tauri::command]
async fn get_diagnostics(state: State<'_, AppState>) -> Result<String, String> {
    let build = BuildInfo::current();
    let (mode, stt_url) = {
        let cfg = state.config.lock().map_err(|e| e.to_string())?;
        (cfg.server.mode, cfg.server.effective_stt_url())
    };
    let snap = log::snapshot();
    let skip = snap.lines.len().saturating_sub(200);
    let tail: Vec<&str> = snap.lines[skip..].iter().map(|l| l.text.as_str()).collect();
    Ok(format!(
        "Voice Input v{} · build {} · {}\n系统: {} {}\n连接: {:?} {}\n托盘: {}\n日志文件: {}\n\n── 最近 {} 行客户端日志 ──\n{}\n",
        build.version,
        build.build_id,
        build.built_at,
        std::env::consts::OS,
        std::env::consts::ARCH,
        mode,
        stt_url,
        if tray::available() { "正常" } else { "不可用" },
        snap.file.as_deref().unwrap_or("(未创建)"),
        tail.len(),
        tail.join("\n")
    ))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // 单实例必须第一个注册(插件文档的要求:它得赶在别的插件做任何初始化之前
        // 把第二个进程拦下)。双开的后果是两套全局快捷键监听,按一次录两遍、
        // 输入两遍;第二个实例现在只负责把第一个的窗口叫出来,然后自己退出。
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            log_info!("[app] 应用被再次启动,已把现有窗口调出来(第二个实例直接退出)");
            show_main_window(app);
        }))
        // 更新器插件以前只在 Cargo.toml 里挂着,从没注册过:update.rs 自己用
        // reqwest 下载再启动安装器,`latest.json` 里的签名一眼都没看。注册它之后
        // 端点和公钥统一由 tauri.conf.json 的 plugins.updater 提供,签名验不过
        // 就装不上。
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
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
            // 代价:Dock 图标和应用菜单栏消失。本应用由快捷键 + 托盘驱动,
            // 主窗口通过托盘菜单打开,因此代价可接受。托盘建不成时的退路见下面
            // `tray::setup` 那一段。
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory);

            // 日志最先初始化:下面读配置时的「解析失败已备份」要能落进日志文件。
            log::init(app.handle());

            // 必须在 `load` 之前问:找不到配置时 `load` 会立刻写一份默认的出来。
            let fresh_install = config::VoiceInputConfig::is_fresh_install(app.handle());
            let mut cfg = config::VoiceInputConfig::load(app.handle());

            // 首次运行:猜一次仓库 / 解释器位置并存进配置。猜不到就留空——
            // UI 会明确说「没探测到,请手动填」,而不是在启动时静默失败。
            if cfg.server.local.repo_path.is_none() {
                let detected = server_manager::detect();
                if detected.repo_path.is_some() {
                    log_info!(
                        "[server] 自动探测到仓库 {:?},解释器 {:?}",
                        detected.repo_path,
                        detected.python_path
                    );
                    cfg.server.local.repo_path = detected.repo_path;
                    cfg.server.local.python_path = detected.python_path;
                    if cfg.server.apply_first_run_defaults(fresh_install) {
                        log_info!(
                            "[server] 首次启动且仓库就在本机:默认本地管理{}",
                            if cfg.server.local.auto_start { "并随应用启动服务" } else { "(还没有解释器,先不自动启动)" }
                        );
                    }
                    let _ = cfg.save(app.handle());
                } else if let Some(problem) = detected.problem {
                    log_info!("[server] {}", problem);
                }
            }

            // 客户端连哪儿由模式决定:远程连 host,本地连 127.0.0.1:stt_port。
            // 老配置没有 mode 字段 → 默认 Remote → 和以前完全一样。
            let stt_url = cfg.server.effective_stt_url();
            let shortcut = cfg.hotkey.key.clone();
            let distinguish_sides = cfg.hotkey.distinguish_left_right;
            hotkey::set_toggle_mode(cfg.hotkey.toggle);
            let start_minimized = cfg.ui.start_minimized;
            let local_mode = cfg.server.mode == config::ServerMode::Local;
            let auto_start = local_mode && cfg.server.local.auto_start;

            // 子进程日志和 pid 记账放在应用数据目录里,和 config.json 同级。
            // 兜底路径的主目录也要认 Windows 的 USERPROFILE,以前只读 HOME。
            let data_dir = app.path().app_data_dir().unwrap_or_else(|_| {
                server_manager::home_dir()
                    .unwrap_or_else(|| ".".into())
                    .join(".config/voice-input")
            });
            let mut manager = server_manager::ServerManager::new(data_dir);
            // 上次会话如果是被强杀的,子进程还活着;校验后认领回来,
            // 这样用户还能从 UI 里停掉它们。
            let reclaimed = manager.reclaim_orphans();
            if !reclaimed.is_empty() {
                log_info!("[server] 认领上次遗留的服务进程: {}", reclaimed.join("、"));
            }

            app.manage(AppState {
                stt: Mutex::new(stt::SttClient::new(&stt_url)),
                recorder: Mutex::new(audio::AudioRecorder::new()),
                config: Mutex::new(cfg),
                indicator_status: std::sync::Arc::new(Mutex::new(String::new())),
                servers: std::sync::Arc::new(Mutex::new(manager)),
                stt_health: Mutex::new(heartbeat::SttHealth::unknown(&stt_url)),
            });

            // 连接状态不再只在设置面板开着时才更新(R12):后台每 5 秒看一次服务。
            heartbeat::spawn(app.handle().clone());

            let build = BuildInfo::current();
            log_info!(
                "[app] Voice Input v{} · build {} · {}",
                build.version,
                build.build_id,
                build.built_at
            );

            // 托盘建不成(Linux GNOME 缺 AppIndicator 等)以前被 `let _` 吞掉:
            // 再加上「启动时最小化」和「关窗只是隐藏」,应用就既看不见也退不出。
            if let Err(e) = tray::setup(app) {
                log_error!(
                    "[tray] 系统托盘创建失败({}):主窗口不会被隐藏,关闭按钮改为最小化;退出请用「设置 → 关于 → 退出应用」",
                    e
                );
            }
            // 托盘「复制最近一条」接上持久化的历史:重启后不用等说完第一句才能用。
            history::seed_tray(app.handle());

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

            if let Some(keys) = hotkey::parse_hotkey(&shortcut, distinguish_sides) {
                hotkey::start_listener(app.handle().clone(), keys);
                eprintln!("[hotkey] Started listener for: {}", shortcut);
            } else {
                log_error!("[hotkey] 快捷键「{}」无法解析,全局快捷键未注册", shortcut);
            }

            // 本地模式 + 用户勾了「随应用启动」才自动拉起。`start` 内部照样
            // 先探测:用户已经在终端跑着的服务会被采纳,不会被重复拉起。
            //
            // 拉不拉 LLM 取决于后处理开关。这里有个绕不开的先后问题:开关的权威
            // 在 STT 服务上,而此刻 STT 还没起来,问不到。所以启动这一步只能信
            // 本地缓存(`cfg.llm.enabled`),然后在 STT 健康之后立刻拿权威对一次账
            // (`reconcile_llm_after_start`)。反过来「先起 STT、等它好了再决定要不要
            // 起 LLM」也能跑通,但那样 LLM 只能排在 STT 后面,又回到了「STT 的反代
            // 指着一个还没起来的端口」——正是要避免的那种窗口。
            if auto_start {
                let state = app.state::<AppState>();
                let servers = state.servers.clone();
                let snapshot = state
                    .config
                    .lock()
                    .ok()
                    .map(|c| (c.server.clone(), c.llm.enabled));
                let app_handle = app.handle().clone();
                if let Some((server_cfg, llm_wanted)) = snapshot {
                    tauri::async_runtime::spawn(async move {
                        if !llm_wanted {
                            log_info!("[server] LLM 后处理是关的,本次不启动 LLM 服务");
                        }
                        for kind in server_manager::auto_start_plan(llm_wanted) {
                            match server_manager::start(&servers, &server_cfg, kind).await {
                                Ok(msg) => log_info!("[server] 自动启动: {}", msg),
                                Err(e) => {
                                    log_error!("[server] 自动启动 {} 失败: {}", kind.label(), e)
                                }
                            }
                        }
                        reconcile_llm_after_start(&app_handle, servers, server_cfg, llm_wanted)
                            .await;
                    });
                }
            }

            if start_minimized && tray::available() {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            } else {
                // accessory 应用启动时不会自动激活自己:窗口建出来了、也是 visible,
                // 但从不前置,而且没有 Dock 图标可点 —— 用户看到的就是「明明没勾
                // 「启动时最小化」,程序却自己最小化了」。这里显式前置一次。
                show_main_window(app.handle());
            }

            // 标题栏上的关闭按钮只藏窗口,不退应用;退出走托盘菜单里的「退出」。
            // 托盘没建成时藏起来就再也找不回来了,所以改成最小化(任务栏里还在)。
            //
            // `prevent_close()` 这一句是关键:少了它,下面 `hide()` 藏起来的窗口
            // 紧接着还是会被真正关掉,而主窗口一关整个应用就跟着退了——用户点个
            // ✕ 想收起界面,结果连全局快捷键一起没了。以前就是这个样子。
            if let Some(window) = app.get_webview_window("main") {
                let win = window.clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        hotkey::reset_state();
                        if tray::available() {
                            let _ = win.hide();
                        } else {
                            let _ = win.minimize();
                        }
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            set_server_host,
            connect_effective_server,
            start_recording,
            stop_recording,
            get_audio_devices,
            get_audio_level,
            get_indicator_status,
            get_models,
            switch_model,
            get_model_status,
            get_llm_models,
            get_build_info,
            switch_llm_model,
            get_config,
            update_config,
            get_llm_prompt,
            save_llm_prompt,
            reset_llm_prompt,
            get_vocabulary,
            pick_and_transcribe_file,
            save_vocabulary,
            get_llm_enabled,
            get_llm_status,
            set_llm_enabled,
            auto_input,
            get_permissions,
            request_permission,
            open_permission_settings,
            register_hotkey,
            get_hotkey_status,
            set_hotkey_suspended,
            set_hotkey_toggle,
            validate_hotkey,
            get_autostart,
            set_autostart,
            check_update,
            install_update,
            get_server_report,
            start_server,
            stop_server,
            restart_server,
            set_server_mode,
            set_local_server_config,
            detect_local_server,
            log::get_gui_logs,
            log::open_log_dir,
            get_diagnostics,
            set_tray_status,
            tray_available,
            quit_app,
            get_stt_health,
            history::history_list,
            history::history_add,
            history::history_delete,
            history::history_clear,
        ])
        .build(tauri::generate_context!())
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
        })
        // 从 `.run(context)` 改成 `.build(context).run(callback)`,只为了能拿到
        // `RunEvent::Exit`:应用退出时必须把自己拉起的 Python 子进程带走,
        // 否则它们会继续占着 6544/6545,下次启动只能当成「外部进程」。
        //
        // 覆盖得到的退出路径:托盘「退出」(`app.exit(0)`)、Cmd+Q、
        // 系统注销。**覆盖不到**的是 SIGKILL / 强制退出 / 崩溃——那时谁的代码
        // 都不会跑,子进程会被 launchd 收养并继续运行。这种情况由启动时的
        // `reclaim_orphans` 兜底:核对 pid 与命令行后认领回来,用户仍然能从
        // UI 里停掉;万一认领不成(比如 pid 已被复用),`start` 的健康探测
        // 也会把它当成外部进程直接采纳,绝不会重复拉起。
        .run(|app_handle, event| match event {
            // macOS:本应用是没有 Dock 图标的 accessory 应用。窗口藏着时从启动台 /
            // Finder 再点一次,系统不会起第二个进程,而是给正在跑的这个发「reopen」;
            // 以前没人接,用户看到的是点了没反应。
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { .. } => show_main_window(app_handle),
            // 装完更新重启时不停本地服务:新进程会按 pid 记账认领回来(见 update.rs),
            // 省得把几个 G 的模型重新加载一遍。只在 unix 上这么做 —— Windows 不认领
            // 遗留进程(R33),留下来就成了管不着的外部进程;而且 Windows 的更新由
            // NSIS 安装器负责重启,本来也走不到这里。
            tauri::RunEvent::Exit
                if cfg!(unix)
                    && update::RESTARTING_FOR_UPDATE.load(std::sync::atomic::Ordering::SeqCst) => {}
            tauri::RunEvent::Exit => {
                // 先把 Arc 克隆出来:`State` 借的是 `app_handle`,而 guard 的
                // 析构要排在 `state` 之后,直接锁会活不过这个块。
                // 先把 Arc 克隆出来(`State` 借的是 `app_handle`),再把锁的结果
                // 单独绑一个变量——`if let` 里的临时值要活到块尾,会比 `servers`
                // 本身还晚析构。
                let servers = app_handle.state::<AppState>().servers.clone();
                let locked = servers.lock();
                if let Ok(mut manager) = locked {
                    manager.shutdown_all();
                }
            }
            _ => {}
        });
}
