//! Auto-update via GitHub Releases.
//! Uses direct HTTP fetch for check (with 30s timeout), falls back to
//! tauri-plugin-updater for download + install.

use serde::{Deserialize, Serialize};
use tauri::Emitter;
use tauri_plugin_updater::UpdaterExt;

/// GitHub release asset manifest
#[derive(Deserialize)]
struct ReleaseManifest {
    version: String,
    #[allow(dead_code)]
    notes: Option<String>,
    #[allow(dead_code)]
    pub_date: Option<String>,
    platforms: std::collections::HashMap<String, PlatformEntry>,
}

#[derive(Deserialize)]
struct PlatformEntry {
    url: String,
    #[allow(dead_code)]
    signature: String,
}

/// Result of an update check
#[derive(Serialize, Clone)]
pub struct UpdateInfo {
    pub available: bool,
    pub current_version: String,
    pub latest_version: String,
    pub body: String,
    pub download_size: u64,
}

const LATEST_JSON_URL: &str =
    "https://github.com/3F3Feng/voice-input-framework/releases/latest/download/latest.json";

/// Check for updates via direct HTTP fetch (30s timeout).
pub async fn check(app: &tauri::AppHandle) -> Result<UpdateInfo, String> {
    let current = app.package_info().version.to_string();
    eprintln!("[update] Checking for updates (current: {})...", current);

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| format!("创建HTTP客户端失败: {}", e))?;

    let resp = client
        .get(LATEST_JSON_URL)
        .send()
        .await
        .map_err(|e| format!("请求最新版本信息超时(30s): {}", e))?;

    if !resp.status().is_success() {
        return Err(format!("服务器响应异常: HTTP {}", resp.status()));
    }

    let manifest: ReleaseManifest = resp
        .json()
        .await
        .map_err(|e| format!("解析更新信息失败: {}", e))?;

    let latest = manifest.version.trim_start_matches('v').to_string();
    let available = latest != current && latest != current.trim_start_matches('v');

    eprintln!(
        "[update] Current: {}, Latest: {}, available: {}",
        current, latest, available
    );

    Ok(UpdateInfo {
        available,
        current_version: current,
        latest_version: latest.clone(),
        body: format!("版本 {}", latest),
        download_size: 0,
    })
}

/// Download and install using the tauri-plugin-updater.
pub async fn download_and_install(app: &tauri::AppHandle) -> Result<String, String> {
    let updater = match app.updater() {
        Ok(u) => u,
        Err(_) => return Err("更新插件未启用".to_string()),
    };

    let maybe_update = updater
        .check()
        .await
        .map_err(|e| format!("检查更新失败: {}", e))?;

    let update = match maybe_update {
        Some(u) if u.version != u.current_version => u,
        Some(_) => return Ok("已是最新版本".to_string()),
        None => return Ok("没有可用更新".to_string()),
    };

    eprintln!("[update] Downloading {}...", update.version);
    let _ = app.emit("update-progress", "正在下载更新...");

    match update
        .download_and_install(|_chunk_length, _total| {}, || {})
        .await
    {
        Ok(()) => {
            eprintln!("[update] Update installed successfully");
            let _ = app.emit("update-progress", "更新已安装，重启后生效");
            Ok("更新已安装，重启应用后生效".to_string())
        }
        Err(e) => Err(format!("下载安装失败: {}", e)),
    }
}
