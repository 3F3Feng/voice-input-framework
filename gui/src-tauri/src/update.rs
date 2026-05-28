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

    let updater = match app.updater() {
        Ok(u) => u,
        Err(_) => {
            eprintln!("[update] Updater plugin not available");
            return Err("更新插件未启用，请检查配置".to_string());
        }
    };

    let maybe_update = updater
        .check()
        .await
        .map_err(|e| format!("检查更新失败: {}", e))?;

    match maybe_update {
        Some(update) => {
            let latest = update.version.clone();
            let available = latest != current;
            let body = update.body.clone().unwrap_or_default();
            eprintln!("[update] Current: {}, Latest: {}, available: {}", current, latest, available);
            Ok(UpdateInfo { available, current_version: current, latest_version: latest, body, download_size: 0 })
        }
        None => {
            eprintln!("[update] No update available (None)");
            Ok(UpdateInfo { available: false, current_version: current.clone(), latest_version: current, body: String::new(), download_size: 0 })
        }
    }
}

/// Download and install the update.
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
    let app_clone = app.clone();

    match update.download_and_install(
        move |chunk_length, total| {
            if total > 0 {
                let pct = (chunk_length as f64 / total as f64 * 100.0) as u32;
                let _ = app_clone.emit("update-progress", format!("下载中 {}%", pct));
            }
        },
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
