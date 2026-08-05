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

use std::sync::atomic::{AtomicU64, Ordering};
use tauri::{Emitter, Manager};

// Windows branch (raw Win32 polling) uses these bare names; other
// platforms reference them fully-qualified.
#[cfg(target_os = "windows")]
use std::sync::atomic::AtomicBool;
#[cfg(target_os = "windows")]
use std::sync::Arc;
#[cfg(target_os = "windows")]
use std::time::{Duration, Instant};
#[cfg(target_os = "windows")]
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
                                // Reset the recorder directly (cleaner than partial stop)
                                // Bind in separate lets to ensure proper drop order
                                let state = app.state::<AppState>();
                                let result = state.recorder.lock();
                                if let Ok(mut guard) = result {
                                    guard.reset();
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

// ── Non-Windows: hotkey worker (shared by macOS CGEventTap & Linux rdev) ──

#[cfg(not(target_os = "windows"))]
enum HotkeyCmd {
    Press,
    Release,
}

#[cfg(not(target_os = "windows"))]
fn spawn_hotkey_worker(
    app: tauri::AppHandle,
    cmd_rx: std::sync::mpsc::Receiver<HotkeyCmd>,
    my_gen: u64,
) {
    use std::sync::atomic::AtomicBool;
    use std::sync::Arc;

    let recording = Arc::new(AtomicBool::new(false));
    let _ = std::thread::Builder::new()
        .name("hotkey-worker".into())
        .spawn(move || loop {
            // Exit if a newer listener generation was registered.
            if LISTENER_GEN.load(Ordering::SeqCst) != my_gen {
                eprintln!("[hotkey] Worker gen {} superseded, exiting", my_gen);
                break;
            }
            match cmd_rx.recv() {
                Ok(HotkeyCmd::Press) => {
                    if recording.swap(true, Ordering::SeqCst) {
                        continue;
                    }
                    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        let state = app.state::<crate::AppState>();
                        let _ = crate::start_recording_internal(&app, &state);
                    }));
                    if r.is_err() {
                        eprintln!("[hotkey] start_recording panic");
                        recording.store(false, Ordering::SeqCst);
                        continue;
                    }
                    'record: loop {
                        match cmd_rx.try_recv() {
                            Ok(_) => break 'record,
                            Err(std::sync::mpsc::TryRecvError::Disconnected) => break 'record,
                            Err(std::sync::mpsc::TryRecvError::Empty) => {}
                        }
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    recording.store(false, Ordering::SeqCst);
                    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        let state = app.state::<crate::AppState>();
                        let _ = crate::stop_recording_internal(&app, &state);
                    }));
                }
                _ => break,
            }
        });
}

/// Shared press/release bookkeeping + hotkey-match → worker channel.
/// Returns the emitted channel command, if any.
#[cfg(not(target_os = "windows"))]
struct HotkeyMatcher {
    pressed: Vec<HotkeyKey>,
    keys: Vec<HotkeyKey>,
    last_press_emit: std::time::Instant,
}

#[cfg(not(target_os = "windows"))]
impl HotkeyMatcher {
    fn new(keys: Vec<HotkeyKey>) -> Self {
        Self {
            pressed: Vec::new(),
            keys,
            last_press_emit: std::time::Instant::now()
                - std::time::Duration::from_secs(1),
        }
    }

    fn on_change(&mut self, key: HotkeyKey, is_press: bool) -> Option<HotkeyCmd> {
        if is_press {
            if !self.pressed.contains(&key) {
                self.pressed.push(key);
            }
        } else {
            self.pressed.retain(|&x| x != key);
        }
        let all_pressed = self.keys.iter().all(|k| self.pressed.contains(k));
        let now = std::time::Instant::now();
        if is_press {
            if all_pressed {
                self.last_press_emit = now;
                return Some(HotkeyCmd::Press);
            }
        } else if !all_pressed {
            if now.duration_since(self.last_press_emit).as_millis() < 100 {
                return None;
            }
            return Some(HotkeyCmd::Release);
        }
        None
    }
}

