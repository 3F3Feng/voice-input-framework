//! 系统界面语言是不是中文 —— 只用于「跟随系统」时启动那一刻的初判。
//!
//! 前端加载完会按 `navigator.languages` 解析一次再告诉 Rust(`set_ui_language`),
//! 但在那之前 Rust 已经在干活了:托盘菜单要建、快捷键监听起不来的原因要记下
//! (之后设置页原样显示)。以前「跟随系统」一律先按中文,英文系统上这几句就是中文。
//!
//! 各平台取的都是用户首选的界面语言,不是区域格式(英文界面 + 中国区域格式的
//! 用户要的是英文)。

/// 语言标签(`zh-Hans-CN`、`zh_CN.UTF-8`、`en-US`……)是不是中文。
fn tag_is_chinese(tag: &str) -> bool {
    let t = tag.trim().to_ascii_lowercase();
    t == "zh" || t.starts_with("zh-") || t.starts_with("zh_")
}

#[cfg(target_os = "macos")]
pub fn system_is_chinese() -> bool {
    use core_foundation::array::{CFArray, CFArrayRef};
    use core_foundation::base::TCFType;
    use core_foundation::string::CFString;

    extern "C" {
        fn CFLocaleCopyPreferredLanguages() -> CFArrayRef;
    }
    // 系统设置 →「语言与地区」里的首选语言列表,第一项就是界面语言。
    let raw = unsafe { CFLocaleCopyPreferredLanguages() };
    if raw.is_null() {
        return false;
    }
    let langs: CFArray<CFString> = unsafe { CFArray::wrap_under_create_rule(raw) };
    let first = langs.get(0).map(|s| s.to_string());
    first.as_deref().is_some_and(tag_is_chinese)
}

#[cfg(target_os = "windows")]
pub fn system_is_chinese() -> bool {
    #[link(name = "kernel32")]
    extern "system" {
        fn GetUserDefaultUILanguage() -> u16;
    }
    // LANGID 的低 10 位是主语言;LANG_CHINESE = 0x04(简繁都是)。
    let langid = unsafe { GetUserDefaultUILanguage() };
    langid & 0x3ff == 0x04
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
pub fn system_is_chinese() -> bool {
    // POSIX 的优先顺序:LC_ALL > LC_MESSAGES > LANG。LANGUAGE 是 GNU 的首选列表。
    ["LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"]
        .iter()
        .filter_map(|k| std::env::var(k).ok())
        .find(|v| !v.is_empty() && v != "C" && v != "POSIX")
        .map(|v| tag_is_chinese(v.split(':').next().unwrap_or("")))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::{system_is_chinese, tag_is_chinese};

    /// 真去问一次系统(FFI 的签名、内存管理错了会在这里崩);结果随机器而定,不断言。
    #[test]
    fn system_language_query_runs() {
        eprintln!("system_is_chinese = {}", system_is_chinese());
    }

    #[test]
    fn recognizes_chinese_tags() {
        for tag in [
            "zh",
            "zh-Hans",
            "zh-Hans-CN",
            "zh-TW",
            "zh_CN.UTF-8",
            "ZH-hk",
        ] {
            assert!(tag_is_chinese(tag), "{tag}");
        }
        for tag in ["en", "en-US", "en_US.UTF-8", "ja-JP", "", "zhx"] {
            assert!(!tag_is_chinese(tag), "{tag}");
        }
    }
}
