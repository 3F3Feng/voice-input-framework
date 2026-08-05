//! Floating recording indicator overlay at bottom center of screen.

use tauri::{window::Color, Emitter, Manager, WebviewWindowBuilder};

pub const INDICATOR_LABEL: &str = "indicator";

pub fn show(app: &tauri::AppHandle) -> Result<(), String> {
    // 复用已存在的窗口:close 是异步的,close 后立刻同 label 重建会
    // "window already exists" 静默失败 → 胶囊不显示。复用 + 重置页面。
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.show();
        let _ = window.set_focus();
        let _ = window.emit("indicator-reset", ());
        return Ok(());
    }
    let (sw, sh) = screen_center_bottom(app);
    let x = sw - 110;
    let y = sh - 100;

    let builder = WebviewWindowBuilder::new(app, INDICATOR_LABEL, tauri::WebviewUrl::App("indicator.html".into()))
        .inner_size(210.0, 44.0)
        .position(x as f64, y as f64)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .shadow(false)
        .title("");

    // macOS 用不透明深色窗口:透明窗口(Tauri 2 + WKWebView)内容经常
    // 不渲染(已知问题),导致全透明空窗口完全不可见。胶囊 HTML 的背景
    // 色与窗口背景色一致(rgb(15,15,25)),视觉上等同圆角胶囊。
    #[cfg(target_os = "macos")]
    let window = builder.build().map_err(|e| format!("Indicator failed: {}", e))?;
    #[cfg(not(target_os = "macos"))]
    let window = builder.transparent(true).build().map_err(|e| format!("Indicator failed: {}", e))?;

    #[cfg(target_os = "macos")]
    { let _ = window.set_background_color(Some(Color(15, 15, 25, 255))); }
    #[cfg(not(target_os = "macos"))]
    { let _ = window.set_background_color(Some(Color(0, 0, 0, 0))); }

    let _ = window.show();
    let _ = window.set_focus();
    eprintln!(
        "[indicator] built: screen=({},{}) pos=({},{}), url={:?}",
        sw, sh, x, y, tauri::WebviewUrl::App("indicator.html".into())
    );

    // 诊断:1s 后读窗口标题 + 实际 URL,判断 indicator.html 是否成功加载
    // (页面加载后会把标题改成 "indicator-loaded")
    let probe = window.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
        match probe.title() {
            Ok(t) => eprintln!("[indicator] title after 1s: {:?}", t),
            Err(e) => eprintln!("[indicator] title probe failed: {:?}", e),
        }
        match probe.url() {
            Ok(u) => eprintln!("[indicator] url: {}", u),
            Err(e) => eprintln!("[indicator] url probe failed: {:?}", e),
        }
    });

    Ok(())
}

pub fn hide(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) { let _ = window.close(); }
    Ok(())
}

pub fn update_timer(app: &tauri::AppHandle, timer: &str) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit("update-timer", serde_json::json!({"timer": timer}));
    }
}

pub fn update_level(app: &tauri::AppHandle, level: f32) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit("update-level", serde_json::json!({"level": level}));
    }
}

pub fn update_status(app: &tauri::AppHandle, status: &str) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit("update-status", serde_json::json!({"status": status}));
    }
}

/// Show processing result time on indicator (e.g., "1234ms").
/// The indicator JS will display it for a short duration before hiding.
pub fn show_result(app: &tauri::AppHandle, duration_ms: u64) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit("indicator-result", serde_json::json!({
            "duration_ms": duration_ms,
        }));
    }
}

fn screen_center_bottom(app: &tauri::AppHandle) -> (i32, i32) {
    if let Some(main) = app.get_webview_window("main") {
        if let Ok(Some(mon)) = main.current_monitor() {
            let size = mon.size();
            return (size.width as i32 / 2, size.height as i32);
        }
    }
    (960, 1400)
}