// ── macOS: Quartz CGEventTap listener ──
//
// rdev's macOS backend calls TSMGetInputSourceProperty (TextServices) in
// its event callback; on macOS 15+ with a running NSApplication main loop
// that call dispatches on the background listener thread and hits
// _dispatch_assert_queue_fail → SIGTRAP crash (identical to the pynput
// crash we replaced on the Python client). CGEventTap is a pure C API and
// never touches TextServices, so it is crash-free.

#[cfg(target_os = "macos")]
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<HotkeyKey>) {
    let my_gen = LISTENER_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    let (cmd_tx, cmd_rx) = std::sync::mpsc::channel::<HotkeyCmd>();
    spawn_hotkey_worker(app.clone(), cmd_rx, my_gen);

    let _ = std::thread::Builder::new()
        .name("hotkey-listener".into())
        .spawn(move || {
            let mut matcher = HotkeyMatcher::new(hotkey_keys.clone());
            let a = app.clone();
            mac_tap::run(move |key, is_press| {
                if let Some(cmd) = matcher.on_change(key, is_press) {
                    let _ = cmd_tx.send(cmd);
                    let _ = a.emit(
                        if is_press { "hotkey-press" } else { "hotkey-release" },
                        (),
                    );
                }
            });
        });
}

#[cfg(target_os = "macos")]
mod mac_tap {
    use super::*;
    use core_foundation::runloop::*;
    use core_graphics::event::{
        CGEvent, CGEventFlags, CGEventTap, CGEventTapLocation, CGEventTapOptions,
        CGEventTapPlacement, CGEventType, EventField,
    };

    // macOS HID keycode → HotkeyKey (ANSI layout; letters A=0x00, not Windows VK).
    fn hid_to_hotkey(vk: i64) -> Option<HotkeyKey> {
        Some(match vk {
            0x00 => HotkeyKey::KeyA,
            0x01 => HotkeyKey::KeyS,
            0x02 => HotkeyKey::KeyD,
            0x03 => HotkeyKey::KeyF,
            0x04 => HotkeyKey::KeyH,
            0x05 => HotkeyKey::KeyG,
            0x06 => HotkeyKey::KeyZ,
            0x07 => HotkeyKey::KeyX,
            0x08 => HotkeyKey::KeyC,
            0x09 => HotkeyKey::KeyV,
            0x0B => HotkeyKey::KeyB,
            0x0C => HotkeyKey::KeyQ,
            0x0D => HotkeyKey::KeyW,
            0x0E => HotkeyKey::KeyE,
            0x0F => HotkeyKey::KeyR,
            0x10 => HotkeyKey::KeyY,
            0x11 => HotkeyKey::KeyT,
            0x1F => HotkeyKey::KeyO,
            0x20 => HotkeyKey::KeyU,
            0x22 => HotkeyKey::KeyI,
            0x23 => HotkeyKey::KeyP,
            0x25 => HotkeyKey::KeyL,
            0x26 => HotkeyKey::KeyJ,
            0x28 => HotkeyKey::KeyK,
            0x2D => HotkeyKey::KeyN,
            0x2E => HotkeyKey::KeyM,
            0x31 => HotkeyKey::Space,
            0x30 => HotkeyKey::Tab,
            0x24 => HotkeyKey::Return,
            0x33 => HotkeyKey::Backspace,
            0x35 => HotkeyKey::Escape,
            0x75 => HotkeyKey::Delete,
            0x39 => HotkeyKey::CapsLock,
            0x7A => HotkeyKey::F1,
            0x78 => HotkeyKey::F2,
            0x63 => HotkeyKey::F3,
            0x76 => HotkeyKey::F4,
            0x60 => HotkeyKey::F5,
            0x61 => HotkeyKey::F6,
            0x62 => HotkeyKey::F7,
            0x64 => HotkeyKey::F8,
            0x65 => HotkeyKey::F9,
            0x6D => HotkeyKey::F10,
            0x67 => HotkeyKey::F11,
            0x6F => HotkeyKey::F12,
            _ => return None,
        })
    }

