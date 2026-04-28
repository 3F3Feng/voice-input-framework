use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::GlobalShortcutExt;

pub fn setup(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();

    // Try multiple shortcut combinations for cross-platform compatibility
    // macOS: Cmd+Option+R, Windows/Linux: Alt+Ctrl+R
    let shortcuts = ["CmdOrCtrl+Option+R", "Alt+Ctrl+R"];
    let mut registered = false;

    for shortcut in &shortcuts {
        match app.global_shortcut().on_shortcut(
            *shortcut,
            move |_app, _shortcut, event| {
                use tauri_plugin_global_shortcut::ShortcutState;
                if event.state() == ShortcutState::Pressed {
                    if let Some(window) = handle.get_webview_window("main") {
                        let _ = window.emit("toggle-recording", ());
                    }
                }
            },
        ) {
            Ok(_) => {
                eprintln!("[hotkey] Registered global shortcut: {}", shortcut);
                registered = true;
                break;
            }
            Err(e) => {
                eprintln!("[hotkey] Failed to register '{}': {}", shortcut, e);
            }
        }
    }

    if !registered {
        eprintln!("[hotkey] Warning: Could not register any global shortcut");
    }

    Ok(())
}
