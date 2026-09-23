//! 系统托盘:状态行、复制最近一条结果、显示窗口、设置、检查更新、退出。
//!
//! 以前只有「显示窗口 / 退出」。旧 Python 客户端的托盘有状态、检查更新这些,
//! 迁移时丢了;前端一直监听着 `tray-check-update`,却没有任何地方发它。
//! 本应用平时藏在后台,托盘是除了快捷键之外唯一摸得到的入口,该放的东西得放上。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, Wry,
};
use tauri_plugin_clipboard_manager::ClipboardExt;

/// 托盘有没有建成。建不成(典型:Linux 缺 AppIndicator 库)时,主窗口就是用户
/// 唯一能摸到的东西,不能再把它藏起来——见 `lib.rs` 里启动最小化和关窗的处理。
static TRAY_OK: AtomicBool = AtomicBool::new(false);

/// 最近一条转写结果。存在 Rust 这边是因为托盘菜单点的时候窗口多半藏着,
/// 不能指望前端来回答。
static LAST_RESULT: Mutex<String> = Mutex::new(String::new());

/// 菜单里需要在运行时改文字 / 启用状态的那几项。
struct TrayItems {
    status: MenuItem<Wry>,
    copy_last: MenuItem<Wry>,
}

/// 托盘是否可用。
pub fn available() -> bool {
    TRAY_OK.load(Ordering::SeqCst)
}

/// 菜单项里显示的结果预览:按字符截断(中文按字节截会切在半个字上直接 panic),
/// 换行压成空格,免得菜单项撑成好几行。
fn preview(text: &str, max_chars: usize) -> String {
    let flat: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= max_chars {
        flat
    } else {
        let mut s: String = flat.chars().take(max_chars).collect();
        s.push('…');
        s
    }
}

pub fn setup(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    // 状态行只用来看,不能点。文字由前端按连接状态推过来(`set_status`)。
    let status = MenuItem::with_id(app, "status", "启动中…", false, None::<&str>)?;
    // 还没有结果时置灰,而不是点了没反应。
    let copy_last = MenuItem::with_id(app, "copy_last", "复制最近一条结果", false, None::<&str>)?;
    let show_item = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
    let settings_item = MenuItem::with_id(app, "settings", "设置…", true, None::<&str>)?;
    let update_item = MenuItem::with_id(app, "check_update", "检查更新", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "退出", true, Some("CmdOrCtrl+Q"))?;

    let menu = Menu::with_items(
        app,
        &[
            &status,
            &PredefinedMenuItem::separator(app)?,
            &copy_last,
            &PredefinedMenuItem::separator(app)?,
            &show_item,
            &settings_item,
            &update_item,
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
        ],
    )?;

    let mut builder = TrayIconBuilder::new().menu(&menu);
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
    }
    builder
        .tooltip("Voice Input")
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                crate::show_main_window(app);
            }
            "copy_last" => {
                let text = LAST_RESULT.lock().map(|t| t.clone()).unwrap_or_default();
                if text.is_empty() {
                    return;
                }
                if let Err(e) = app.clipboard().write_text(text) {
                    crate::log_error!("[tray] 复制最近一条结果失败: {}", e);
                }
            }
            "settings" => {
                crate::show_main_window(app);
                let _ = app.emit("tray-open-settings", ());
            }
            "check_update" => {
                // 检查结果显示在设置面板的「关于」页,窗口藏着的话用户看不到。
                crate::show_main_window(app);
                let _ = app.emit("tray-check-update", ());
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                crate::show_main_window(tray.app_handle());
            }
        })
        .build(app)?;

    app.manage(TrayItems { status, copy_last });
    TRAY_OK.store(true, Ordering::SeqCst);
    Ok(())
}

/// 更新托盘里的状态行。托盘没建成时什么也不做。
pub fn set_status(app: &tauri::AppHandle, text: &str) {
    if let Some(items) = app.try_state::<TrayItems>() {
        let _ = items.status.set_text(text);
    }
}

/// 记下最近一条转写结果,并让托盘里的「复制」项可点、带上预览。
pub fn remember_result(app: &tauri::AppHandle, text: &str) {
    if text.trim().is_empty() {
        return;
    }
    if let Ok(mut last) = LAST_RESULT.lock() {
        *last = text.to_string();
    }
    if let Some(items) = app.try_state::<TrayItems>() {
        let _ = items
            .copy_last
            .set_text(format!("复制最近一条:{}", preview(text, 16)));
        let _ = items.copy_last.set_enabled(true);
    }
}

#[cfg(test)]
mod tests {
    use super::preview;

    #[test]
    fn short_text_is_kept_whole() {
        assert_eq!(preview("你好世界", 16), "你好世界");
    }

    /// 按字符截,不是按字节:中文一个字三个字节,按字节截会切在字中间。
    #[test]
    fn long_chinese_text_is_cut_on_char_boundary() {
        let text = "今天天气很好我们一起去公园散步然后吃饭";
        let p = preview(text, 8);
        assert_eq!(p, "今天天气很好我们…");
    }

    #[test]
    fn newlines_are_flattened() {
        assert_eq!(preview("第一行\n第二行\t  尾", 16), "第一行 第二行 尾");
    }
}
