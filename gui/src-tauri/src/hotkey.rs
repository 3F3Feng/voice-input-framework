//! Custom global hotkey listener using rdev.
//! Supports left/right modifier distinction and multi-key chords.
//!
//! Recording lifecycle is handled directly in Rust (not via frontend events)
//! so hotkey operations work even when the webview is minimized/hidden.

use rdev::{listen, Event, Key};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc, Mutex, OnceLock,
};
use std::time::Instant;
use tauri::{Emitter, Manager};

static PRESSED_KEYS: OnceLock<Arc<Mutex<Vec<Key>>>> = OnceLock::new();

/// Reset hotkey listener state. Call when window is minimized/hidden
/// to prevent stuck key states after minimize.
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

            // ── Non-blocking hotkey lifecycle ──
    // On Windows, rdev uses WH_KEYBOARD_LL which requires the hook callback
    // to return quickly (Windows can silently remove slow hooks).
    // start/stop_recording_internal involve audio device setup (cpal) that can
    // take tens of ms, so we defer them to a worker thread via mpsc channel.
    // This way the hook callback only updates key state and pushes a command.

    enum HotkeyCmd { Press, Release }

    let hotkey_keys_worker = hotkey_keys.clone();
    let (cmd_tx, cmd_rx) = mpsc::channel::<HotkeyCmd>();
    let worker_app = a.clone();

    // Worker thread: processes start/stop recording without holding up rdev hook.
    // After starting recording, polls physical key state in a tight loop.
    // On Windows this uses GetAsyncKeyState (independent of hook/focus).
    // If the hook misses a KeyRelease event, the poll catches the state change.
    let recording = Arc::new(AtomicBool::new(false));
    let worker_recording = recording.clone();
    let _ = std::thread::Builder::new()
        .name("hotkey-worker".into())
        .spawn(move || {
            loop {
                match cmd_rx.recv() {
                    Ok(HotkeyCmd::Press) => {
                        if worker_recording.swap(true, Ordering::SeqCst) { continue; }
                        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            let state = worker_app.state::<crate::AppState>();
                            let _ = crate::start_recording_internal(&worker_app, &state);
                        }));
                        if r.is_err() {
                            eprintln!("[hotkey] start_recording panic");
                            worker_recording.store(false, Ordering::SeqCst);
                            continue;
                        }

                        // Poll physical key state until release (or explicit Release cmd).
                        // rdev::is_key_pressed uses GetAsyncKeyState on Windows — no
                        // hook timing issues, works regardless of focus/visibility.
                        'record: loop {
                            match cmd_rx.try_recv() {
                                Ok(HotkeyCmd::Release) => break 'record,
                                Ok(HotkeyCmd::Press) => {
                                    // Re-press while recording → force stop
                                    break 'record;
                                }
                                Err(mpsc::TryRecvError::Disconnected) => break 'record,
                                Err(mpsc::TryRecvError::Empty) => {}
                            }
                            // Check if any hotkey key is physically released
                            let released = hotkey_keys_worker.iter().any(|k| !rdev::is_key_pressed(k));
                            if released { break 'record; }
                            std::thread::sleep(std::time::Duration::from_millis(10));
                        }

                        // Stop recording (release was detected via poll or cmd)
                        worker_recording.store(false, Ordering::SeqCst);
                        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            let state = worker_app.state::<crate::AppState>();
                            let _ = crate::stop_recording_internal(&worker_app, &state);
                        }));
                    }
                    _ => break,
                }
            }
        });

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
            if let rdev::EventType::KeyPress(_) = event.event_type {
                if all_pressed {
                    last_press_emit = now;
                    let _ = cmd_tx.send(HotkeyCmd::Press);
                    let _ = a.emit("hotkey-press", ());
                }
                return;
            }

            // ── Hotkey released: any key of the combo released ──
            if !all_pressed {
                if now.duration_since(last_press_emit).as_millis() < 100 {
                    return;
                }
                let _ = cmd_tx.send(HotkeyCmd::Release);
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
