//! Custom global hotkey listener using rdev.
//! Supports left/right modifier distinction and multi-key chords.

use rdev::{listen, Event, Key};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;
use tauri::Emitter;

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
                // Debounce: state lock + time guard
                // Mouse side buttons can generate rapid press/release sequences (bounce)
                // State lock: prevent double-trigger while already recording
                // Time guard: ignore events within 150ms debounce window
                let mut is_active = false;
                let mut last_release_time = Instant::now() - std::time::Duration::from_secs(1);

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

                    // Check if this key is part of our hotkey
                    if !keys.contains(&key) { return; }

                    // Check if ALL hotkey keys are currently pressed
                    let all_pressed = if let Ok(p) = pressed.lock() {
                        keys.iter().all(|k| p.contains(k))
                    } else {
                        false
                    };

                    let now = Instant::now();

                    match event.event_type {
                        rdev::EventType::KeyPress(_) => {
                            // Time debounce: ignore press within 150ms of last release
                            if now.duration_since(last_release_time).as_millis() < 150 { return; }
                            // State lock: ignore if already active
                            if all_pressed && !is_active {
                                is_active = true;
                                let _ = a.emit("hotkey-press", ());
                            }
                        }
                        rdev::EventType::KeyRelease(_) => {
                            // State lock: only release if currently active
                            if !all_pressed && is_active {
                                is_active = false;
                                last_release_time = now;
                                let _ = a.emit("hotkey-release", ());
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
