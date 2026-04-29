//! Floating recording indicator overlay at bottom center of screen.

use tauri::{window::Color, Manager, WebviewWindowBuilder};

const INDICATOR_LABEL: &str = "indicator";

pub fn show(app: &tauri::AppHandle) -> Result<(), String> {
    let _ = hide(app);

    let (sw, sh) = screen_center_bottom(app);
    let x = sw - 110;
    let y = sh - 100;

    let window = WebviewWindowBuilder::new(
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
    .map_err(|e| format!("Indicator failed: {}", e))?;

    // Set webview background to dark to avoid white rectangle on Windows
    let _ = window.set_background_color(Some(Color(20, 22, 36, 217)));

    Ok(())
}

pub fn hide(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.close();
    }
    Ok(())
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
