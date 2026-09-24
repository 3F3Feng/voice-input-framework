//! Floating recording indicator overlay at bottom center of screen.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tauri::{window::Color, Emitter, Manager, WebviewWindowBuilder};

use crate::i18n::t;

pub const INDICATOR_LABEL: &str = "indicator";

/// 失败 / 没听到声音时胶囊停留多久再关。太短看不清,太长挡着下一次录音。
pub const FAILURE_LINGER: Duration = Duration::from_millis(2500);

/// 每次 `show()` 加一。延迟关闭(`hide_later`)只在期间没有新的 `show()` 时
/// 才真的关:结果 / 错误还挂在胶囊上时用户又开始了下一次录音,旧任务睡醒后
/// 不能把正在录音的胶囊关掉。停留时间从 500 ms 拉长到 2.5 s 后,这种重叠
/// 就不再罕见了。
static SHOW_GEN: AtomicU64 = AtomicU64::new(0);

pub fn show(app: &tauri::AppHandle) -> Result<(), String> {
    SHOW_GEN.fetch_add(1, Ordering::SeqCst);
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
    // 胶囊是被动提示,绝不能拿焦点。Windows(以及部分 Linux 桌面)上新建的
    // 窗口默认会被激活:录音一开始,键盘焦点就从用户正在输入的应用跳到胶囊上,
    // 转写完自动输入的文字就打进了胶囊(等于丢了)。`focused(false)` 管创建
    // 那一刻,`focusable(false)`(Windows 上是 WS_EX_NOACTIVATE)管之后。
    .focused(false)
    .focusable(false)
    // 页面是独立的纯 HTML,用不了前端的 i18n:创建时把当前界面语言塞给它,
    // 之后切换语言靠它自己监听 `ui-language` 事件。
    .initialization_script(if crate::i18n::is_en() {
        "window.__VIF_LANG = 'en';"
    } else {
        "window.__VIF_LANG = 'zh';"
    })
    .title("");

    // 全平台使用透明窗口,让 indicator.html 里的圆角胶囊直接呈现。
    //
    // 历史:曾因 macOS 上"透明窗口内容不渲染"改成不透明深色窗口,但那次
    // 诊断(title after 1s 为空)是被定位缺陷误导的 —— 当时窗口被放在了
    // 屏幕外面(物理像素当逻辑像素用),而 macOS 对完全离屏的窗口会推迟
    // WebView 渲染。定位修复后页面渲染正常,不透明窗口反而会在圆角胶囊
    // 外面露出一圈黑色方块(窗口 210x44 比 200px 的胶囊大一圈)。
    let window = builder.transparent(true).build().map_err(|e| {
        crate::tr!(
            "录音提示胶囊创建失败:{}",
            "Couldn't create the recording indicator: {}",
            e
        )
    })?;

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
///
/// 已经可见就不再 show:Windows 上 show 走的是 `ShowWindow(SW_SHOW)`,
/// 那是「显示**并激活**」;新建窗口的那次显示由 `focused(false)` 换成了
/// 不激活的版本,复用窗口时没这层保护。
#[cfg(not(target_os = "macos"))]
fn float_over_fullscreen(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        if !window.is_visible().unwrap_or(false) {
            let _ = window.show();
        }
    }
}

pub fn hide(app: &tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.close();
    }
    Ok(())
}

/// 等 `delay` 之后关掉胶囊 —— 除非这期间又开始了新的录音(见 `SHOW_GEN`)。
pub async fn hide_later(app: &tauri::AppHandle, delay: Duration) {
    let gen = SHOW_GEN.load(Ordering::SeqCst);
    tokio::time::sleep(delay).await;
    if SHOW_GEN.load(Ordering::SeqCst) == gen {
        let _ = hide(app);
    }
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

/// 在胶囊上显示「还剩 N 秒」:录音快到时长上限了。胶囊继续录音、继续
/// 显示音量,只是把计时换成倒计时。
pub fn show_warning(app: &tauri::AppHandle, remaining_secs: u64) {
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit(
            "indicator-warning",
            serde_json::json!({ "remaining_secs": remaining_secs }),
        );
    }
}

/// 在胶囊上显示失败原因(红点)或「没听到声音」(灰点)。
///
/// 以前转写一失败胶囊就立刻关掉,原因只进主窗口的 toast —— 主窗口藏着的
/// 时候(这是常态:这是个快捷键驱动的后台应用),用户说完话什么也没看到。
/// 调用方负责随后 `hide_later(FAILURE_LINGER)`。
pub fn show_failure(app: &tauri::AppHandle, err: &str) {
    let (kind, message) = failure_display(err);
    if let Some(window) = app.get_webview_window(INDICATOR_LABEL) {
        let _ = window.emit(
            "indicator-error",
            serde_json::json!({ "kind": kind, "message": message }),
        );
    }
}

/// 转写错误 → 胶囊上的 (样式, 一句短原因)。胶囊只有 200px 宽,放不下完整的
/// 错误信息(常常带着 URL),这里只给个大类,完整原因在主窗口的 toast 和日志里。
/// 样式:`"info"` 是灰点(不算故障),`"error"` 是红点。
pub(crate) fn failure_display(err: &str) -> (&'static str, &'static str) {
    if err == crate::stt::NO_SPEECH {
        ("info", t("没听到声音", "Didn't catch anything"))
    } else if crate::stt::is_unreachable(err) {
        ("error", t("连不上识别服务", "Can't reach STT"))
    } else if crate::stt::is_result_timeout(err)
        || err == "转写超时"
        || err == "Transcription timed out"
    {
        // 「转写超时」是服务端自己的 600 秒上限(stt_server.py 的 E5002),
        // 服务端按界面语言回中文或英文。
        ("error", t("识别超时", "STT timed out"))
    } else {
        ("error", t("识别失败", "Transcription failed"))
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_speech_is_info_not_error() {
        assert_eq!(
            failure_display(crate::stt::NO_SPEECH),
            ("info", "没听到声音")
        );
    }

    #[test]
    fn connection_failures_say_so() {
        for e in [
            "连不上 STT 服务(http://127.0.0.1:6544):Connection refused",
            "连不上 STT 服务(http://10.0.0.1:6544):连接超时",
            "连不上 STT 服务:服务没有应答(等待就绪消息超时)",
            "连不上 STT 服务:服务没有发来就绪消息",
        ] {
            assert_eq!(failure_display(e), ("error", "连不上识别服务"), "{e}");
        }
    }

    #[test]
    fn timeouts_and_other_errors() {
        assert_eq!(
            failure_display("等待识别结果超时(5 分钟)"),
            ("error", "识别超时")
        );
        assert_eq!(failure_display("转写超时"), ("error", "识别超时"));
        // 界面是英文时服务端和客户端给的是英文错误,归类不能跟着失效。
        assert_eq!(
            failure_display("Transcription timed out"),
            ("error", "识别超时")
        );
        assert_eq!(
            failure_display("Can't reach the STT service (http://127.0.0.1:6544): refused"),
            ("error", "连不上识别服务")
        );
        assert_eq!(
            failure_display("Timed out waiting for the transcription result (5 min)"),
            ("error", "识别超时")
        );
        assert_eq!(
            failure_display("等待识别结果超时:识别服务 60 秒没有动静,可能已经卡住"),
            ("error", "识别超时")
        );
        // 以前按「超时」这个词归类:服务端回的任何带「超时」的错误都会被说成识别超时。
        assert_eq!(
            failure_display("模型加载失败:下载超时"),
            ("error", "识别失败")
        );
        assert_eq!(failure_display("CUDA out of memory"), ("error", "识别失败"));
    }
}
