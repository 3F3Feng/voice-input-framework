//! Custom global hotkey listener using rdev.
//! Supports left/right modifier distinction and multi-key chords.
//!
//! Recording lifecycle is handled directly in Rust (not via frontend events)
//! so hotkey operations work even when the webview is minimized/hidden.

use rdev::{listen, Event, Key};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;
use tauri::{Emitter, Manager};

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
                // Release debounce: suppress hotkey-release if emitted within
                // 100ms of hotkey-press. Mouse side buttons generate millisecond-level
                // press/release bounce. Without this, a bounce release would stop and
                // restart recording before the user can say anything.
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

                    // ── Hotkey pressed: combo fully engaged ──
                    // Start recording directly in Rust (bypass frontend) so it works
                    // whether or not the webview is visible. Then emit event for UI.
                    if let rdev::EventType::KeyPress(_) = event.event_type {
                        if all_pressed {
                            last_press_emit = now;
                            // Wrap state access in catch_unwind — AppState is managed in
                            // setup, so this won't normally fail, but this callback runs on
                            // rdev's internal thread and an unchecked panic could abort.
                            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                let state = a.state::<crate::AppState>();
                                let _ = crate::start_recording_internal(&a, &state);
                            }));
                            if r.is_err() {
                                eprintln!("[hotkey] start_recording panic (AppState not ready?)");
                            }
                            let _ = a.emit("hotkey-press", ());
                        }
                        return;
                    }

                    // ── Hotkey released: any key of the combo released ──
                    if !all_pressed {
                        // Release debounce
                        if now.duration_since(last_press_emit).as_millis() < 100 {
                            return;
                        }
                        // Stop recording directly in Rust (webview-agnostic).
                        // Even if the frontend never receives this event, the audio
                        // gets transcribed and results are emitted when available.
                        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            let state = a.state::<crate::AppState>();
                            let _ = crate::stop_recording_internal(&a, &state);
                        }));
                        if r.is_err() {
                            eprintln!("[hotkey] stop_recording panic (AppState not ready?)");
                        }
                        let _ = a.emit("hotkey-release", ());
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
