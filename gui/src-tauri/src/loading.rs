//! 模型加载 / 下载进度(服务端 `/health.loading`,见 services/model_catalog.py 的
//! `load_progress`)。STT 和 LLM 两个服务报的是同一个格式。
//!
//! 首次用一个模型要下几百 MB 到几 GB(默认的 LLM 是 3–7 GB)。以前只有服务器面板
//! 那一行会说「已下载多少 MB」,而且没有分母;主界面头部全程只有一句「模型加载中…」,
//! LLM 那边连这一句都没有——看不出是在下载、卡住了还是坏了。

use serde::{Deserialize, Deserializer, Serialize};

use crate::i18n::t;
use crate::tr;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct LoadingProgress {
    /// 正在加载的模型名。老服务端没有。
    pub model: Option<String>,
    /// 加载了多少秒(服务端报的是小数,这里取整:要能比较相等,也没人看小数)。
    #[serde(deserialize_with = "secs")]
    pub elapsed_s: u64,
    /// **这次加载**新下载的字节数。
    pub downloaded_bytes: u64,
    /// 缓存里现在一共有多少字节(上次下到一半时比上面那个大)。老服务端没有。
    pub cached_bytes: Option<u64>,
    /// 下完一共多大(估计值)。老服务端没有,服务端不知道时也为空。
    pub total_bytes: Option<u64>,
}

fn secs<'de, D: Deserializer<'de>>(d: D) -> Result<u64, D::Error> {
    let v = f64::deserialize(d)?;
    Ok(if v.is_finite() && v > 0.0 {
        v.round() as u64
    } else {
        0
    })
}

/// 十进制(1 MB = 10^6 字节):和 HuggingFace 页面上、服务端注册表里的 `download_mb`
/// 是同一种算法,界面上「需下载约 1.6 GB」和这里的分母才对得上。
const MB: u64 = 1_000_000;

/// 「1.6 GB」/「312 MB」。
fn size_text(bytes: u64) -> String {
    if bytes >= 1000 * MB {
        format!("{:.1} GB", bytes as f64 / (1000 * MB) as f64)
    } else {
        format!("{} MB", bytes / MB)
    }
}

impl LoadingProgress {
    /// 已经在本地的字节数:知道缓存总量就用它(和分母对得上),否则用这次下载的量。
    fn have(&self) -> u64 {
        self.cached_bytes
            .unwrap_or(self.downloaded_bytes)
            .max(self.downloaded_bytes)
    }

    /// 给用户看的一句话。
    pub fn text(&self) -> String {
        let secs = self.elapsed_s;
        // 不到 1 MB 的增长不算在下载:加载时落盘的几个小配置文件也会让缓存变大。
        if self.downloaded_bytes < MB {
            return if secs >= 1 {
                tr!("正在加载模型…({} 秒)", "Loading model… ({}s)", secs)
            } else {
                t("正在加载模型...", "Loading model...").to_string()
            };
        }
        let have = self.have();
        match self.total_bytes.filter(|total| *total > 0) {
            // 估计值对不上(仓库更新过、引擎多拉了文件)时不显示分母,免得出现 105%。
            Some(total) if have <= total => {
                let pct = have * 100 / total;
                if pct >= 99 {
                    tr!(
                        "下载完成,正在加载模型…({} 秒)",
                        "Download finished; loading model… ({}s)",
                        secs
                    )
                } else {
                    tr!(
                        "正在下载模型… {} / 约 {}({}%,{} 秒)",
                        "Downloading model… {} of ~{} ({}%, {}s)",
                        size_text(have),
                        size_text(total),
                        pct,
                        secs
                    )
                }
            }
            _ => tr!(
                "正在下载模型… 已下载 {}({} 秒)",
                "Downloading model… {} downloaded ({}s)",
                size_text(have),
                secs
            ),
        }
    }
}

/// 「启动中」那一行怎么说。没有进度(老服务端、刚起来还没开始加载)时只有一句话。
pub fn text(progress: Option<&LoadingProgress>) -> String {
    match progress {
        Some(p) => p.text(),
        None => t("正在加载模型...", "Loading model...").to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(v: serde_json::Value) -> LoadingProgress {
        serde_json::from_value(v).unwrap()
    }

    #[test]
    fn old_server_without_totals_reports_bytes_only() {
        let p = parse(serde_json::json!({"elapsed_s": 42.4, "downloaded_bytes": 300 * MB}));
        assert_eq!(p.elapsed_s, 42);
        assert_eq!(p.text(), "正在下载模型… 已下载 300 MB(42 秒)");
    }

    #[test]
    fn with_total_shows_fraction_and_percent() {
        let p = parse(serde_json::json!({
            "model": "whisper_turbo", "elapsed_s": 30.0,
            "downloaded_bytes": 300 * MB, "cached_bytes": 405 * MB,
            "total_bytes": 1620 * MB, "phase": "downloading",
        }));
        // 分子用缓存里的总量:上次下到一半、这次接着下,分母是整个模型。
        assert_eq!(p.text(), "正在下载模型… 405 MB / 约 1.6 GB(25%,30 秒)");
    }

    #[test]
    fn estimate_overshoot_drops_the_denominator() {
        let p = parse(serde_json::json!({
            "elapsed_s": 9, "downloaded_bytes": 700 * MB, "cached_bytes": 700 * MB,
            "total_bytes": 600 * MB,
        }));
        assert_eq!(p.text(), "正在下载模型… 已下载 700 MB(9 秒)");
    }

    #[test]
    fn finished_download_says_loading() {
        let p = parse(serde_json::json!({
            "elapsed_s": 80, "downloaded_bytes": 1620 * MB, "cached_bytes": 1620 * MB,
            "total_bytes": 1620 * MB,
        }));
        assert_eq!(p.text(), "下载完成,正在加载模型…(80 秒)");
    }

    #[test]
    fn already_downloaded_model_only_counts_seconds() {
        let p = parse(serde_json::json!({
            "elapsed_s": 7.2, "downloaded_bytes": 0, "cached_bytes": 700 * MB,
            "total_bytes": 715 * MB,
        }));
        assert_eq!(p.text(), "正在加载模型…(7 秒)");
        assert_eq!(text(None), "正在加载模型...");
        assert_eq!(parse(serde_json::json!({})).text(), "正在加载模型...");
    }

    #[test]
    fn null_fields_and_garbage_seconds_dont_break_parsing() {
        let p = parse(serde_json::json!({
            "elapsed_s": -3.0, "downloaded_bytes": 2 * MB, "cached_bytes": null, "total_bytes": null,
        }));
        assert_eq!(p.elapsed_s, 0);
        assert_eq!(p.text(), "正在下载模型… 已下载 2 MB(0 秒)");
    }
}
