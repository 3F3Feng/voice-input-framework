//! Auto-update via GitHub Releases.
//! Uses direct HTTP fetch for check (with 30s timeout), falls back to
//! tauri-plugin-updater for download + install.

use std::cmp::Ordering;
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
    let current_clean = current.trim_start_matches('v').to_string();
    let available = compare_versions(&latest, &current_clean) == Ordering::Greater;

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

/// Compare two semver strings ("2.0.4" vs "2.0.3").
fn compare_versions(a: &str, b: &str) -> Ordering {
    let a_parts: Vec<u32> = a.split('.').filter_map(|s| s.parse().ok()).collect();
    let b_parts: Vec<u32> = b.split('.').filter_map(|s| s.parse().ok()).collect();
    for i in 0..3 {
        let av = a_parts.get(i).copied().unwrap_or(0);
        let bv = b_parts.get(i).copied().unwrap_or(0);
        match av.cmp(&bv) {
            Ordering::Equal => continue,
            other => return other,
        }
    }
    Ordering::Equal
}

/// Get the download URL for the current platform from latest.json.
fn get_platform_key() -> &'static str {
    #[cfg(target_os = "windows")]
    { "windows-x86_64" }
    #[cfg(target_os = "linux")]
    { "linux-x86_64" }
    #[cfg(target_os = "macos")]
    { "darwin-aarch64" }
}

/// Download update file and trigger install.
/// Uses reqwest (like check()) instead of tauri-plugin-updater's internal client,
/// which has timeout issues with GitHub redirects.
pub async fn download_and_install(app: &tauri::AppHandle) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(300)) // 5min total timeout
        .build()
        .map_err(|e| format!("创建HTTP客户端失败: {}", e))?;

    // Fetch latest.json to get download URL
    eprintln!("[update] Fetching update manifest...");
    let manifest: ReleaseManifest = client
        .get(LATEST_JSON_URL)
        .send()
        .await
        .map_err(|e| format!("获取更新信息失败: {}", e))?
        .json()
        .await
        .map_err(|e| format!("解析更新信息失败: {}", e))?;

    let current = app.package_info().version.to_string();
    let latest = manifest.version.trim_start_matches('v').to_string();
    if compare_versions(&latest, &current.trim_start_matches('v')) != Ordering::Greater {
        return Ok("已是最新版本".to_string());
    }

    let platform_key = get_platform_key();
    let entry = manifest.platforms.get(platform_key)
        .ok_or_else(|| format!("当前平台({})没有可用更新", platform_key))?;

    let download_url = &entry.url;
    eprintln!("[update] Downloading {} from {}", manifest.version, download_url);
    let _ = app.emit("update-progress", "正在下载更新...");

    // Download file to temp path
    let resp = client
        .get(download_url)
        .send()
        .await
        .map_err(|e| format!("下载失败: {}", e))?;

    let total_size = resp.content_length().unwrap_or(0);
    let app_clone = app.clone();

    // Stream download with progress
    let mut downloaded: u64 = 0;
    let mut bytes: Vec<u8> = Vec::new();
    let mut stream = resp.bytes_stream();

    use futures_util::StreamExt;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| format!("下载中断: {}", e))?;
        downloaded += chunk.len() as u64;
        bytes.extend_from_slice(&chunk);

        if total_size > 0 {
            let pct = (downloaded as f64 / total_size as f64 * 100.0) as u32;
            let _ = app_clone.emit("update-progress", format!("下载中 {}%", pct));
        }
    }

    eprintln!("[update] Downloaded {} bytes", bytes.len());
    let _ = app.emit("update-progress", "下载完成，准备安装...");

    // Write to temp file
    let ext = if cfg!(target_os = "windows") { ".exe" } else if cfg!(target_os = "macos") { ".dmg" } else { ".AppImage" };
    let temp_dir = std::env::temp_dir();
    let temp_path = temp_dir.join(format!("vif-update-{}{}", manifest.version, ext));

    // Remove old file if it exists
    let _ = std::fs::remove_file(&temp_path);
    std::fs::write(&temp_path, &bytes)
        .map_err(|e| format!("写入临时文件失败: {}", e))?;

    eprintln!("[update] Saved to {:?}", temp_path);

    // Mark as executable on Linux/macOS
    #[cfg(not(target_os = "windows"))]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&temp_path, std::fs::Permissions::from_mode(0o755))
            .map_err(|e| format!("设置执行权限失败: {}", e))?;
    }

    // Launch the installer
    #[cfg(target_os = "windows")]
    {
        let _ = std::process::Command::new(&temp_path)
            .spawn()
            .map_err(|e| format!("启动安装程序失败: {}", e))?;
    }
    #[cfg(target_os = "linux")]
    {
        let _ = std::process::Command::new(&temp_path)
            .arg("--no-sandbox")
            .spawn()
            .map_err(|e| format!("启动安装程序失败: {}", e))?;
    }
    #[cfg(target_os = "macos")]
    {
        let _ = std::process::Command::new("open")
            .arg(&temp_path)
            .spawn()
            .map_err(|e| format!("启动安装程序失败: {}", e))?;
    }

    eprintln!("[update] Installer launched, exiting app");
    let _ = app.emit("update-progress", "安装程序已启动，应用即将关闭");

    // Exit app to allow installer to replace files
    app.exit(0);
    Ok("更新已安装".to_string())
}
