use std::sync::Mutex;
use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::GlobalShortcutExt;

static CURRENT_SHORTCUT: Mutex<Option<String>> = Mutex::new(None);

/// Register a shortcut and set up the handler.
/// Should be called once during app setup.
pub fn setup(app: &tauri::App, shortcut: &str) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();
    let s = shortcut.to_string();

    app.global_shortcut().on_shortcut(
        s.as_str(),
        move |_app, _sc, event| {
            use tauri_plugin_global_shortcut::ShortcutState;
            if event.state() == ShortcutState::Pressed {
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.emit("toggle-recording", ());
                }
            }
        },
    )?;

    *CURRENT_SHORTCUT.lock().unwrap() = Some(s);
    Ok(())
}

/// Update the hotkey at runtime.
/// Unregisters the old one, registers the new one.
pub fn update(app: &tauri::AppHandle, new_shortcut: &str) -> Result<(), String> {
    let old = CURRENT_SHORTCUT.lock().map_err(|e| e.to_string())?.take();
    
    // Unregister old
    if let Some(ref old_sc) = old {
        let _ = app.global_shortcut().unregister(old_sc.as_str());
    }
    
    // Register new with handler
    let handle = app.clone();
    let sc = new_shortcut.to_string();
    app.global_shortcut().on_shortcut(
        sc.as_str(),
        move |_app, _sc, event| {
            use tauri_plugin_global_shortcut::ShortcutState;
            if event.state() == ShortcutState::Pressed {
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.emit("toggle-recording", ());
                }
            }
        },
    )
    .map_err(|e| format!("Hotkey register failed: {}", e))?;
    
    *CURRENT_SHORTCUT.lock().map_err(|e| e.to_string())? = Some(sc);
    Ok(())
}

/// Get the default shortcut for the current platform
pub fn default_shortcut() -> &'static str {
    #[cfg(target_os = "macos")]
    { "CmdOrCtrl+Option+R" }
    #[cfg(not(target_os = "macos"))]
    { "Alt+Ctrl+R" }
}
