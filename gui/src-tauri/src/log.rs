//! In-app logging — forwards log messages to both stderr and the Tauri GUI.
//!
//! Stores an AppHandle globally. Each log call emits a "gui-log" event directly.
//! No spawned tasks — works even during setup.

use std::sync::OnceLock;
use tauri::Emitter;

static APP_HANDLE: OnceLock<tauri::AppHandle> = OnceLock::new();

/// Initialize the logger. Call once during app setup.
pub fn init(app_handle: &tauri::AppHandle) {
    let _ = APP_HANDLE.set(app_handle.clone());
}

#[doc(hidden)]
pub fn __log_inner(level: &str, msg: &str) {
    let formatted = format!("[{}] {}", level, msg);
    eprintln!("{}", formatted);
    if let Some(app) = APP_HANDLE.get() {
        let _ = app.emit("gui-log", &formatted);
    }
}
