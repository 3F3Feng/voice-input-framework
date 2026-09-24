//! Auto-input: type text into the active window using keyboard simulation.
//! Uses the `enigo` crate for cross-platform keyboard input.
//!
//! 这里的函数**只能在主线程上调**(调用方用 `lib.rs` 的 `on_main_thread`):
//! macOS 上 enigo 查键盘布局走 `TSMGetInputSourceProperty`,在别的线程上调会 SIGTRAP。

use enigo::{Direction, Enigo, Key, Keyboard};

use crate::i18n::t;
use crate::tr;

/// Type text into the currently focused window.
pub fn type_text(text: &str) -> Result<(), String> {
    if text.is_empty() {
        return Ok(());
    }

    // macOS:没有「辅助功能」权限时 enigo 投出的 CGEvent 会被系统直接丢弃,
    // text() 仍然返回 Ok——转录成功、日志干净,但目标窗口里什么都没出现。
    // 这里提前拦截,把静默失败变成明确的错误。
    check_accessibility_permission()?;

    let mut enigo = Enigo::new(&Default::default()).map_err(|e| {
        tr!(
            "创建键盘模拟器失败: {}",
            "Couldn't create the keyboard simulator: {}",
            e
        )
    })?;

    // Type the text character by character using enigo's text() method.
    // This simulates real keyboard input to the active window.
    enigo
        .text(text)
        .map_err(|e| tr!("模拟输入失败: {}", "Simulated typing failed: {}", e))?;

    Ok(())
}

/// 模拟一次「粘贴」快捷键(macOS 上 Cmd+V,其它平台 Ctrl+V)。
///
/// 剪贴板由调用方先写好、事后还原(见 `lib.rs` 的 `deliver_text`)。
pub fn press_paste() -> Result<(), String> {
    check_accessibility_permission()?;
    let mut enigo = Enigo::new(&Default::default()).map_err(|e| {
        tr!(
            "创建键盘模拟器失败: {}",
            "Couldn't create the keyboard simulator: {}",
            e
        )
    })?;
    #[cfg(target_os = "macos")]
    let modifier = Key::Meta;
    #[cfg(not(target_os = "macos"))]
    let modifier = Key::Control;
    enigo
        .key(modifier, Direction::Press)
        .map_err(|e| tr!("模拟粘贴失败: {}", "Simulated paste failed: {}", e))?;
    let clicked = enigo.key(Key::Unicode('v'), Direction::Click);
    // 无论点 V 成没成,修饰键都得松开,否则用户的 Cmd / Ctrl 会一直「按着」。
    let released = enigo.key(modifier, Direction::Release);
    clicked.map_err(|e| tr!("模拟粘贴失败: {}", "Simulated paste failed: {}", e))?;
    released.map_err(|e| tr!("模拟粘贴失败: {}", "Simulated paste failed: {}", e))?;
    Ok(())
}

/// 自动输入前的辅助功能权限闸门。
///
/// 第一次发现没有权限时弹一次系统引导窗口(这正是用户真正需要它的时刻),
/// 之后只返回错误,不再反复弹窗骚扰。非 macOS 平台永远放行。
fn check_accessibility_permission() -> Result<(), String> {
    if crate::permissions::accessibility_status().is_granted() {
        return Ok(());
    }

    // 只弹一次:每转录一句就弹一次窗会让人抓狂。
    static PROMPTED: std::sync::Once = std::sync::Once::new();
    PROMPTED.call_once(crate::permissions::request_accessibility);

    Err(t(
        "未获得「辅助功能」权限,无法把文字输入到其他窗口。请到「系统设置 → 隐私与安全性 → 辅助功能」中勾选 Voice Input(改动后可能需要重启本应用)。",
        "No Accessibility permission, so text can't be typed into other windows. Turn on Voice Input in System Settings → Privacy & Security → Accessibility (you may need to restart the app afterwards).",
    )
    .to_string())
}
