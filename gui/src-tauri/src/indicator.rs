//! Floating recording indicator overlay at bottom center of screen.

use tauri::{window::Color, Emitter, Manager, WebviewWindowBuilder};

pub const INDICATOR_LABEL: &str = "indicator";

pub fn show(app: &tauri::AppHandle) -> Result<(), String> {
    // 复用已存在的窗口:close 是异步的,close 后立刻同 label 重建会
    // "window already exists" 静默失败 → 胶囊不显示。复用 + 重置页面。
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit("indicator-reset", ());
        // 复用同样要走 float_over_fullscreen:上次显示时所在的 Space 可能
        // 已经不是现在这个,需要重新设 behavior 再 order front。
        float_over_fullscreen(app);
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

    // 全平台使用透明窗口,让 indicator.html 里的圆角胶囊直接呈现。
    //
    // 历史:曾因 macOS 上"透明窗口内容不渲染"改成不透明深色窗口,但那次
    // 诊断(title after 1s 为空)是被定位缺陷误导的 —— 当时窗口被放在了
    // 屏幕外面(物理像素当逻辑像素用),而 macOS 对完全离屏的窗口会推迟
    // WebView 渲染。定位修复后页面渲染正常,不透明窗口反而会在圆角胶囊
    // 外面露出一圈黑色方块(窗口 210x44 比 200px 的胶囊大一圈)。
    let window = builder
        .transparent(true)
        .build()
        .map_err(|e| format!("Indicator failed: {}", e))?;

    let _ = window.set_background_color(Some(Color(0, 0, 0, 0)));

    // 注意:显示动作在 float_over_fullscreen 内部完成。
    // collection behavior 必须在窗口显示**之前**设好 —— 窗口一旦显示,
    // macOS 就把它分配到了当时的 Space,之后再改 behavior 不会重新分配。
    // 不调用 set_focus:这是一个 always-on-top 的被动提示窗。语音输入的目的
    // 是把文字打进用户当前的应用,录音一开始就抢走焦点会直接破坏这个前提。
    float_over_fullscreen(app);

    Ok(())
}

/// 让胶囊能浮在全屏应用之上。
///
/// macOS 的全屏应用独占一个 Space,普通窗口即使 always_on_top 也进不去。
/// Tauri 的 `visible_on_all_workspaces` 只设了 `CanJoinAllSpaces`(1<<0),
/// 那管的是多个桌面 Space,管不了全屏;还需要 `FullScreenAuxiliary`(1<<8)。
/// 同时把层级从 NSFloatingWindowLevel(3)提到 NSStatusWindowLevel(25),
/// 否则在全屏 Space 里仍会被盖住。
///
/// **必须在主线程执行**:AppKit 规定 NSWindow 只能在主线程操作,macOS 27 对
/// 违反者是硬性 trap("Must only be used from the main thread" → SIGTRAP)。
/// `show()` 是从 hotkey-worker 线程调进来的 —— 同一函数里的 `window.show()`
/// 等调用之所以安全,是因为 Tauri 内部会转发到主线程;这里用 msg_send! 直接
/// 打 AppKit 绕过了那层保护,所以必须自己用 run_on_main_thread 转发。
#[cfg(target_os = "macos")]
fn float_over_fullscreen(app: &tauri::AppHandle) {
    let app = app.clone();
    // 在闭包内部重新取窗口,避免把 ns_window 的裸指针跨线程传递。
    let _ = app.clone().run_on_main_thread(move || {
        use objc2::msg_send;
        use objc2::runtime::AnyObject;

        const CAN_JOIN_ALL_SPACES: usize = 1 << 0;
        const FULL_SCREEN_AUXILIARY: usize = 1 << 8;
        const NS_STATUS_WINDOW_LEVEL: isize = 25;

        let Some(window) = app.get_webview_window(INDICATOR_LABEL) else {
            return;
        };
        let Ok(ptr) = window.ns_window() else {
            return;
        };
        if ptr.is_null() {
            return;
        }
        let ns_window = ptr as *mut AnyObject;
        unsafe {
            let current: usize = msg_send![ns_window, collectionBehavior];
            let behavior = current | CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY;
            let _: () = msg_send![ns_window, setCollectionBehavior: behavior];
            let _: () = msg_send![ns_window, setLevel: NS_STATUS_WINDOW_LEVEL];

            // 设好 behavior 之后才显示。orderFrontRegardless 不会激活本应用,
            // 因此不会把用户从全屏应用里踢出去,也不抢焦点。
            let _: () = msg_send![ns_window, orderFrontRegardless];
        }
    });
}

/// 非 macOS:没有 Space 概念,直接显示即可(显示动作在 macOS 分支里是由
/// orderFrontRegardless 完成的,这里要补上)。
#[cfg(not(target_os = "macos"))]
fn float_over_fullscreen(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.show();
    }
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
