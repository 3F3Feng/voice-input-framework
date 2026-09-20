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
use crate::AppState;
#[cfg(target_os = "windows")]
use std::sync::atomic::AtomicBool;
#[cfg(target_os = "windows")]
use std::sync::Arc;
#[cfg(target_os = "windows")]
use std::time::{Duration, Instant};

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
    F1,
    F2,
    F3,
    F4,
    F5,
    F6,
    F7,
    F8,
    F9,
    F10,
    F11,
    F12,
    /// A–Z
    KeyA,
    KeyB,
    KeyC,
    KeyD,
    KeyE,
    KeyF,
    KeyG,
    KeyH,
    KeyI,
    KeyJ,
    KeyK,
    KeyL,
    KeyM,
    KeyN,
    KeyO,
    KeyP,
    KeyQ,
    KeyR,
    KeyS,
    KeyT,
    KeyU,
    KeyV,
    KeyW,
    KeyX,
    KeyY,
    KeyZ,
}

/// 快捷键里的一「项」:满足它的物理键有哪些,按下任意一个就算满足。
///
/// 需要这一层是因为「Ctrl」这个说法本身就是有歧义的,而**业界通行的做法是
/// 不写边就两边都认**(Discord / OBS / 各家推话器都是这样):用户写 `ctrl+alt`,
/// 按左边右边都该响应;只有明确写了 `left_ctrl` 才只认左边。
///
/// 另外配置里一直有个 `hotkey.distinguish_left_right` 开关,但 Rust 客户端从来
/// 没读过它 —— 存了、迁移了、前端类型里也有,就是没人用。关掉它的意思就是
/// 「别分左右」,现在由这里落实。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KeySpec {
    /// 满足这一项的物理键(任一即可)。
    alts: Vec<HotkeyKey>,
}

impl KeySpec {
    fn exact(k: HotkeyKey) -> Self {
        Self { alts: vec![k] }
    }

    /// 这一项现在满足了吗:交给 `is_down` 去问每个候选键的状态。
    pub fn satisfied(&self, mut is_down: impl FnMut(HotkeyKey) -> bool) -> bool {
        self.alts.iter().copied().any(&mut is_down)
    }
}

/// 一个修饰键的左右两侧。不是修饰键就返回 None。
fn both_sides(k: HotkeyKey) -> Option<[HotkeyKey; 2]> {
    Some(match k {
        HotkeyKey::ControlLeft | HotkeyKey::ControlRight => {
            [HotkeyKey::ControlLeft, HotkeyKey::ControlRight]
        }
        HotkeyKey::ShiftLeft | HotkeyKey::ShiftRight => {
            [HotkeyKey::ShiftLeft, HotkeyKey::ShiftRight]
        }
        HotkeyKey::Alt | HotkeyKey::AltGr => [HotkeyKey::Alt, HotkeyKey::AltGr],
        _ => return None,
    })
}

/// 解析 `"left_ctrl+left_alt"` / `"ctrl+alt"` / `"capslock"` 这样的快捷键串。
///
/// `distinguish_sides` 为 false 时,写了边的修饰键也按两边都认处理
/// (对应配置里的 `hotkey.distinguish_left_right`)。不带边的写法(`ctrl`、
/// `alt`、`shift`)**无论这个开关怎样都是两边都认** —— 用户没说边,就不该替他挑一边。
pub fn parse_hotkey(s: &str, distinguish_sides: bool) -> Option<Vec<KeySpec>> {
    let tokens: Vec<&str> = s
        .split('+')
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .collect();
    if tokens.is_empty() {
        return None;
    }
    let specs: Vec<KeySpec> = tokens
        .iter()
        .filter_map(|t| {
            let sided = is_sided_token(t);
            let key = parse_key(t)?;
            // 没写边 → 永远两边都认;写了边 → 看开关。
            if !sided || !distinguish_sides {
                if let Some(pair) = both_sides(key) {
                    return Some(KeySpec {
                        alts: pair.to_vec(),
                    });
                }
            }
            Some(KeySpec::exact(key))
        })
        .collect();
    if specs.len() == tokens.len() {
        Some(specs)
    } else {
        None
    }
}

