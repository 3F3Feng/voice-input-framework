//! Floating recording indicator - a small transparent overlay at bottom center of screen.

use tauri::{window::Color, WebviewWindowBuilder};

const INDICATOR_LABEL: &str = "indicator";

pub fn show(app: &tauri::AppHandle) -> Result<(), String> {
    // Close any existing indicator first
    let _ = hide(app);

    // Calculate center-bottom position from the main window's monitor
    let (x, y) = {
        let (sw, sh) = get_screen_center_bottom(app);
        (sw - 110, sh - 100)
    };

    let _window = WebviewWindowBuilder::new(
        app,
        INDICATOR_LABEL,
        tauri::WebviewUrl::App("indicator.html".into()),
    )
    .inner_size(220.0, 60.0)
    .position(x as f64, y as f64)
    .decorations(false)
    .always_on_top(true)
    .skip_taskbar(true)
    .resizable(false)
    .title("")
    .build()
    .map_err(|e| format!("Failed to create indicator: {}", e))?;

    // Make the window background transparent (removes white rectangle)
    _window.set_background_color(Some(Color(0, 0, 0, 0))).ok();

    Ok(())
}

pub fn hide(app: &tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        // Don't error if already closed
        let _ = window.close();
    }
    Ok(())
}

fn get_screen_center_bottom(app: &tauri::AppHandle) -> (i32, i32) {
    use tauri::Manager;
    if let Some(main) = app.get_webview_window("main") {
        if let Ok(Some(mon)) = main.current_monitor() {
            let size = mon.size();
            return ((size.width / 2) as i32, size.height as i32);
        }
    }
    (960, 1400) // fallback for common screen resolutions
}
