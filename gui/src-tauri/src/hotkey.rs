//! Custom global hotkey listener.
//! Uses rdev event-based listen() on a background thread.
//! Falls back gracefully if rdev fails or panics.

use rdev::{listen, Event, EventType, Key};
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
            match c {
                'a' => Some(Key::KeyA),
                'b' => Some(Key::KeyB),
                'c' => Some(Key::KeyC),
                'd' => Some(Key::KeyD),
                'e' => Some(Key::KeyE),
                'f' => Some(Key::KeyF),
                'g' => Some(Key::KeyG),
                'h' => Some(Key::KeyH),
                'i' => Some(Key::KeyI),
                'j' => Some(Key::KeyJ),
                'k' => Some(Key::KeyK),
                'l' => Some(Key::KeyL),
                'm' => Some(Key::KeyM),
                'n' => Some(Key::KeyN),
                'o' => Some(Key::KeyO),
                'p' => Some(Key::KeyP),
                'q' => Some(Key::KeyQ),
                'r' => Some(Key::KeyR),
                's' => Some(Key::KeyS),
                't' => Some(Key::KeyT),
                'u' => Some(Key::KeyU),
                'v' => Some(Key::KeyV),
                'w' => Some(Key::KeyW),
                'x' => Some(Key::KeyX),
                'y' => Some(Key::KeyY),
                'z' => Some(Key::KeyZ),
                _ => None,
            }
        }
        _ => None,
    }
}

/// Start background hotkey listener using rdev.
/// If rdev fails, the app continues without hotkey support.
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<Key>) {
    let res = std::thread::Builder::new()
        .name("hotkey-listener".into())
        .spawn(move || {
            if hotkey_keys.is_empty() {
                return;
            }

            let pressed = Arc::new(AtomicBool::new(false));
            let p = pressed.clone();
            let a = app.clone();

            eprintln!("[hotkey] Starting rdev listener on background thread...");

            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let _ = listen(move |event: Event| match event.event_type {
                    EventType::KeyPress(key)
                        if hotkey_matches(&hotkey_keys, &key)
                            && !p.swap(true, Ordering::SeqCst) =>
                    {
                        let _ = a.emit("hotkey-press", ());
                    }
                    EventType::KeyRelease(key)
                        if hotkey_matches(&hotkey_keys, &key)
                            && p.swap(false, Ordering::SeqCst) =>
                    {
                        let _ = a.emit("hotkey-release", ());
                    }
                    _ => {}
                });
            }));

            match result {
                Ok(_) => eprintln!("[hotkey] rdev listener exited"),
                Err(e) => {
                    let msg = if let Some(s) = e.downcast_ref::<&str>() {
                        *s
                    } else if let Some(s) = e.downcast_ref::<String>() {
                        s.as_str()
                    } else {
                        "unknown error"
                    };
                    eprintln!("[hotkey] rdev listener failed: {}", msg);
                }
            }
        });

    if let Err(e) = res {
        eprintln!("[hotkey] Failed to spawn listener thread: {}", e);
    }
}

fn hotkey_matches(hotkey: &[Key], event_key: &Key) -> bool {
    if hotkey.is_empty() {
        return false;
    }
    if hotkey.len() == 1 {
        return keys_cmp(&hotkey[0], event_key);
    }
    hotkey.iter().any(|k| keys_cmp(k, event_key))
}

fn keys_cmp(a: &Key, b: &Key) -> bool {
    use Key::*;
    match (a, b) {
        (ControlLeft, ControlLeft)
        | (ControlLeft, ControlRight)
        | (ControlRight, ControlLeft)
        | (ControlRight, ControlRight) => true,
        (ShiftLeft, ShiftLeft)
        | (ShiftLeft, ShiftRight)
        | (ShiftRight, ShiftLeft)
        | (ShiftRight, ShiftRight) => true,
        (Alt, AltGr) | (AltGr, Alt) => true,
        _ => a == b,
    }
}