/// 这个写法有没有明确指定左右。
fn is_sided_token(token: &str) -> bool {
    let t = token.to_lowercase();
    matches!(
        t.as_str(),
        "left_ctrl"
            | "left_control"
            | "lctrl"
            | "right_ctrl"
            | "right_control"
            | "rctrl"
            | "left_alt"
            | "lalt"
            | "right_alt"
            | "ralt"
            | "left_shift"
            | "lshift"
            | "right_shift"
            | "rshift"
    )
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
        // 长度必须 >= 2。写成 `t.len() <= 3` 时,单个字母 "f" 也会进这一条:
        // `t[1..]` 是空串,parse 失败,`?` 直接让整个 parse_key 返回 None ——
        // match 的分支不会往下落,所以字母 F 永远配不出快捷键(设成 "ctrl+f"
        // 会被判成非法组合,快捷键静默失效)。
        _ if t.starts_with('f') && (2..=3).contains(&t.len()) => {
            let n: u8 = t[1..].parse().ok()?;
            if !(1..=12).contains(&n) {
                return None;
            }
            Some(match n {
                1 => HotkeyKey::F1,
                2 => HotkeyKey::F2,
                3 => HotkeyKey::F3,
                4 => HotkeyKey::F4,
                5 => HotkeyKey::F5,
                6 => HotkeyKey::F6,
                7 => HotkeyKey::F7,
                8 => HotkeyKey::F8,
                9 => HotkeyKey::F9,
                10 => HotkeyKey::F10,
                11 => HotkeyKey::F11,
                12 => HotkeyKey::F12,
                _ => return None,
            })
        }
        _ if t.len() == 1 => {
            let c = t.chars().next()?;
            let i = (c as u8).wrapping_sub(b'a') as usize;
            const LETTERS: [HotkeyKey; 26] = [
                HotkeyKey::KeyA,
                HotkeyKey::KeyB,
                HotkeyKey::KeyC,
                HotkeyKey::KeyD,
                HotkeyKey::KeyE,
                HotkeyKey::KeyF,
                HotkeyKey::KeyG,
                HotkeyKey::KeyH,
                HotkeyKey::KeyI,
                HotkeyKey::KeyJ,
                HotkeyKey::KeyK,
                HotkeyKey::KeyL,
                HotkeyKey::KeyM,
                HotkeyKey::KeyN,
                HotkeyKey::KeyO,
                HotkeyKey::KeyP,
                HotkeyKey::KeyQ,
                HotkeyKey::KeyR,
                HotkeyKey::KeyS,
                HotkeyKey::KeyT,
                HotkeyKey::KeyU,
                HotkeyKey::KeyV,
                HotkeyKey::KeyW,
                HotkeyKey::KeyX,
                HotkeyKey::KeyY,
                HotkeyKey::KeyZ,
            ];
            LETTERS.get(i).copied()
        }
        _ => None,
    }
}

/// No-op on non-Windows; press/release tracking is handled by the platform-specific
/// listener and reset is not needed.
pub fn reset_state() {}

// ── 错误上报 ──
//
// 快捷键是这个应用的主路径,可它跑在后台线程上,没有任何 Tauri command 的
// 返回值可以借。以前这里一律 `let _ =` 把 Result 丢掉,结果 `lib.rs` 里那些
// 写得很清楚的权限提示(「未获得麦克风权限。请到「系统设置 → …」」)一个字
// 都到不了用户眼前:没有 toast、没有事件、连日志都没有。用户按住快捷键,
// 什么都不发生,也不知道为什么。
//
// 下面两个函数把失败送到前端已经在监听的事件上,不必新增事件类型:
// `hotkey-release` 收掉「录音中」的状态和计时器,`transcribe-error` 关掉
// loading 并弹出错误 toast(toast 同时会进应用内的日志面板)。

/// 录音没能开起来(或中途被强行终止):把 UI 从「录音中」收回来,并把原因
/// 摆到用户面前。`hotkey-press` 已经发过了,所以必须补一个 `hotkey-release`,
/// 否则界面会永远停在录音状态、计时器一直涨。
fn report_recording_aborted(app: &tauri::AppHandle, err: &str) {
    crate::log_error!("[hotkey] 录音中止:{}", err);
    // 悬浮胶囊只在 stop 的转录任务里关。安全超时那条路不走 stop,胶囊会一直
    // 挂在屏幕上。窗口不存在时 hide 自身是空操作,启动失败那条路调它也无害。
    let _ = crate::indicator::hide(app);
    // 顺序有讲究:先 release(停计时器、进 loading),再报错(关 loading)。
    // 用户手指真正松开时监听线程还会再发一次 hotkey-release —— 前端那边有
    // 「不在录音状态就忽略」的判断,所以这里抢先补发是安全的。
    let _ = app.emit("hotkey-release", ());
    let _ = app.emit("transcribe-error", err.to_string());
}

/// 结束录音失败。这时 `hotkey-release` 已经发过,UI 停在「识别中」,
/// `transcribe-error` 会把它收掉并弹出原因。
fn report_stop_failed(app: &tauri::AppHandle, err: &str) {
    crate::log_error!("[hotkey] 结束录音失败:{}", err);
    let _ = app.emit("transcribe-error", err.to_string());
}

// ── Windows implementation: pure GetAsyncKeyState polling ──

