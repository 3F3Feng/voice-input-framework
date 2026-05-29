//! Floating recording indicator overlay at bottom center of screen.

use tauri::{window::Color, Emitter, Manager, WebviewWindowBuilder};

pub const INDICATOR_LABEL: &str = "indicator";

pub fn show(app: &tauri::AppHandle) -> Result<(), String> {
    let _ = hide(app);
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

    #[cfg(any(not(target_os = "macos"), feature = "macos-private-api"))]
    let window = builder.transparent(true).build().map_err(|e| format!("Indicator failed: {}", e))?;
    #[cfg(not(any(not(target_os = "macos"), feature = "macos-private-api")))]
    let window = builder.build().map_err(|e| format!("Indicator failed: {}", e))?;

    #[cfg(any(not(target_os = "macos"), feature = "macos-private-api"))]
    { let _ = window.set_background_color(Some(Color(0, 0, 0, 0))); }
    #[cfg(not(any(not(target_os = "macos"), feature = "macos-private-api")))]
    { let _ = window.set_background_color(Some(Color(15, 15, 25, 255))); }

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
