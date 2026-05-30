//! Global hotkey listener.
//!
//! ## Windows (primary)
//! Uses a dedicated polling thread with `GetAsyncKeyState` (raw Win32 FFI).
//! No hook dependency — works regardless of webview focus / minimize state.
//! The polling thread runs continuously and detects rising/falling edges of
//! the hotkey combo entirely via physical key state queries.
//!
//! ## Other platforms (fallback)
//! Uses rdev `listen()` (WH_KEYBOARD_LL on macOS/Linux) for event-driven
//! hotkey detection. The worker thread still polls as a secondary fallback.
//!
//! Recording lifecycle is handled directly in Rust (not via frontend events)
//! so hotkey operations work even when the webview is minimized/hidden.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

use crate::AppState;

/// Global generation counter. Incremented each time `start_listener` is called.
/// Old poller threads check this and exit when they detect a newer generation.
static LISTENER_GEN: AtomicU64 = AtomicU64::new(0);

// ── Key representation ──

/// Cross-platform key identifier for hotkey combos.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HotkeyKey {
    /// Left Ctrl
    ControlLeft,
    /// Right Ctrl
    ControlRight,
    /// Left Alt (VK_MENU on Windows)
    Alt,
    /// Right Alt / AltGr (VK_RMENU on Windows)
    AltGr,
    /// Left Shift
    ShiftLeft,
    /// Right Shift
    ShiftRight,
    /// Caps Lock
    CapsLock,
    /// Space
    Space,
    /// Enter / Return
    Return,
    /// Tab
    Tab,
    /// Escape
    Escape,
    /// Delete
    Delete,
    /// Backspace
    Backspace,
    /// F1–F12
    F1, F2, F3, F4, F5, F6,
    F7, F8, F9, F10, F11, F12,
    /// A–Z
    KeyA, KeyB, KeyC, KeyD, KeyE, KeyF,
    KeyG, KeyH, KeyI, KeyJ, KeyK, KeyL,
    KeyM, KeyN, KeyO, KeyP, KeyQ, KeyR,
    KeyS, KeyT, KeyU, KeyV, KeyW, KeyX,
    KeyY, KeyZ,
}

/// Parse a hotkey string like `"left_ctrl+left_alt"` or `"capslock"` into key list.
pub fn parse_hotkey(s: &str) -> Option<Vec<HotkeyKey>> {
    let tokens: Vec<&str> = s.split('+').collect();
    if tokens.is_empty() { return None; }
    let keys: Vec<HotkeyKey> = tokens.iter().filter_map(|t| parse_key(t.trim())).collect();
    if keys.len() == tokens.len() { Some(keys) } else { None }
}