#[cfg(target_os = "windows")]
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<KeySpec>) {
    if hotkey_keys.is_empty() {
        return;
    }

    // Bump generation so any previously spawned poller threads will exit.
    let my_gen = LISTENER_GEN.fetch_add(1, Ordering::SeqCst) + 1;

    let _ = std::thread::Builder::new()
        .name("hotkey-poller".into())
        .spawn(move || {
            eprintln!(
                "[hotkey] Starting GetAsyncKeyState poller for {} keys",
                hotkey_keys.len()
            );

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

                // 每一项只要有一个候选键按下就算满足(不分左右时 Ctrl 有两个候选)。
                let all_down = hotkey_keys
                    .iter()
                    .all(|spec| spec.satisfied(|k| win_key_down(&k)));

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
                                crate::start_recording_internal(&app, &state)
                            }));
                            match r {
                                Err(_) => {
                                    eprintln!("[hotkey] start_recording_internal panic");
                                    recording.store(false, Ordering::SeqCst);
                                    record_start = None;
                                    report_recording_aborted(&app, "录音启动时发生内部错误。");
                                }
                                Ok(Err(e)) => {
                                    // 权限被拒、设备打不开之类:必须说出来。
                                    // 键还按着也不会刷屏 —— 触发只认上升沿,
                                    // `all_down_start` 已经清成 None 了。
                                    recording.store(false, Ordering::SeqCst);
                                    record_start = None;
                                    report_recording_aborted(&app, &e);
                                }
                                Ok(Ok(())) => {}
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

                            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                let state = app.state::<AppState>();
                                crate::stop_recording_internal(&app, &state)
                            }));
                            match r {
                                Err(_) => report_stop_failed(&app, "结束录音时发生内部错误。"),
                                Ok(Err(e)) => report_stop_failed(&app, &e),
                                Ok(Ok(_)) => {}
                            }
                        }
                    }
                }

                // ── Safety timeout: force-stop if stuck recording ──
                if recording.load(Ordering::SeqCst) {
                    if let Some(start) = record_start {
                        if start.elapsed() >= Duration::from_secs(MAX_RECORD_SECS) {
                            eprintln!(
                                "[hotkey] Safety timeout: force-stopping recording after {}s",
                                MAX_RECORD_SECS
                            );
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
                            // 录音机复位了,可前端还停在「录音中」:没有 hotkey-release
                            // 就没人去清 recording / 计时器,界面会一直转,连录音按钮
                            // 都被 startRecord 的状态锁卡死,直到重启应用。
                            report_recording_aborted(
                                &app,
                                "录音超过 5 分钟,已自动停止(这段音频未转录)。",
                            );
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

    // Safety timeout: force-stop recording if running longer than this
    // (mirrors the Windows poller, which already had one).
    const MAX_RECORD_SECS: u64 = 300; // 5 minutes

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
                    // Re-check after the (blocking) recv: a superseded worker
                    // must not act on a command from its stale listener.
                    if LISTENER_GEN.load(Ordering::SeqCst) != my_gen {
                        eprintln!("[hotkey] Worker gen {} superseded, exiting", my_gen);
                        break;
                    }
                    if recording.swap(true, Ordering::SeqCst) {
                        continue;
                    }
                    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        let state = app.state::<crate::AppState>();
                        crate::start_recording_internal(&app, &state)
                    }));
                    match r {
                        Err(_) => {
                            eprintln!("[hotkey] start_recording panic");
                            recording.store(false, Ordering::SeqCst);
                            report_recording_aborted(&app, "录音启动时发生内部错误。");
                            continue;
                        }
                        Ok(Err(e)) => {
                            // 权限被拒、设备打不开之类:必须说出来。
                            recording.store(false, Ordering::SeqCst);
                            report_recording_aborted(&app, &e);
                            continue;
                        }
                        Ok(Ok(())) => {}
                    }
                    let record_start = std::time::Instant::now();
                    let mut timed_out = false;
                    'record: loop {
                        match cmd_rx.try_recv() {
                            Ok(_) => break 'record,
                            Err(std::sync::mpsc::TryRecvError::Disconnected) => break 'record,
                            Err(std::sync::mpsc::TryRecvError::Empty) => {}
                        }
                        if record_start.elapsed() >= std::time::Duration::from_secs(MAX_RECORD_SECS)
                        {
                            eprintln!(
                                "[hotkey] Safety timeout: force-stopping recording after {}s",
                                MAX_RECORD_SECS
                            );
                            timed_out = true;
                            break 'record;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    recording.store(false, Ordering::SeqCst);
                    if timed_out {
                        // Drop the buffer instead of transcribing 5 min of audio.
                        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            // Bind in separate lets to ensure proper drop order
                            let state = app.state::<crate::AppState>();
                            let result = state.recorder.lock();
                            if let Ok(mut guard) = result {
                                guard.reset();
                            }
                        }));
                        // 录音机复位了,可前端还停在「录音中」:没有 hotkey-release
                        // 就没人去清 recording / 计时器,界面会一直转,连录音按钮
                        // 都被 startRecord 的状态锁卡死,直到重启应用。
                        report_recording_aborted(
                            &app,
                            "录音超过 5 分钟,已自动停止(这段音频未转录)。",
                        );
                        continue;
                    }
                    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        let state = app.state::<crate::AppState>();
                        crate::stop_recording_internal(&app, &state)
                    }));
                    match r {
                        Err(_) => report_stop_failed(&app, "结束录音时发生内部错误。"),
                        Ok(Err(e)) => report_stop_failed(&app, &e),
                        Ok(Ok(_)) => {}
                    }
                }
                // 落单的 Release(比如上一次 Press 因为权限失败提前收了尾)不该
                // 让 worker 退出 —— 一退出快捷键就彻底哑了,要等重新注册才复活。
                Ok(HotkeyCmd::Release) => continue,
                Err(_) => break,
            }
        });
}

