//! 界面语言(F22):中文 / 英文两套文案。
//!
//! 只有两种语言,所以不搞键值表:每处文案就地写成 `t("中文", "English")`,
//! 读代码时两种说法摆在一起,改一边不会忘了另一边。需要格式化的用 [`tr!`]。
//!
//! 「跟随系统」由前端解析(`navigator.languages` 在 WebView 里跟着系统语言走,
//! Rust 这边没有现成可靠的办法,macOS 从 Finder 启动时连 `LANG` 都没有),
//! 解析完调 `set_ui_language` 告诉这边。在那之前,配置里明确选了语言就用配置的,
//! 否则先按中文。

//!
//! 这个文件不依赖 tauri:`stt.rs` 用到它,而 `stt-logic-tests` 用 `#[path]` 复用
//! `stt.rs` 时也得把它一起带上。

use std::sync::atomic::{AtomicBool, Ordering};

static ENGLISH: AtomicBool = AtomicBool::new(false);

pub fn is_en() -> bool {
    ENGLISH.load(Ordering::Relaxed)
}

/// 按当前界面语言挑一句。
pub fn t(zh: &'static str, en: &'static str) -> &'static str {
    pick(is_en(), zh, en)
}

fn pick<'a>(english: bool, zh: &'a str, en: &'a str) -> &'a str {
    if english {
        en
    } else {
        zh
    }
}

/// 带格式化参数的 [`t`]:`tr!("还剩 {} 秒", "{}s left", n)`。
#[macro_export]
macro_rules! tr {
    ($zh:literal, $en:literal $(, $arg:expr)* $(,)?) => {
        if $crate::i18n::is_en() {
            format!($en $(, $arg)*)
        } else {
            format!($zh $(, $arg)*)
        }
    };
}

/// 启动时按配置里的偏好先定一个语言。`auto` 先按中文,等前端解析完再改。
pub fn init_from_pref(pref: &str) {
    ENGLISH.store(pref == "en", Ordering::Relaxed);
}

/// 前端解析出实际语言后调用,`lang` 是 `zh` 或 `en`。
pub fn set_resolved(lang: &str) {
    ENGLISH.store(lang == "en", Ordering::Relaxed);
}

// 测试里**不要**切全局语言:别的模块的测试并行跑,断言的是中文文案。
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn picks_by_language() {
        assert_eq!(pick(true, "你好", "Hello"), "Hello");
        assert_eq!(pick(false, "你好", "Hello"), "你好");
    }

    #[test]
    fn defaults_to_chinese() {
        assert!(!is_en());
        assert_eq!(tr!("还剩 {} 秒", "{}s left", 5), "还剩 5 秒");
    }
}
