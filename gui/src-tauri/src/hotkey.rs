use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::GlobalShortcutExt;

pub fn setup(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();

    // Register with callback via string shortcut format
    app.global_shortcut().on_shortcut("Alt+Super+R", move |_app, _shortcut, event| {
        // event is GlobalHotKeyEvent - check if pressed
        // GlobalHotKeyEvent is from the global_hotkey crate
        // event.state() returns HotKeyState::Pressed or HotKeyState::Released
        use tauri_plugin_global_shortcut::ShortcutState;
        if event.state() == ShortcutState::Pressed {
            if let Some(window) = handle.get_webview_window("main") {
                let _ = window.emit("toggle-recording", ());
            }
        }
    })?;

    Ok(())
}