/// Shared press/release bookkeeping + hotkey-match → worker channel.
/// Returns the emitted channel command, if any.
#[cfg(not(target_os = "windows"))]
struct HotkeyMatcher {
    pressed: Vec<HotkeyKey>,
    keys: Vec<KeySpec>,
    /// True between an emitted Press and its matching Release. Pairing
    /// Press/Release explicitly (instead of suppressing releases that arrive
    /// within a time window) keeps a quick tap from losing its Release and
    /// leaving the recorder stuck on, and swallows key-autorepeat Presses.
    press_active: bool,
}

#[cfg(not(target_os = "windows"))]
impl HotkeyMatcher {
    fn new(keys: Vec<KeySpec>) -> Self {
        Self {
            pressed: Vec::new(),
            keys,
            press_active: false,
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
        let all_pressed = self
            .keys
            .iter()
            .all(|spec| spec.satisfied(|k| self.pressed.contains(&k)));
        if all_pressed && !self.press_active {
            self.press_active = true;
            return Some(HotkeyCmd::Press);
        }
        if !all_pressed && self.press_active {
            self.press_active = false;
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
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<KeySpec>) {
    let my_gen = LISTENER_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    let (cmd_tx, cmd_rx) = std::sync::mpsc::channel::<HotkeyCmd>();
    spawn_hotkey_worker(app.clone(), cmd_rx, my_gen);

    let _ = std::thread::Builder::new()
        .name("hotkey-listener".into())
        .spawn(move || {
            let mut matcher = HotkeyMatcher::new(hotkey_keys.clone());
            let a = app.clone();
            mac_tap::run(move |key, is_press| {
                // A re-registration spawns a new tap; this (now stale) one must
                // stop, or both taps fire and recording starts twice.
                if LISTENER_GEN.load(Ordering::SeqCst) != my_gen {
                    eprintln!("[hotkey] Tap gen {} superseded, stopping runloop", my_gen);
                    return mac_tap::TapAction::Stop;
                }
                if let Some(cmd) = matcher.on_change(key, is_press) {
                    let _ = cmd_tx.send(cmd);
                    let _ = a.emit(
                        if is_press {
                            "hotkey-press"
                        } else {
                            "hotkey-release"
                        },
                        (),
                    );
                }
                mac_tap::TapAction::Continue
            });
        });
}

#[cfg(target_os = "macos")]
mod mac_tap {
    use super::*;
    use core_foundation::runloop::*;
    use core_graphics::event::{
        CGEventFlags, CGEventTap, CGEventTapLocation, CGEventTapOptions, CGEventTapPlacement,
        CGEventType, EventField,
    };

    // macOS HID keycode → HotkeyKey (ANSI layout; letters A=0x00, not Windows VK).
    fn hid_to_hotkey(vk: i64) -> Option<HotkeyKey> {
        Some(match vk {
            // 修饰键(flagsChanged 事件经 mod_flag + flags 判断按下/释放)
            0x3B => HotkeyKey::ControlLeft,
            0x3E => HotkeyKey::ControlRight,
            0x3A => HotkeyKey::Alt,
            0x3D => HotkeyKey::AltGr,
            0x38 => HotkeyKey::ShiftLeft,
            0x3C => HotkeyKey::ShiftRight,
            // 0x37/0x36 (Command) 未在 HotkeyKey 枚举中,不支持
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

    // FlagsChanged 事件带的是「哪个修饰键变了」的 keycode,按下还是抬起要看
    // CGEventGetFlags 里对应的位还在不在。
    //
    // 但**不能只看设备无关位**(CGEventFlagControl 之类):那一位左右共用。
    // 左右 Ctrl 同时按住、再松开其中一个时,Control 位仍然亮着,于是那次抬起会
    // 被读成「按下」,那个键就永远留在 `pressed` 里 —— 快捷键从此卡住。
    //
    // macOS 另有一组**设备相关**位能分左右(IOLLEvent.h 里的 NX_DEVICE*KEYMASK),
    // 优先用它们;真实键盘事件都会带。个别合成事件不带,那时回落到设备无关位,
    // 也就是改动前的行为,不会更坏。
    const NX_DEVICE_LCTL: u64 = 0x0000_0001;
    const NX_DEVICE_RCTL: u64 = 0x0000_2000;
    const NX_DEVICE_LSHIFT: u64 = 0x0000_0002;
    const NX_DEVICE_RSHIFT: u64 = 0x0000_0004;
    const NX_DEVICE_LALT: u64 = 0x0000_0020;
    const NX_DEVICE_RALT: u64 = 0x0000_0040;

    /// 这个修饰键现在是按下状态吗。
    ///
    /// `side` 是它自己那一侧的设备位,`family` 是左右两侧的并集,
    /// `fallback` 是左右共用的设备无关位。
    fn mod_is_down(vk: i64, flags: CGEventFlags) -> Option<bool> {
        let bits = flags.bits();
        let (side, family, fallback) = match vk {
            0x3B => (
                NX_DEVICE_LCTL,
                NX_DEVICE_LCTL | NX_DEVICE_RCTL,
                CGEventFlags::CGEventFlagControl,
            ),
            0x3E => (
                NX_DEVICE_RCTL,
                NX_DEVICE_LCTL | NX_DEVICE_RCTL,
                CGEventFlags::CGEventFlagControl,
            ),
            0x38 => (
                NX_DEVICE_LSHIFT,
                NX_DEVICE_LSHIFT | NX_DEVICE_RSHIFT,
                CGEventFlags::CGEventFlagShift,
            ),
            0x3C => (
                NX_DEVICE_RSHIFT,
                NX_DEVICE_LSHIFT | NX_DEVICE_RSHIFT,
                CGEventFlags::CGEventFlagShift,
            ),
            0x3A => (
                NX_DEVICE_LALT,
                NX_DEVICE_LALT | NX_DEVICE_RALT,
                CGEventFlags::CGEventFlagAlternate,
            ),
            0x3D => (
                NX_DEVICE_RALT,
                NX_DEVICE_LALT | NX_DEVICE_RALT,
                CGEventFlags::CGEventFlagAlternate,
            ),
            // Caps Lock 没有左右之分,也没有物理按下/抬起可言:它只有「灯亮着
            // 没有」这一个状态,按一下亮、再按一下灭。所以用它当快捷键在 macOS 上
            // 是**按一下开始、再按一下结束**,而不是别的键那样按住说话。
            //
            // 以前这里根本没有 0x39 这一条,于是 FlagsChanged 分支要求
            // (mod_flag, hid_to_hotkey) 都是 Some 才处理,Caps Lock 被整个忽略
            // —— parse_key 明明认 "capslock",设了却什么都不会发生。
            0x39 => return Some(flags.contains(CGEventFlags::CGEventFlagAlphaShift)),
            _ => return None,
        };
        Some(if bits & family != 0 {
            bits & side != 0
        } else {
            flags.contains(fallback)
        })
    }

    /// What the handler wants the tap to do next.
    pub enum TapAction {
        Continue,
        /// Stop the runloop and drop the tap (used when superseded).
        Stop,
    }

    /// Run a CGEventTap on the current thread (called from the
    /// hotkey-listener thread). Never touches TextServices, so it does not
    /// hit the macOS 15+ TSMGetInputSourceProperty assert crash that rdev
    /// does. Requires Input Monitoring / Accessibility permission;
    /// otherwise CGEventTapCreate fails and we log and return.
    pub fn run<F>(handler: F)
    where
        F: FnMut(HotkeyKey, bool) -> TapAction + 'static,
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
                let action = match etype {
                    CGEventType::KeyDown | CGEventType::KeyUp => {
                        let vk = event.get_integer_value_field(EventField::KEYBOARD_EVENT_KEYCODE);
                        match hid_to_hotkey(vk) {
                            Some(k) => h(k, matches!(etype, CGEventType::KeyDown)),
                            None => TapAction::Continue,
                        }
                    }
                    CGEventType::FlagsChanged => {
                        let vk = event.get_integer_value_field(EventField::KEYBOARD_EVENT_KEYCODE);
                        let flags = event.get_flags();
                        match (mod_is_down(vk, flags), hid_to_hotkey(vk)) {
                            (Some(is_down), Some(k)) => h(k, is_down),
                            _ => TapAction::Continue,
                        }
                    }
                    _ => TapAction::Continue,
                };
                if matches!(action, TapAction::Stop) {
                    CFRunLoop::get_current().stop();
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
            Err(_) => {
                // 几乎总是因为缺「输入监控」权限,把当前状态一并打出来,
                // 免得用户只看到一句语焉不详的失败。
                crate::log_error!(
                    "[hotkey] CGEventTapCreate 失败,全局快捷键不可用(输入监控权限={:?})",
                    crate::permissions::input_monitoring_status()
                );
            }
        }
    }
}

// ── Linux (and other non-Windows/non-macOS): rdev listener ──

#[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
pub fn start_listener(app: tauri::AppHandle, hotkey_keys: Vec<KeySpec>) {
    use std::sync::mpsc;

    let my_gen = LISTENER_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    let (cmd_tx, cmd_rx) = mpsc::channel::<HotkeyCmd>();
    spawn_hotkey_worker(app.clone(), cmd_rx, my_gen);

    let _ = std::thread::Builder::new()
        .name("hotkey-listener".into())
        .spawn(move || {
            let mut matcher = HotkeyMatcher::new(hotkey_keys.clone());
            let a = app.clone();

            // rdev 在 Linux 上走的是 X11。Wayland 会话里拿不到全局按键
            // (Wayland 的设计就是不让普通客户端窥探别的窗口的输入),
            // `listen` 要么直接失败,要么装上去却一个事件都收不到。
            //
            // 以前这里是 `let _ = rdev::listen(...)`,失败被整个丢掉:用户看到的
            // 是快捷键毫无反应,日志里一个字都没有,无从查起。至少要说出来。
            if let Some(kind) = wayland_session() {
                crate::log_error!(
                    "[hotkey] 检测到 {} 会话。全局快捷键依赖 X11,在 Wayland 下拿不到\
                     其它窗口的按键,多半不会工作。可改用 Xorg 会话登录,或在界面上\
                     按住录音按钮说话。",
                    kind
                );
            }
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                rdev::listen(move |event: rdev::Event| {
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
                            if is_press {
                                "hotkey-press"
                            } else {
                                "hotkey-release"
                            },
                            (),
                        );
                    }
                })
            }));
            match result {
                Ok(Err(e)) => crate::log_error!(
                    "[hotkey] 全局按键监听启动失败,快捷键不可用: {:?}。\
                     Linux 上常见原因:Wayland 会话,或当前用户不在 input 组\
                     (`sudo usermod -aG input $USER` 后重新登录)。",
                    e
                ),
                Err(_) => crate::log_error!("[hotkey] 全局按键监听线程 panic,快捷键不可用"),
                // listen() 正常情况下不会返回;真返回了说明监听结束了。
                Ok(Ok(())) => crate::log_error!("[hotkey] 全局按键监听已结束,快捷键不再工作"),
            }
        });
}

