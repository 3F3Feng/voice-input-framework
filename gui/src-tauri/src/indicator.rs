//! Floating recording indicator - a small transparent overlay window.

use tauri::{Emitter, WebviewWindowBuilder};
use tauri::Manager;

const INDICATOR_LABEL: &str = "indicator";

pub fn show(app: &tauri::AppHandle, pos: Option<(i32, i32)>) -> Result<(), String> {
    // Close existing indicator if any
    let _ = hide(app);

    let (x, y) = pos.unwrap_or((400, 300));

    let window = WebviewWindowBuilder::new(
        app,
        INDICATOR_LABEL,
        tauri::WebviewUrl::App("indicator.html".into()),
    )
    .inner_size(220.0, 60.0)
    .position(x as f64 - 110.0, y as f64 - 30.0)
    .decorations(false)
    .always_on_top(true)
    .skip_taskbar(true)
    .resizable(false)
    .title("")
    .build()
    .map_err(|e| format!("Failed to create indicator: {}", e))?;

    // Focus main window back after creating indicator
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.set_focus();
    }

    Ok(())
}

pub fn hide(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        window.close().map_err(|e| format!("Failed to close indicator: {}", e))?;
    }
    Ok(())
}
