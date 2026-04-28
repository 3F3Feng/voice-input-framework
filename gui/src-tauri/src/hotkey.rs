use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::GlobalShortcutExt;

pub fn setup(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();

    // Register CmdOrCtrl+Option+R as global shortcut
    app.global_shortcut().on_shortcut(
        "CmdOrCtrl+Option+R",
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