/// 当前是不是 Wayland 会话。是的话返回它的名字,好原样写进日志。
///
/// 在 Ubuntu 24.04 上实测过五种环境:`XDG_SESSION_TYPE` 为 tty / x11 时返回
/// None,为 wayland / Wayland(大小写不敏感)时返回 Some;只要 `WAYLAND_DISPLAY`
/// 有值就算 Wayland,哪怕 `XDG_SESSION_TYPE` 说的是别的。
///
/// 同一台机器上还确认了:拿不到显示服务时 `rdev::listen` 是**很快返回
/// `Err(KeyboardError)`**(实测 4.5ms),不是卡住不返回 —— 所以下面那段错误
/// 提示真的会打印出来,不会石沉大海。
#[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
fn wayland_session() -> Option<&'static str> {
    if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        return Some("Wayland");
    }
    match std::env::var("XDG_SESSION_TYPE") {
        Ok(t) if t.eq_ignore_ascii_case("wayland") => Some("Wayland"),
        _ => None,
    }
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
    let Some(vk) = hotkey_to_vk(k) else {
        return false;
    }; // unknown key = assume NOT pressed
       // GetAsyncKeyState returns SHORT (i16). MSB=0x8000 means key is down.
       // Cast to u16 to avoid literal out of range for i16.
    unsafe { (GetAsyncKeyState(vk) as u16) & 0x8000u16 != 0 }
}

