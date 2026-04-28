//! Custom global hotkey listener using rdev.
//! Supports left/right modifier distinction and multi-key chords.

use rdev::{listen, Event, Key};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::Emitter;

/// Parse a hotkey string like "left_ctrl+left_alt" or "capslock" into key list.
pub fn parse_hotkey(s: &str) -> Option<Vec<Key>> {
    let tokens: Vec<&str> = s.split('+').collect();
    if tokens.is_empty() {
        return None;
    }
    let keys: Vec<Key> = tokens.iter().filter_map(|t| parse_key(t.trim())).collect();
    if keys.len() == tokens.len() {
        Some(keys)
    } else {
        None
    }
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
            if (1..=12).contains(&n) {
                Some(Key::F1)
            } else {
                None
            }
        }
        _ if t.len() == 1 => {
            let c = t.chars().next()?;
            // Map a-z to their Key variants
            let keys = [
                Key::KeyA,
                Key::KeyB,
                Key::KeyC,
                Key::KeyD,
                Key::KeyE,
                Key::KeyF,
                Key::KeyG,
                Key::KeyH,
                Key::KeyI,
                Key::KeyJ,
                Key::KeyK,
                Key::KeyL,
                Key::KeyM,
                Key::KeyN,
                Key::KeyO,
                Key::KeyP,
                Key::KeyQ,
                Key::KeyR,
                Key::KeyS,
                Key::KeyT,
                Key::KeyU,
                Key::KeyV,
                Key::KeyW,
                Key::KeyX,
                Key::KeyY,
                Key::KeyZ,
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
            if hotkey_keys.is_empty() {
                return;
            }

            let is_pressed = Arc::new(AtomicBool::new(false));
            let was_activated = Arc::new(AtomicBool::new(false));
            let p = is_pressed.clone();
            let act = was_activated.clone();
            let a = app.clone();
            let keys = hotkey_keys.clone();

            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let _ = listen(move |event: Event| match event.event_type {
                    rdev::EventType::KeyPress(key) if key_match(&keys, &key) => {
                        if keys.len() == 1 {
                            if !p.swap(true, Ordering::SeqCst) {
                                let _ = a.emit("hotkey-press", ());
                            }
                        } else if !act.swap(true, Ordering::SeqCst) {
                            p.store(true, Ordering::SeqCst);
                            let _ = a.emit("hotkey-press", ());
                        }
                    }
                    rdev::EventType::KeyRelease(key) if key_match(&keys, &key) => {
                        if keys.len() == 1 {
                            if p.swap(false, Ordering::SeqCst) {
                                let _ = a.emit("hotkey-release", ());
                            }
                        } else if act.swap(false, Ordering::SeqCst) {
                            p.store(false, Ordering::SeqCst);
                            let _ = a.emit("hotkey-release", ());
                        }
                    }
                    _ => {}
                });
            }));

            if let Err(e) = result {
                let msg = if let Some(s) = e.downcast_ref::<&str>() {
                    s
                } else if let Some(s) = e.downcast_ref::<String>() {
                    s.as_str()
                } else {
                    "unknown error"
                };
                eprintln!("[hotkey] rdev listener failed: {}", msg);
            }
        });
}

/// Match a single key against the hotkey combo, respecting left/right modifiers.
fn key_match(hotkey: &[Key], event_key: &Key) -> bool {
    use Key::*;
    for hk in hotkey {
        let matched = match (hk, event_key) {
            (ControlLeft, ControlLeft) => true,
            (ControlRight, ControlRight) => true,
            (ControlLeft, _) | (ControlRight, _) => false,
            (ShiftLeft, ShiftLeft) => true,
            (ShiftRight, ShiftRight) => true,
            (ShiftLeft, _) | (ShiftRight, _) => false,
            (Alt, Alt) => true,
            (AltGr, AltGr) => true,
            (Alt, AltGr) | (AltGr, Alt) => false,
            _ => hk == event_key,
        };
        if matched {
            return true;
        }
    }
    false
}
