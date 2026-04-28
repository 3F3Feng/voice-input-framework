use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::GlobalShortcutExt;

#[cfg(target_os = "macos")]
const SHORTCUT: &str = "CmdOrCtrl+Option+R";

#[cfg(not(target_os = "macos"))]
const SHORTCUT: &str = "Alt+Ctrl+R";

pub fn setup(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();

    app.global_shortcut().on_shortcut(
        SHORTCUT,
        move |_app, _shortcut, event| {
            use tauri_plugin_global_shortcut::ShortcutState;
            if event.state() == ShortcutState::Pressed {
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.emit("toggle-recording", ());
                }
            }
        },
    )?;

    Ok(())
}