/// Maps HotkeyKey → Windows Virtual-Key code.
#[cfg(target_os = "windows")]
fn hotkey_to_vk(k: &HotkeyKey) -> Option<i32> {
    Some(match k {
        // VK_LMENU(左 Alt),**不是** VK_MENU(0x12,任意 Alt)。
        //
        // 用 VK_MENU 会让默认快捷键 left_ctrl+left_alt 在欧洲键盘布局上乱触发:
        // Windows 上按 AltGr 会**同时合成一个左 Ctrl 按下**再加右 Alt,于是
        // VK_LCONTROL 和 VK_MENU 同时为真 —— 德语/法语/波兰语等布局的用户每打一个
        // @ € { } 都会开始录音。换成 VK_LMENU 后右 Alt 不再满足条件。
        HotkeyKey::Alt => 0xA4,          // VK_LMENU
        HotkeyKey::AltGr => 0xA5,        // VK_RMENU
        HotkeyKey::ControlLeft => 0xA2,  // VK_LCONTROL
        HotkeyKey::ControlRight => 0xA3, // VK_RCONTROL
        HotkeyKey::ShiftLeft => 0xA0,    // VK_LSHIFT
        HotkeyKey::ShiftRight => 0xA1,   // VK_RSHIFT
        HotkeyKey::CapsLock => 0x14,     // VK_CAPITAL
        HotkeyKey::Space => 0x20,        // VK_SPACE
        HotkeyKey::Return => 0x0D,       // VK_RETURN
        HotkeyKey::Tab => 0x09,          // VK_TAB
        HotkeyKey::Escape => 0x1B,       // VK_ESCAPE
        HotkeyKey::Delete => 0x2E,       // VK_DELETE
        HotkeyKey::Backspace => 0x08,    // VK_BACK
        HotkeyKey::F1 => 0x70,
        HotkeyKey::F2 => 0x71,
        HotkeyKey::F3 => 0x72,
        HotkeyKey::F4 => 0x73,
        HotkeyKey::F5 => 0x74,
        HotkeyKey::F6 => 0x75,
        HotkeyKey::F7 => 0x76,
        HotkeyKey::F8 => 0x77,
        HotkeyKey::F9 => 0x78,
        HotkeyKey::F10 => 0x79,
        HotkeyKey::F11 => 0x7A,
        HotkeyKey::F12 => 0x7B,
        HotkeyKey::KeyA => 0x41,
        HotkeyKey::KeyB => 0x42,
        HotkeyKey::KeyC => 0x43,
        HotkeyKey::KeyD => 0x44,
        HotkeyKey::KeyE => 0x45,
        HotkeyKey::KeyF => 0x46,
        HotkeyKey::KeyG => 0x47,
        HotkeyKey::KeyH => 0x48,
        HotkeyKey::KeyI => 0x49,
        HotkeyKey::KeyJ => 0x4A,
        HotkeyKey::KeyK => 0x4B,
        HotkeyKey::KeyL => 0x4C,
        HotkeyKey::KeyM => 0x4D,
        HotkeyKey::KeyN => 0x4E,
        HotkeyKey::KeyO => 0x4F,
        HotkeyKey::KeyP => 0x50,
        HotkeyKey::KeyQ => 0x51,
        HotkeyKey::KeyR => 0x52,
        HotkeyKey::KeyS => 0x53,
        HotkeyKey::KeyT => 0x54,
        HotkeyKey::KeyU => 0x55,
        HotkeyKey::KeyV => 0x56,
        HotkeyKey::KeyW => 0x57,
        HotkeyKey::KeyX => 0x58,
        HotkeyKey::KeyY => 0x59,
        HotkeyKey::KeyZ => 0x5A,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 把解析结果摊平成「每一项的候选键」,方便断言。
    fn alts(s: &str, distinguish: bool) -> Option<Vec<Vec<HotkeyKey>>> {
        parse_hotkey(s, distinguish).map(|v| v.into_iter().map(|k| k.alts).collect())
    }

    #[test]
    fn single_letters_parse_including_f() {
        // 回归:"f" 以前会被 F1–F12 那条分支吃掉(`t[1..]` 是空串,parse 失败,
        // `?` 让整个 parse_key 返回 None,match 又不会往下落),于是字母 F
        // 永远配不出快捷键 —— 设成 "ctrl+f" 会被判成非法组合,静默失效。
        assert_eq!(alts("f", true), Some(vec![vec![HotkeyKey::KeyF]]));
        assert_eq!(
            alts("left_ctrl+f", true),
            Some(vec![vec![HotkeyKey::ControlLeft], vec![HotkeyKey::KeyF]])
        );
        for c in 'a'..='z' {
            let parsed = parse_hotkey(&c.to_string(), true);
            assert!(parsed.is_some(), "字母 {} 解析失败", c);
            assert_eq!(parsed.unwrap().len(), 1, "字母 {} 解析出多个键", c);
        }
    }

    #[test]
    fn function_keys_parse() {
        assert_eq!(alts("f1", true), Some(vec![vec![HotkeyKey::F1]]));
        assert_eq!(alts("f12", true), Some(vec![vec![HotkeyKey::F12]]));
        assert_eq!(alts("f13", true), None);
        assert_eq!(alts("f0", true), None);
    }

    #[test]
    fn invalid_combos_are_rejected_whole() {
        // 别把 "ctrl+nosuchkey" 悄悄降级成 "ctrl"
        assert_eq!(alts("left_ctrl+nosuchkey", true), None);
        assert_eq!(alts("", true), None);
        assert_eq!(alts("+", true), None);
    }

    /// 不写边就两边都认 —— 业界通行做法(Discord / OBS 等推话器都是这样)。
    /// 用户写 `ctrl`,按左右哪个都该响应;`distinguish_left_right` 也管不着它,
    /// 因为用户压根没说边,不该替他挑一边。
    #[test]
    fn bare_modifier_matches_either_side() {
        for distinguish in [true, false] {
            assert_eq!(
                alts("ctrl", distinguish),
                Some(vec![vec![HotkeyKey::ControlLeft, HotkeyKey::ControlRight]]),
                "distinguish={}",
                distinguish
            );
            assert_eq!(
                alts("alt", distinguish),
                Some(vec![vec![HotkeyKey::Alt, HotkeyKey::AltGr]]),
                "distinguish={}",
                distinguish
            );
            assert_eq!(
                alts("shift", distinguish),
                Some(vec![vec![HotkeyKey::ShiftLeft, HotkeyKey::ShiftRight]]),
                "distinguish={}",
                distinguish
            );
        }
    }

    /// 写了边时,`distinguish_left_right` 才起作用。这个开关配置里一直有,
    /// 但 Rust 客户端从来没读过 —— 关掉它以前完全没有效果。
    #[test]
    fn sided_modifier_honors_the_distinguish_setting() {
        assert_eq!(
            alts("left_ctrl", true),
            Some(vec![vec![HotkeyKey::ControlLeft]]),
            "开着分左右时,left_ctrl 只认左边"
        );
        assert_eq!(
            alts("left_ctrl", false),
            Some(vec![vec![HotkeyKey::ControlLeft, HotkeyKey::ControlRight]]),
            "关掉分左右时,left_ctrl 两边都认"
        );
        // 默认快捷键在两种设置下的完整形态
        assert_eq!(
            alts("left_ctrl+left_alt", true),
            Some(vec![vec![HotkeyKey::ControlLeft], vec![HotkeyKey::Alt]])
        );
        assert_eq!(
            alts("left_ctrl+left_alt", false),
            Some(vec![
                vec![HotkeyKey::ControlLeft, HotkeyKey::ControlRight],
                vec![HotkeyKey::Alt, HotkeyKey::AltGr],
            ])
        );
    }

    /// 非修饰键没有左右之分,开关不该影响它们。
    #[test]
    fn non_modifiers_are_unaffected_by_the_setting() {
        for distinguish in [true, false] {
            assert_eq!(
                alts("space", distinguish),
                Some(vec![vec![HotkeyKey::Space]])
            );
            assert_eq!(
                alts("capslock", distinguish),
                Some(vec![vec![HotkeyKey::CapsLock]])
            );
            assert_eq!(alts("f5", distinguish), Some(vec![vec![HotkeyKey::F5]]));
        }
    }

    #[test]
    fn key_spec_is_satisfied_by_any_alternative() {
        let spec = &parse_hotkey("ctrl", true).unwrap()[0];
        assert!(spec.satisfied(|k| k == HotkeyKey::ControlLeft));
        assert!(spec.satisfied(|k| k == HotkeyKey::ControlRight));
        assert!(!spec.satisfied(|k| k == HotkeyKey::ShiftLeft));

        let strict = &parse_hotkey("left_ctrl", true).unwrap()[0];
        assert!(strict.satisfied(|k| k == HotkeyKey::ControlLeft));
        assert!(
            !strict.satisfied(|k| k == HotkeyKey::ControlRight),
            "明确写了 left_ctrl 就不该被右 Ctrl 满足"
        );
    }

    /// `left_alt` 在 Windows 上必须映射到 VK_LMENU(0xA4),不能是 VK_MENU(0x12)。
    /// 用 VK_MENU 的话,AltGr 会同时点亮 VK_LCONTROL 和 VK_MENU,默认快捷键
    /// left_ctrl+left_alt 在欧洲布局上每打一个 @ € { } 都会误触发。
    #[cfg(target_os = "windows")]
    #[test]
    fn left_alt_maps_to_left_menu_not_any_menu() {
        assert_eq!(hotkey_to_vk(&HotkeyKey::Alt), Some(0xA4));
        assert_eq!(hotkey_to_vk(&HotkeyKey::AltGr), Some(0xA5));
        assert_ne!(hotkey_to_vk(&HotkeyKey::Alt), Some(0x12));
    }
}
