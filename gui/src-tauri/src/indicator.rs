//! Floating recording indicator overlay at bottom center of screen.

use tauri::{window::Color, Emitter, Manager, WebviewWindowBuilder};

pub const INDICATOR_LABEL: &str = "indicator";

pub fn show(app: &tauri::AppHandle) -> Result<(), String> {
    // 复用已存在的窗口:close 是异步的,close 后立刻同 label 重建会
    // "window already exists" 静默失败 → 胶囊不显示。复用 + 重置页面。
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.show();
        let _ = window.emit("indicator-reset", ());
        return Ok(());
    }
    let (sw, sh) = screen_center_bottom(app);
    let x = sw - 110;
    let y = sh - 100;

    let builder = WebviewWindowBuilder::new(
        app,
        INDICATOR_LABEL,
        tauri::WebviewUrl::App("indicator.html".into()),
    )
    .inner_size(210.0, 44.0)
    .position(x as f64, y as f64)
    .decorations(false)
    .always_on_top(true)
    .skip_taskbar(true)
    .resizable(false)
    .shadow(false)
    .title("");

    // macOS 用不透明深色窗口:透明窗口(Tauri 2 + WKWebView)内容经常
    // 不渲染(已知问题),导致全透明空窗口完全不可见。胶囊 HTML 的背景
    // 色与窗口背景色一致(rgb(15,15,25)),视觉上等同圆角胶囊。
    #[cfg(target_os = "macos")]
    let window = builder
        .build()
        .map_err(|e| format!("Indicator failed: {}", e))?;
    #[cfg(not(target_os = "macos"))]
    let window = builder
        .transparent(true)
        .build()
        .map_err(|e| format!("Indicator failed: {}", e))?;

    #[cfg(target_os = "macos")]
    {
        let _ = window.set_background_color(Some(Color(15, 15, 25, 255)));
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = window.set_background_color(Some(Color(0, 0, 0, 0)));
    }

    // 不调用 set_focus:这是一个 always-on-top 的被动提示窗。语音输入的目的
    // 是把文字打进用户当前的应用,录音一开始就抢走焦点会直接破坏这个前提。
    let _ = window.show();

    Ok(())
}

pub fn hide(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.close();
    }
    Ok(())
}

// 说明:此处原有 update_timer / update_level / update_status 三个 push 接口,
// 已删除。胶囊改成了 pull 模型 —— indicator.html 每 100ms 通过
// invoke('get_audio_level') / invoke('get_indicator_status') 主动取值,
// 因此这三个函数不但 Rust 侧无人调用,页面侧也从不监听它们发出的事件。

/// Show processing result time on indicator (e.g., "1234ms").
/// The indicator JS will display it for a short duration before hiding.
pub fn show_result(app: &tauri::AppHandle, duration_ms: u64) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit(
            "indicator-result",
            serde_json::json!({
                "duration_ms": duration_ms,
            }),
        );
    }
}

/// 返回可用工作区的水平中点与底边,单位是**逻辑像素**。
///
/// `Monitor::size()` / `work_area()` 给的是物理像素,而 `WebviewWindowBuilder`
/// 的 `position()` 和 `inner_size()` 收的是逻辑像素 —— 不按 scale_factor 换算,
/// 在 Retina(scale=2)上算出来的坐标会是实际值的两倍:1512x982 的屏幕会得到
/// y≈1864,窗口整个落到屏幕下方之外,胶囊"消失"。
///
/// 用 work_area 而不是 size,这样底部对齐会落在 Dock 上方而不是被它盖住。
fn screen_center_bottom(app: &tauri::AppHandle) -> (i32, i32) {
    if let Some(main) = app.get_webview_window("main") {
        if let Ok(Some(mon)) = main.current_monitor() {
            let scale = mon.scale_factor();
            if scale > 0.0 {
                let area = mon.work_area();
                let left = area.position.x as f64 / scale;
                let top = area.position.y as f64 / scale;
                let width = area.size.width as f64 / scale;
                let height = area.size.height as f64 / scale;
                return ((left + width / 2.0) as i32, (top + height) as i32);
            }
        }
    }
    // 兜底:取一个常见的逻辑分辨率,至少保证窗口落在屏幕内
    (760, 900)
}