    // Modifier HID keycodes → flag mask. FlagsChanged events carry the
    // keycode of the changed modifier; CGEventGetFlags decides press vs
    // release (kCGEventFlagMaskControl = 0x40000; after release the flag
    // is gone, so flags&mask is reliable — no event-count toggling).
    fn mod_flag(vk: i64) -> Option<CGEventFlags> {
        Some(match vk {
            0x37 | 0x36 => CGEventFlags::CGEventFlagCommand,
            0x38 | 0x3C => CGEventFlags::CGEventFlagShift,
            0x3A | 0x3D => CGEventFlags::CGEventFlagAlternate,
            0x3B | 0x3E => CGEventFlags::CGEventFlagControl,
            _ => return None,
        })
    }

    /// Run a CGEventTap on the current thread (called from the
    /// hotkey-listener thread). Never touches TextServices, so it does not
    /// hit the macOS 15+ TSMGetInputSourceProperty assert crash that rdev
    /// does. Requires Input Monitoring / Accessibility permission;
    /// otherwise CGEventTapCreate fails and we log and return.
    pub fn run<F>(handler: F)
    where
        F: FnMut(HotkeyKey, bool) + 'static,
    {
        // CGEventTap::new's callback is `Fn` (immutable), but our handler
        // mutates the matcher — RefCell gives interior mutability; the
        // tap callback runs on this same (listener) thread, so no
        // cross-thread access and no Send requirement.
        let handler = std::cell::RefCell::new(handler);
        let events = vec![
            CGEventType::KeyDown,
            CGEventType::KeyUp,
            CGEventType::FlagsChanged,
        ];
        let tap = CGEventTap::new(
            CGEventTapLocation::Session,
            CGEventTapPlacement::HeadInsertEventTap,
            CGEventTapOptions::ListenOnly,
            events,
            move |_proxy, etype, event| {
                let mut h = handler.borrow_mut();
                match etype {
                    CGEventType::KeyDown | CGEventType::KeyUp => {
                        let vk = event.get_integer_value_field(EventField::KEYBOARD_EVENT_KEYCODE);
                        if let Some(k) = hid_to_hotkey(vk) {
                            h(k, matches!(etype, CGEventType::KeyDown));
                        }
                    }
                    CGEventType::FlagsChanged => {
                        let vk = event.get_integer_value_field(EventField::KEYBOARD_EVENT_KEYCODE);
                        let flags = event.get_flags();
                        if let Some(mask) = mod_flag(vk) {
                            if let Some(k) = hid_to_hotkey(vk) {
                                h(k, flags.contains(mask));
                            }
                        }
                    }
                    _ => {}
                }
                None
            },
        );
        match tap {
            Ok(tap) => {
                tap.enable();
                match tap.mach_port.create_runloop_source(0) {
                    Ok(source) => {
                        let current = CFRunLoop::get_current();
                        // kCFRunLoopCommonModes is an extern static — reading it is unsafe
                        current.add_source(&source, unsafe { kCFRunLoopCommonModes });
                        eprintln!("[hotkey] CGEventTap listening");
                        CFRunLoop::run_current();
                    }
                    Err(_) => eprintln!("[hotkey] create_runloop_source failed"),
                }
            }
            Err(_) => eprintln!(
                "[hotkey] CGEventTapCreate failed (Input Monitoring permission?)"
            ),
        }
    }
}

// ── Linux (and other non-Windows/non-macOS): rdev listener ──

#[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<HotkeyKey>) {
    use std::sync::mpsc;

    let my_gen = LISTENER_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    let (cmd_tx, cmd_rx) = mpsc::channel::<HotkeyCmd>();
    spawn_hotkey_worker(app.clone(), cmd_rx, my_gen);

    let _ = std::thread::Builder::new()
        .name("hotkey-listener".into())
        .spawn(move || {
            let mut matcher = HotkeyMatcher::new(hotkey_keys.clone());
            let a = app.clone();

            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let _ = rdev::listen(move |event: rdev::Event| {
                    let (key, is_press) = match event.event_type {
                        rdev::EventType::KeyPress(k) => (Some(k), true),
                        rdev::EventType::KeyRelease(k) => (Some(k), false),
                        _ => (None, false),
                    };
                    let Some(rk) = key else { return };
                    let Some(k) = rdev_to_hotkey(&rk) else { return };
                    if let Some(cmd) = matcher.on_change(k, is_press) {
                        let _ = cmd_tx.send(cmd);
                        let _ = a.emit(
                            if is_press { "hotkey-press" } else { "hotkey-release" },
                            (),
                        );
                    }
                });
            }));
        });
}

