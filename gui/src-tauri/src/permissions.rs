//! 系统权限(macOS TCC)查询与申请。
//!
//! 本应用在 macOS 上需要三项权限,缺任何一项都会静默失效:
//!
//! | 权限 | 用到它的代码 | 查询 API | 申请 API |
//! |------|--------------|----------|----------|
//! | 麦克风 | `audio.rs`(cpal 采集) | `AVCaptureDevice.authorizationStatusForMediaType:` | `requestAccessForMediaType:completionHandler:` |
//! | 输入监控 | `hotkey.rs`(CGEventTap 全局快捷键) | `IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)` | `IOHIDRequestAccess(kIOHIDRequestTypeListenEvent)` |
//! | 辅助功能 | `input.rs`(enigo 模拟键盘输入) | `AXIsProcessTrusted()` | `AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: true})` |
//!
//! 麦克风还需要 `Info.plist` 里的 `NSMicrophoneUsageDescription`——缺这个 key
//! 时系统直接拒绝(且可能杀掉进程),不会弹窗。见 `src-tauri/Info.plist`。
//!
//! 非 macOS 平台不存在这套 TCC 机制,所有查询一律返回 `Granted`,调用方无需
//! 按平台分支。

use serde::{Deserialize, Serialize};

/// 单项权限状态。
///
/// macOS 对麦克风区分「尚未询问 / 已拒绝 / 受限(家长控制等)/ 已授权」四态;
/// 输入监控区分「未知(尚未询问)/ 已拒绝 / 已授权」三态;辅助功能只有
/// 「受信任 / 不受信任」两态,因此不受信任一律映射为 `Denied`。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionStatus {
    /// 已授权,功能可用。
    Granted,
    /// 用户明确拒绝过。系统不会再自动弹窗,只能引导用户去「系统设置」手动打开。
    Denied,
    /// 从未询问过。此时调用 `request_*` 会弹出系统授权窗口。
    NotDetermined,
    /// 被策略限制(MDM / 家长控制),用户自己也改不了。
    Restricted,
}

impl PermissionStatus {
    /// 是否已授权。
    pub fn is_granted(self) -> bool {
        matches!(self, PermissionStatus::Granted)
    }
}

/// 三项权限的快照,供前端一次性查询。
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct PermissionReport {
    /// 非 macOS 时为 false,前端据此隐藏整个权限区块。
    pub is_macos: bool,
    pub microphone: PermissionStatus,
    pub input_monitoring: PermissionStatus,
    pub accessibility: PermissionStatus,
}

/// 前端指定要申请 / 要打开设置面板的权限项。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Permission {
    Microphone,
    InputMonitoring,
    Accessibility,
}

impl Permission {
    /// 对应的「系统设置 → 隐私与安全性」子面板 URL。
    ///
    /// 权限被拒绝后系统不会再弹窗,只能把用户送到这里手动勾选。
    pub fn settings_url(self) -> &'static str {
        match self {
            Permission::Microphone => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
            }
            Permission::InputMonitoring => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
            }
            Permission::Accessibility => {
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            }
        }
    }
}

/// 一次性查询三项权限(不弹窗)。
pub fn report() -> PermissionReport {
    PermissionReport {
        is_macos: cfg!(target_os = "macos"),
        microphone: microphone_status(),
        input_monitoring: input_monitoring_status(),
        accessibility: accessibility_status(),
    }
}

/// 打开对应的系统设置面板。非 macOS 为空操作。
pub fn open_settings(permission: Permission) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(permission.settings_url())
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("打开系统设置失败: {}", e))
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = permission;
        Ok(())
    }
}

/// 轮询等待某项权限变为已授权(用户在系统弹窗 / 设置面板里操作需要时间)。
///
/// 最多等 `timeout`,一旦不再是 `NotDetermined` 就立即返回。超时返回当前状态,
/// 不会无限挂住命令。
pub async fn await_status(
    permission: Permission,
    timeout: std::time::Duration,
) -> PermissionStatus {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        let status = status_of(permission);
        if status.is_granted() || std::time::Instant::now() >= deadline {
            return status;
        }
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
    }
}

