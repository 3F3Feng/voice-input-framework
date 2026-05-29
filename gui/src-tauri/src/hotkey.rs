//! Custom global hotkey listener using rdev.
//! Supports left/right modifier distinction and multi-key chords.

use rdev::{listen, Event, Key};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;
use tauri::{Emitter, Manager};

use crate::AppState;

static PRESSED_KEYS: OnceLock<Arc<Mutex<Vec<Key>>>> = OnceLock::new();

/// Reset hotkey listener state. Call when window is restored from tray
/// to prevent stuck keys after minimize/restore.
pub fn reset_state() {
    if let Some(keys) = PRESSED_KEYS.get() {
        if let Ok(mut k) = keys.lock() { k.clear(); }
    }
}

/// Parse a hotkey string like "left_ctrl+left_alt" or "capslock" into key list.
pub fn parse_hotkey(s: &str) -> Option<Vec<Key>> {
    let tokens: Vec<&str> = s.split('+').collect();
    if tokens.is_empty() { return None; }
    let keys: Vec<Key> = tokens.iter().filter_map(|t| parse_key(t.trim())).collect();
    if keys.len() == tokens.len() { Some(keys) } else { None }
}

fn parse_key(token: &str) -> Option<Key> {
    let t = token.to_lowercase();
    match t.as_str() {
        "left_ctrl" | "left_control" | "lctrl" => Some(Key::ControlLeft),
        "right_ctrl" | "right_control" | "rctrl" => Some(Key::ControlRight),
        "left_alt" | "lalt" => Some(Key::Alt),
        "right_alt" | "ralt" => Some(Key::AltGr),
        "left_shift" | "lshift" => Some(Key::ShiftLeft),
        "right_shift" | "rshift" => Some(Key::ShiftRight),
        "ctrl" | "control" => Some(Key::ControlLeft),
        "alt" => Some(Key::Alt),
        "shift" => Some(Key::ShiftLeft),
        "capslock" | "caps" => Some(Key::CapsLock),
        "space" => Some(Key::Space),
        "enter" | "return" => Some(Key::Return),
        "tab" => Some(Key::Tab),
        "escape" | "esc" => Some(Key::Escape),
        "delete" | "del" => Some(Key::Delete),
        "backspace" => Some(Key::Backspace),
        _ if t.starts_with('f') && t.len() <= 3 => {
            let n: u8 = t[1..].parse().ok()?;
            if (1..=12).contains(&n) { Some(Key::F1) } else { None }
        }
        _ if t.len() == 1 => {
            let c = t.chars().next()?;
            let keys = [
                Key::KeyA, Key::KeyB, Key::KeyC, Key::KeyD, Key::KeyE, Key::KeyF,
                Key::KeyG, Key::KeyH, Key::KeyI, Key::KeyJ, Key::KeyK, Key::KeyL,
                Key::KeyM, Key::KeyN, Key::KeyO, Key::KeyP, Key::KeyQ, Key::KeyR,
                Key::KeyS, Key::KeyT, Key::KeyU, Key::KeyV, Key::KeyW, Key::KeyX,
                Key::KeyY, Key::KeyZ,
            ];
            let idx = (c as u8).wrapping_sub(b'a') as usize;
            keys.get(idx).copied()
        }
        _ => None,
    }
}

/// Start background hotkey listener using rdev.
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<Key>) {
    let _ = std::thread::Builder::new()
        .name("hotkey-listener".into())
        .spawn(move || {
            if hotkey_keys.is_empty() { return; }

            let pressed = Arc::new(Mutex::new(Vec::<Key>::new()));
            let _ = PRESSED_KEYS.set(pressed.clone());
            let a = app.clone();
            let keys = hotkey_keys.clone();

            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                // Release debounce (only): suppress hotkey-release if emitted within
                // 100ms of hotkey-press. Mouse side buttons generate millisecond-level
                // press/release bounce. Without this, a bounce release would stop and
                // restart recording before the user can say anything.
                // Press events always pass through (frontend has its own state lock).
                let mut last_press_emit = Instant::now() - std::time::Duration::from_secs(1);

                let _ = listen(move |event: Event| {
                    let key = match event.event_type {
                        rdev::EventType::KeyPress(k) => {
                            if let Ok(mut p) = pressed.lock() {
                                if !p.contains(&k) { p.push(k); }
                            }
                            Some(k)
                        }
                        rdev::EventType::KeyRelease(k) => {
                            if let Ok(mut p) = pressed.lock() {
                                p.retain(|&x| x != k);
                            }
                            Some(k)
                        }
                        _ => None,
                    };

                    let Some(key) = key else { return };

                    if !keys.contains(&key) { return; }

                    let all_pressed = if let Ok(p) = pressed.lock() {
                        keys.iter().all(|k| p.contains(k))
                    } else {
                        false
                    };

                    let now = Instant::now();

                    match event.event_type {
                        rdev::EventType::KeyPress(_) => {
                            if all_pressed {
                                last_press_emit = now;
                                let _ = a.emit("hotkey-press", ());
                            }
                        }
                        rdev::EventType::KeyRelease(_) => {
                            if !all_pressed {
                                // Release debounce: ignore release within 100ms of last press emit
                                // This catches mouse button bounce without affecting long recordings
                                if now.duration_since(last_press_emit).as_millis() < 100 {
                                    return;
                                }
                                let _ = a.emit("hotkey-release", ());

                                // Directly stop recorder from Rust (handles minimized webview
                                // where Tauri events may not be processed by the frontend).
                                // reset() is idempotent — safe to call even when not recording.
                                {
                                    let state = a.state::<AppState>();
                                    if let Ok(mut recorder) = state.recorder.lock() {
                                        eprintln!("[hotkey] Force-resetting recorder (minimized webview fallback)");
                                        recorder.reset();
                                        let _ = a.emit("recording-reset", ());
                                    }
                                }
                            }
                        }
                        _ => {}
                    }
                });
            }));

            if let Err(e) = result {
                let msg = if let Some(s) = e.downcast_ref::<&str>() { s }
                    else if let Some(s) = e.downcast_ref::<String>() { s.as_str() }
                    else { "unknown error" };
                eprintln!("[hotkey] rdev listener failed: {}", msg);
            }
        });
}

/// Match a single key against the hotkey combo, respecting left/right modifiers.
fn key_match(hotkey: &[Key], event_key: &Key) -> bool {
    hotkey.contains(event_key)
}
