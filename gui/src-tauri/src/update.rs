//! Auto-update via GitHub Releases using tauri-plugin-updater.

use serde::Serialize;
use tauri::Emitter;
use tauri_plugin_updater::UpdaterExt;

/// Result of an update check
#[derive(Serialize, Clone)]
pub struct UpdateInfo {
    pub available: bool,
    pub current_version: String,
    pub latest_version: String,
    pub body: String,
    pub download_size: u64,
}

/// Check for updates.
pub async fn check(app: &tauri::AppHandle) -> Result<UpdateInfo, String> {
    let current = app.package_info().version.to_string();
    eprintln!("[update] Checking for updates (current: {})...", current);

    let updater = app.updater()
        .map_err(|e| format!("Updater plugin error: {}", e))?;

    let update = updater.check().await
        .map_err(|e| format!("检查更新失败: {}", e))?;

    match update {
        Some(update) => {
            let latest = update.version.clone();
            let available = latest != current;
            let body = update.body.clone().unwrap_or_default();
            eprintln!("[update] Current: {}, Latest: {}, available: {}", current, latest, available);
            Ok(UpdateInfo { available, current_version: current, latest_version: latest, body, download_size: 0 })
        }
        None => {
            eprintln!("[update] No update available (None)");
            Ok(UpdateInfo { available: false, current_version: current, latest_version: current, body: String::new(), download_size: 0 })
        }
    }
}

/// Download and install the update.
pub async fn download_and_install(app: &tauri::AppHandle) -> Result<String, String> {
    let updater = app.updater()
        .map_err(|e| format!("Updater plugin error: {}", e))?;

    let update = updater.check().await
        .map_err(|e| format!("检查更新失败: {}", e))?;

    let update = match update {
        Some(u) if u.version != u.current_version => u,
        Some(_) => return Ok("已是最新版本".to_string()),
        None => return Ok("没有可用更新".to_string()),
    };

    eprintln!("[update] Downloading {}...", update.version);
    let _ = app.emit("update-progress", "正在下载更新...");

    match update.download_and_install(
        |_chunk_length, _total| {},
        || {},
    ).await {
        Ok(()) => {
            eprintln!("[update] Update installed successfully");
            let _ = app.emit("update-progress", "更新已安装，重启后生效");
            Ok("更新已安装，重启应用后生效".to_string())
        }
        Err(e) => Err(format!("下载安装失败: {}", e)),
    }
}