fn parse_key(token: &str) -> Option<HotkeyKey> {
    let t = token.to_lowercase();
    match t.as_str() {
        "left_ctrl" | "left_control" | "lctrl" => Some(HotkeyKey::ControlLeft),
        "right_ctrl" | "right_control" | "rctrl" => Some(HotkeyKey::ControlRight),
        "left_alt" | "lalt" => Some(HotkeyKey::Alt),
        "right_alt" | "ralt" => Some(HotkeyKey::AltGr),
        "left_shift" | "lshift" => Some(HotkeyKey::ShiftLeft),
        "right_shift" | "rshift" => Some(HotkeyKey::ShiftRight),
        "ctrl" | "control" => Some(HotkeyKey::ControlLeft),
        "alt" => Some(HotkeyKey::Alt),
        "shift" => Some(HotkeyKey::ShiftLeft),
        "capslock" | "caps" => Some(HotkeyKey::CapsLock),
        "space" => Some(HotkeyKey::Space),
        "enter" | "return" => Some(HotkeyKey::Return),
        "tab" => Some(HotkeyKey::Tab),
        "escape" | "esc" => Some(HotkeyKey::Escape),
        "delete" | "del" => Some(HotkeyKey::Delete),
        "backspace" => Some(HotkeyKey::Backspace),
        _ if t.starts_with('f') && t.len() <= 3 => {
            let n: u8 = t[1..].parse().ok()?;
            if !(1..=12).contains(&n) { return None; }
            Some(match n {
                1  => HotkeyKey::F1,  2 => HotkeyKey::F2,
                3  => HotkeyKey::F3,  4 => HotkeyKey::F4,
                5  => HotkeyKey::F5,  6 => HotkeyKey::F6,
                7  => HotkeyKey::F7,  8 => HotkeyKey::F8,
                9  => HotkeyKey::F9,  10 => HotkeyKey::F10,
                11 => HotkeyKey::F11, 12 => HotkeyKey::F12,
                _ => return None,
            })
        }
        _ if t.len() == 1 => {
            let c = t.chars().next()?;
            let i = (c as u8).wrapping_sub(b'a') as usize;
            const LETTERS: [HotkeyKey; 26] = [
                HotkeyKey::KeyA, HotkeyKey::KeyB, HotkeyKey::KeyC, HotkeyKey::KeyD,
                HotkeyKey::KeyE, HotkeyKey::KeyF, HotkeyKey::KeyG, HotkeyKey::KeyH,
                HotkeyKey::KeyI, HotkeyKey::KeyJ, HotkeyKey::KeyK, HotkeyKey::KeyL,
                HotkeyKey::KeyM, HotkeyKey::KeyN, HotkeyKey::KeyO, HotkeyKey::KeyP,
                HotkeyKey::KeyQ, HotkeyKey::KeyR, HotkeyKey::KeyS, HotkeyKey::KeyT,
                HotkeyKey::KeyU, HotkeyKey::KeyV, HotkeyKey::KeyW, HotkeyKey::KeyX,
                HotkeyKey::KeyY, HotkeyKey::KeyZ,
            ];
            LETTERS.get(i).copied()
        }
        _ => None,
    }
}

/// No-op on non-Windows; press/release tracking is handled by the platform-specific
/// listener and reset is not needed.
pub fn reset_state() {}

// ── Windows implementation: pure GetAsyncKeyState polling ──

#[cfg(target_os = "windows")]
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<HotkeyKey>) {
    if hotkey_keys.is_empty() { return; }

    // Bump generation so any previously spawned poller threads will exit.
    let my_gen = LISTENER_GEN.fetch_add(1, Ordering::SeqCst) + 1;

    let _ = std::thread::Builder::new()
        .name("hotkey-poller".into())
        .spawn(move || {
            eprintln!("[hotkey] Starting GetAsyncKeyState poller for {} keys", hotkey_keys.len());

            let recording = Arc::new(AtomicBool::new(false));

            // Debounce: require keys to be held for this duration before firing Press.
            const PRESS_DEBOUNCE_MS: u64 = 50;
            // Debounce: require keys to be released for this duration before firing Release.
            const RELEASE_DEBOUNCE_MS: u64 = 30;
            // Safety timeout: force-stop recording if running longer than this.
            const MAX_RECORD_SECS: u64 = 300; // 5 minutes

            let mut prev_all_down = false;
            let mut all_down_start: Option<Instant> = None;
            let mut not_down_start: Option<Instant> = None;
            let mut record_start: Option<Instant> = None;

            loop {
                // Exit if a newer listener generation was registered.
                if LISTENER_GEN.load(Ordering::SeqCst) != my_gen {
                    eprintln!("[hotkey] Poller gen {} superseded, exiting", my_gen);
                    break;
                }

                let all_down = hotkey_keys.iter().all(|k| win_key_down(k));

                // ── Rising edge: keys just became fully pressed ──
                if all_down && !prev_all_down {
                    not_down_start = None;
                    all_down_start = Some(Instant::now());
                }

                // ── Falling edge: a key just got released ──
                if !all_down && prev_all_down {
                    all_down_start = None;
                    not_down_start = Some(Instant::now());
                }

                // ── Debounced Press trigger ──
                if all_down && !recording.load(Ordering::SeqCst) {
                    if let Some(start) = all_down_start {
                        if start.elapsed() >= Duration::from_millis(PRESS_DEBOUNCE_MS) {
                            // All keys held debounce duration → start recording
                            recording.store(true, Ordering::SeqCst);
                            all_down_start = None;
                            record_start = Some(Instant::now());

                            let _ = app.emit("hotkey-press", ());
                            eprintln!("[hotkey] Press detected (poll), starting recording");

                            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                let state = app.state::<AppState>();
                                let _ = crate::start_recording_internal(&app, &state);
                            }));
                            if r.is_err() {
                                eprintln!("[hotkey] start_recording_internal panic");
                                recording.store(false, Ordering::SeqCst);
                                record_start = None;
                            }
                        }
                    }
                }

                // ── Debounced Release trigger ──
                if !all_down && recording.load(Ordering::SeqCst) {
                    if let Some(start) = not_down_start {
                        if start.elapsed() >= Duration::from_millis(RELEASE_DEBOUNCE_MS) {
                            // Keys have been released for debounce duration → stop recording
                            recording.store(false, Ordering::SeqCst);
                            not_down_start = None;
                            record_start = None;

                            let _ = app.emit("hotkey-release", ());
                            eprintln!("[hotkey] Release detected (poll), stopping recording");

                            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                let state = app.state::<AppState>();
                                let _ = crate::stop_recording_internal(&app, &state);
                            }));
                        }
                    }
                }

                // ── Safety timeout: force-stop if stuck recording ──
                if recording.load(Ordering::SeqCst) {
                    if let Some(start) = record_start {
                        if start.elapsed() >= Duration::from_secs(MAX_RECORD_SECS) {
                            eprintln!("[hotkey] Safety timeout: force-stopping recording after {}s", MAX_RECORD_SECS);
                            recording.store(false, Ordering::SeqCst);
                            record_start = None;

                            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                let state = app.state::<AppState>();
                                // Reset the recorder directly (cleaner than partial stop)
                                if let Ok(mut rec) = state.recorder.lock() {
                                    rec.reset();
                                }
                            }));
                        }
                    }
                }

                // ── Handle sticky NOT-down state: if keys weren't down long enough
                // to trigger press, but they're no longer fully pressed, reset.
                if !all_down && !recording.load(Ordering::SeqCst) {
                    all_down_start = None;
                }

                prev_all_down = all_down;

                #[cfg(target_os = "windows")]
                std::thread::sleep(Duration::from_millis(10));
                #[cfg(not(target_os = "windows"))]
                std::thread::sleep(Duration::from_millis(50));
            }
        });
}