#[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
fn rdev_to_hotkey(k: &rdev::Key) -> Option<HotkeyKey> {
    Some(match k {
        rdev::Key::ControlLeft => HotkeyKey::ControlLeft,
        rdev::Key::ControlRight => HotkeyKey::ControlRight,
        rdev::Key::Alt => HotkeyKey::Alt,
        rdev::Key::AltGr => HotkeyKey::AltGr,
        rdev::Key::ShiftLeft => HotkeyKey::ShiftLeft,
        rdev::Key::ShiftRight => HotkeyKey::ShiftRight,
        rdev::Key::CapsLock => HotkeyKey::CapsLock,
        rdev::Key::Space => HotkeyKey::Space,
        rdev::Key::Return => HotkeyKey::Return,
        rdev::Key::Tab => HotkeyKey::Tab,
        rdev::Key::Escape => HotkeyKey::Escape,
        rdev::Key::Delete => HotkeyKey::Delete,
        rdev::Key::Backspace => HotkeyKey::Backspace,
        rdev::Key::F1 => HotkeyKey::F1,
        rdev::Key::F2 => HotkeyKey::F2,
        rdev::Key::F3 => HotkeyKey::F3,
        rdev::Key::F4 => HotkeyKey::F4,
        rdev::Key::F5 => HotkeyKey::F5,
        rdev::Key::F6 => HotkeyKey::F6,
        rdev::Key::F7 => HotkeyKey::F7,
        rdev::Key::F8 => HotkeyKey::F8,
        rdev::Key::F9 => HotkeyKey::F9,
        rdev::Key::F10 => HotkeyKey::F10,
        rdev::Key::F11 => HotkeyKey::F11,
        rdev::Key::F12 => HotkeyKey::F12,
        rdev::Key::KeyA => HotkeyKey::KeyA,
        rdev::Key::KeyB => HotkeyKey::KeyB,
        rdev::Key::KeyC => HotkeyKey::KeyC,
        rdev::Key::KeyD => HotkeyKey::KeyD,
        rdev::Key::KeyE => HotkeyKey::KeyE,
        rdev::Key::KeyF => HotkeyKey::KeyF,
        rdev::Key::KeyG => HotkeyKey::KeyG,
        rdev::Key::KeyH => HotkeyKey::KeyH,
        rdev::Key::KeyI => HotkeyKey::KeyI,
        rdev::Key::KeyJ => HotkeyKey::KeyJ,
        rdev::Key::KeyK => HotkeyKey::KeyK,
        rdev::Key::KeyL => HotkeyKey::KeyL,
        rdev::Key::KeyM => HotkeyKey::KeyM,
        rdev::Key::KeyN => HotkeyKey::KeyN,
        rdev::Key::KeyO => HotkeyKey::KeyO,
        rdev::Key::KeyP => HotkeyKey::KeyP,
        rdev::Key::KeyQ => HotkeyKey::KeyQ,
        rdev::Key::KeyR => HotkeyKey::KeyR,
        rdev::Key::KeyS => HotkeyKey::KeyS,
        rdev::Key::KeyT => HotkeyKey::KeyT,
        rdev::Key::KeyU => HotkeyKey::KeyU,
        rdev::Key::KeyV => HotkeyKey::KeyV,
        rdev::Key::KeyW => HotkeyKey::KeyW,
        rdev::Key::KeyX => HotkeyKey::KeyX,
        rdev::Key::KeyY => HotkeyKey::KeyY,
        rdev::Key::KeyZ => HotkeyKey::KeyZ,
        _ => return None,
    })
}

// ── Windows: GetAsyncKeyState helpers ──

#[cfg(target_os = "windows")]
fn win_key_down(k: &HotkeyKey) -> bool {
    extern "system" {
        fn GetAsyncKeyState(vKey: i32) -> i16;
    }
    let Some(vk) = hotkey_to_vk(k) else { return false }; // unknown key = assume NOT pressed
    // GetAsyncKeyState returns SHORT (i16). MSB=0x8000 means key is down.
    // Cast to u16 to avoid literal out of range for i16.
    unsafe { (GetAsyncKeyState(vk) as u16) & 0x8000u16 != 0 }
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