/// 查询单项权限(不弹窗)。
pub fn status_of(permission: Permission) -> PermissionStatus {
    match permission {
        Permission::Microphone => microphone_status(),
        Permission::InputMonitoring => input_monitoring_status(),
        Permission::Accessibility => accessibility_status(),
    }
}

/// 申请单项权限(会弹出系统窗口,前提是该权限从未被询问过)。
///
/// 调用立即返回;是否授权要在之后重新查询状态。
pub fn request(permission: Permission) {
    match permission {
        Permission::Microphone => request_microphone(),
        Permission::InputMonitoring => request_input_monitoring(),
        Permission::Accessibility => request_accessibility(),
    }
}

// ── macOS 实现 ──

#[cfg(target_os = "macos")]
mod imp {
    use super::PermissionStatus;

    // ---- 麦克风:AVFoundation ----

    /// `AVCaptureDevice.authorizationStatusForMediaType(AVMediaTypeAudio)`。
    pub fn microphone_status() -> PermissionStatus {
        use objc2_av_foundation::{AVAuthorizationStatus, AVCaptureDevice, AVMediaTypeAudio};

        // AVMediaTypeAudio 是 AVFoundation 的 extern 常量,读取 extern static 需要 unsafe。
        let Some(media_type) = (unsafe { AVMediaTypeAudio }) else {
            // 常量拿不到说明 AVFoundation 没正常加载,按「未询问」处理,
            // 让后续真正采集时由系统决定。
            return PermissionStatus::NotDetermined;
        };
        let status = unsafe { AVCaptureDevice::authorizationStatusForMediaType(media_type) };
        if status == AVAuthorizationStatus::Authorized {
            PermissionStatus::Granted
        } else if status == AVAuthorizationStatus::Denied {
            PermissionStatus::Denied
        } else if status == AVAuthorizationStatus::Restricted {
            PermissionStatus::Restricted
        } else {
            PermissionStatus::NotDetermined
        }
    }

    /// `AVCaptureDevice.requestAccessForMediaType:completionHandler:`。
    ///
    /// 立即返回(系统弹窗是异步的);结果由调用方重新查询状态得到,所以
    /// completion handler 只用来打日志。
    pub fn request_microphone() {
        use block2::RcBlock;
        use objc2::runtime::Bool;
        use objc2_av_foundation::{AVCaptureDevice, AVMediaTypeAudio};

        let Some(media_type) = (unsafe { AVMediaTypeAudio }) else {
            return;
        };
        let handler = RcBlock::new(|granted: Bool| {
            eprintln!("[perm] 麦克风授权结果: {}", granted.as_bool());
        });
        unsafe {
            AVCaptureDevice::requestAccessForMediaType_completionHandler(media_type, &handler);
        }
    }

    // ---- 输入监控:IOKit HID ----

    /// `IOHIDRequestType`。0 = kIOHIDRequestTypePostEvent,1 = kIOHIDRequestTypeListenEvent。
    /// 我们只监听事件(CGEventTap 的 ListenOnly 模式),所以用 ListenEvent。
    const IOHID_REQUEST_TYPE_LISTEN_EVENT: u32 = 1;

    // IOHIDAccessType:0 = Granted,1 = Denied,2 = Unknown(从未询问)。
    const IOHID_ACCESS_TYPE_GRANTED: u32 = 0;
    const IOHID_ACCESS_TYPE_DENIED: u32 = 1;

    #[link(name = "IOKit", kind = "framework")]
    extern "C" {
        /// 查询,不弹窗。macOS 10.15+。
        fn IOHIDCheckAccess(request_type: u32) -> u32;
        /// 申请,未询问过时弹窗;已拒绝时直接返回 false 且不弹窗。
        /// 返回 C 的 `bool`,用 u8 接以避免非 0/1 值造成 UB。
        fn IOHIDRequestAccess(request_type: u32) -> u8;
    }