// ── Non-Windows implementation: rdev listener ──

#[cfg(not(target_os = "windows"))]
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<HotkeyKey>) {
    use std::sync::{mpsc, Arc, Mutex, OnceLock};
    use std::time::Instant;
    use tauri::Manager;

    // Quick conversion back to rdev::Key for the legacy code path.
    // This keeps the non-Windows path working unchanged.
    fn hotkey_to_rdev(k: &HotkeyKey) -> Option<rdev::Key> {
        Some(match k {
            HotkeyKey::ControlLeft  => rdev::Key::ControlLeft,
            HotkeyKey::ControlRight => rdev::Key::ControlRight,
            HotkeyKey::Alt          => rdev::Key::Alt,
            HotkeyKey::AltGr        => rdev::Key::AltGr,
            HotkeyKey::ShiftLeft    => rdev::Key::ShiftLeft,
            HotkeyKey::ShiftRight   => rdev::Key::ShiftRight,
            HotkeyKey::CapsLock     => rdev::Key::CapsLock,
            HotkeyKey::Space        => rdev::Key::Space,
            HotkeyKey::Return       => rdev::Key::Return,
            HotkeyKey::Tab          => rdev::Key::Tab,
            HotkeyKey::Escape       => rdev::Key::Escape,
            HotkeyKey::Delete       => rdev::Key::Delete,
            HotkeyKey::Backspace    => rdev::Key::Backspace,
            HotkeyKey::F1  => rdev::Key::F1,  HotkeyKey::F2  => rdev::Key::F2,
            HotkeyKey::F3  => rdev::Key::F3,  HotkeyKey::F4  => rdev::Key::F4,
            HotkeyKey::F5  => rdev::Key::F5,  HotkeyKey::F6  => rdev::Key::F6,
            HotkeyKey::F7  => rdev::Key::F7,  HotkeyKey::F8  => rdev::Key::F8,
            HotkeyKey::F9  => rdev::Key::F9,  HotkeyKey::F10 => rdev::Key::F10,
            HotkeyKey::F11 => rdev::Key::F11, HotkeyKey::F12 => rdev::Key::F12,
            HotkeyKey::KeyA => rdev::Key::KeyA, HotkeyKey::KeyB => rdev::Key::KeyB,
            HotkeyKey::KeyC => rdev::Key::KeyC, HotkeyKey::KeyD => rdev::Key::KeyD,
            HotkeyKey::KeyE => rdev::Key::KeyE, HotkeyKey::KeyF => rdev::Key::KeyF,
            HotkeyKey::KeyG => rdev::Key::KeyG, HotkeyKey::KeyH => rdev::Key::KeyH,
            HotkeyKey::KeyI => rdev::Key::KeyI, HotkeyKey::KeyJ => rdev::Key::KeyJ,
            HotkeyKey::KeyK => rdev::Key::KeyK, HotkeyKey::KeyL => rdev::Key::KeyL,
            HotkeyKey::KeyM => rdev::Key::KeyM, HotkeyKey::KeyN => rdev::Key::KeyN,
            HotkeyKey::KeyO => rdev::Key::KeyO, HotkeyKey::KeyP => rdev::Key::KeyP,
            HotkeyKey::KeyQ => rdev::Key::KeyQ, HotkeyKey::KeyR => rdev::Key::KeyR,
            HotkeyKey::KeyS => rdev::Key::KeyS, HotkeyKey::KeyT => rdev::Key::KeyT,
            HotkeyKey::KeyU => rdev::Key::KeyU, HotkeyKey::KeyV => rdev::Key::KeyV,
            HotkeyKey::KeyW => rdev::Key::KeyW, HotkeyKey::KeyX => rdev::Key::KeyX,
            HotkeyKey::KeyY => rdev::Key::KeyY, HotkeyKey::KeyZ => rdev::Key::KeyZ,
        })
    }

    let rdev_keys: Vec<rdev::Key> = hotkey_keys.iter().filter_map(hotkey_to_rdev).collect();
    if rdev_keys.is_empty() { return; }

    let my_gen = LISTENER_GEN.fetch_add(1, Ordering::SeqCst) + 1;

    let _ = std::thread::Builder::new()
        .name("hotkey-listener".into())
        .spawn(move || {
            let pressed = Arc::new(Mutex::new(Vec::<rdev::Key>::new()));
            static PRESSED_KEYS: OnceLock<Arc<Mutex<Vec<rdev::Key>>>> = OnceLock::new();
            let _ = PRESSED_KEYS.set(pressed.clone());

            let keys = rdev_keys.clone();
            let a = app.clone();

            enum HotkeyCmd { Press, Release }
            let (cmd_tx, cmd_rx) = mpsc::channel::<HotkeyCmd>();
            let worker_app = a.clone();
            let recording = Arc::new(AtomicBool::new(false));
            let worker_recording = recording.clone();
            let hotkey_keys_worker = rdev_keys.clone();

            // Worker thread (same logic as before)
            let _ = std::thread::Builder::new()
                .name("hotkey-worker".into())
                .spawn(move || {
                    loop {
                        // Exit if a newer listener generation was registered.
                        if LISTENER_GEN.load(Ordering::SeqCst) != my_gen {
                            eprintln!("[hotkey] Worker gen {} superseded, exiting", my_gen);
                            break;
                        }
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
                                'record: loop {
                                    match cmd_rx.try_recv() {
                                        Ok(HotkeyCmd::Release) => break 'record,
                                        Ok(HotkeyCmd::Press) => break 'record,
                                        Err(mpsc::TryRecvError::Disconnected) => break 'record,
                                        Err(mpsc::TryRecvError::Empty) => {}
                                    }
                                    // Non-Windows fallback: check key state if available
                                    #[cfg(target_os = "macos")]
                                    {
                                        // macOS: limited polling support — rely on rdev events
                                    }
                                    std::thread::sleep(std::time::Duration::from_millis(50));
                                }
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

            // rdev listen callback
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let mut last_press_emit = Instant::now() - std::time::Duration::from_secs(1);
                let _ = rdev::listen(move |event: rdev::Event| {
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
                    } else { false };

                    let now = Instant::now();

                    if let rdev::EventType::KeyPress(_) = event.event_type {
                        if all_pressed {
                            last_press_emit = now;
                            let _ = cmd_tx.send(HotkeyCmd::Press);
                            let _ = a.emit("hotkey-press", ());
                        }
                        return;
                    }

                    if !all_pressed {
                        if now.duration_since(last_press_emit).as_millis() < 100 {
                            return;
                        }
                        let _ = cmd_tx.send(HotkeyCmd::Release);
                        let _ = a.emit("hotkey-release", ());
                    }
                });
            }));
        });
}