    pub fn input_monitoring_status() -> PermissionStatus {
        match unsafe { IOHIDCheckAccess(IOHID_REQUEST_TYPE_LISTEN_EVENT) } {
            IOHID_ACCESS_TYPE_GRANTED => PermissionStatus::Granted,
            IOHID_ACCESS_TYPE_DENIED => PermissionStatus::Denied,
            _ => PermissionStatus::NotDetermined,
        }
    }

    pub fn request_input_monitoring() {
        // IOHIDRequestAccess 在部分系统版本上会同步等待用户操作,放到独立线程里
        // 调用,避免卡住主线程 / tokio worker。
        let _ = std::thread::Builder::new()
            .name("perm-hid-request".into())
            .spawn(|| {
                let granted = unsafe { IOHIDRequestAccess(IOHID_REQUEST_TYPE_LISTEN_EVENT) } != 0;
                eprintln!("[perm] 输入监控授权结果: {}", granted);
            });
    }

    // ---- 辅助功能:ApplicationServices ----

    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        /// 返回 Apple 的 `Boolean`(UInt8),用 u8 接。
        fn AXIsProcessTrusted() -> u8;
        fn AXIsProcessTrustedWithOptions(
            options: core_foundation::dictionary::CFDictionaryRef,
        ) -> u8;
        /// CFDictionary 的 key,值为 true 时本次查询会弹出授权引导窗口。
        static kAXTrustedCheckOptionPrompt: core_foundation::string::CFStringRef;
    }

    pub fn accessibility_status() -> PermissionStatus {
        // AX 只有「受信任 / 不受信任」两态,拿不到「从未询问」。
        if unsafe { AXIsProcessTrusted() } != 0 {
            PermissionStatus::Granted
        } else {
            PermissionStatus::Denied
        }
    }

    pub fn request_accessibility() {
        use core_foundation::base::TCFType;
        use core_foundation::boolean::CFBoolean;
        use core_foundation::dictionary::CFDictionary;
        use core_foundation::string::CFString;

        let key = unsafe { CFString::wrap_under_get_rule(kAXTrustedCheckOptionPrompt) };
        let options = CFDictionary::from_CFType_pairs(&[(
            key.as_CFType(),
            CFBoolean::true_value().as_CFType(),
        )]);
        let trusted = unsafe { AXIsProcessTrustedWithOptions(options.as_concrete_TypeRef()) } != 0;
        eprintln!("[perm] 辅助功能受信任: {}", trusted);
    }
}

// ── 非 macOS:无 TCC,全部视为已授权 ──

#[cfg(not(target_os = "macos"))]
mod imp {
    use super::PermissionStatus;

    pub fn microphone_status() -> PermissionStatus {
        PermissionStatus::Granted
    }
    pub fn request_microphone() {}

    pub fn input_monitoring_status() -> PermissionStatus {
        PermissionStatus::Granted
    }
    pub fn request_input_monitoring() {}

    pub fn accessibility_status() -> PermissionStatus {
        PermissionStatus::Granted
    }
    pub fn request_accessibility() {}
}

pub use imp::{
    accessibility_status, input_monitoring_status, microphone_status, request_accessibility,
    request_input_monitoring, request_microphone,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_urls_match_the_three_privacy_panes() {
        assert!(Permission::Microphone
            .settings_url()
            .ends_with("?Privacy_Microphone"));
        assert!(Permission::InputMonitoring
            .settings_url()
            .ends_with("?Privacy_ListenEvent"));
        assert!(Permission::Accessibility
            .settings_url()
            .ends_with("?Privacy_Accessibility"));
    }

    #[test]
    fn status_serializes_as_snake_case() {
        let json = serde_json::to_string(&PermissionStatus::NotDetermined).unwrap();
        assert_eq!(json, "\"not_determined\"");
    }

    /// 非 macOS 上三项权限必须全部报告为已授权,调用方才不需要平台分支。
    #[cfg(not(target_os = "macos"))]
    #[test]
    fn non_macos_reports_everything_granted() {
        let r = report();
        assert!(!r.is_macos);
        assert!(r.microphone.is_granted());
        assert!(r.input_monitoring.is_granted());
        assert!(r.accessibility.is_granted());
    }
}