// ── Windows: GetAsyncKeyState helpers ──

#[cfg(target_os = "windows")]
fn win_key_down(k: &HotkeyKey) -> bool {
    extern "system" {
        fn GetAsyncKeyState(vKey: i32) -> i16;
    }
    let Some(vk) = hotkey_to_vk(k) else { return false }; // unknown key = assume NOT pressed
    unsafe { GetAsyncKeyState(vk) & 0x8000 != 0 }
}

/// Maps HotkeyKey → Windows Virtual-Key code.
#[cfg(target_os = "windows")]
fn hotkey_to_vk(k: &HotkeyKey) -> Option<i32> {
    Some(match k {
        HotkeyKey::Alt          => 0x12,    // VK_MENU
        HotkeyKey::AltGr        => 0xA5,    // VK_RMENU
        HotkeyKey::ControlLeft  => 0xA2,    // VK_LCONTROL
        HotkeyKey::ControlRight => 0xA3,    // VK_RCONTROL
        HotkeyKey::ShiftLeft    => 0xA0,    // VK_LSHIFT
        HotkeyKey::ShiftRight   => 0xA1,    // VK_RSHIFT
        HotkeyKey::CapsLock     => 0x14,    // VK_CAPITAL
        HotkeyKey::Space        => 0x20,    // VK_SPACE
        HotkeyKey::Return       => 0x0D,    // VK_RETURN
        HotkeyKey::Tab          => 0x09,    // VK_TAB
        HotkeyKey::Escape       => 0x1B,    // VK_ESCAPE
        HotkeyKey::Delete       => 0x2E,    // VK_DELETE
        HotkeyKey::Backspace    => 0x08,    // VK_BACK
        HotkeyKey::F1  => 0x70,  HotkeyKey::F2  => 0x71,
        HotkeyKey::F3  => 0x72,  HotkeyKey::F4  => 0x73,
        HotkeyKey::F5  => 0x74,  HotkeyKey::F6  => 0x75,
        HotkeyKey::F7  => 0x76,  HotkeyKey::F8  => 0x77,
        HotkeyKey::F9  => 0x78,  HotkeyKey::F10 => 0x79,
        HotkeyKey::F11 => 0x7A,  HotkeyKey::F12 => 0x7B,
        HotkeyKey::KeyA => 0x41, HotkeyKey::KeyB => 0x42,
        HotkeyKey::KeyC => 0x43, HotkeyKey::KeyD => 0x44,
        HotkeyKey::KeyE => 0x45, HotkeyKey::KeyF => 0x46,
        HotkeyKey::KeyG => 0x47, HotkeyKey::KeyH => 0x48,
        HotkeyKey::KeyI => 0x49, HotkeyKey::KeyJ => 0x4A,
        HotkeyKey::KeyK => 0x4B, HotkeyKey::KeyL => 0x4C,
        HotkeyKey::KeyM => 0x4D, HotkeyKey::KeyN => 0x4E,
        HotkeyKey::KeyO => 0x4F, HotkeyKey::KeyP => 0x50,
        HotkeyKey::KeyQ => 0x51, HotkeyKey::KeyR => 0x52,
        HotkeyKey::KeyS => 0x53, HotkeyKey::KeyT => 0x54,
        HotkeyKey::KeyU => 0x55, HotkeyKey::KeyV => 0x56,
        HotkeyKey::KeyW => 0x57, HotkeyKey::KeyX => 0x58,
        HotkeyKey::KeyY => 0x59, HotkeyKey::KeyZ => 0x5A,
    })
}
