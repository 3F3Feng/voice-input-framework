//! 经 GitHub Releases 自动更新。
//!
//! 走的是 `tauri-plugin-updater`,而不是自己拿 reqwest 下载再启动安装器。
//!
//! 以前是后者,代价有三条,每一条都足以让「更新」这件事名存实亡:
//!
//! 1. **签名从来没验过。** `latest.json` 里的 `signature` 字段被读进来又原样丢掉,
//!    下载到什么就执行什么。配置里那把 updater pubkey 纯属摆设。
//! 2. **产物类型是猜的。** 代码按操作系统硬编码扩展名(macOS 一律 `.dmg`),再
//!    `open` 一下。macOS 上这只是弹出一个磁盘映像让用户自己把 .app 拖进
//!    「应用程序」——那不叫自动更新,那叫手动重装。
//! 3. **和发版流程对不上。** 发版侧现在产出的是更新器专用产物(macOS 的
//!    `.app.tar.gz`、Linux 的 `.AppImage.tar.gz`),`open` 一个 tar.gz 只会解压到
//!    下载目录,什么也不会更新。
//!
//! 插件把这三件事一次性解决:验签名(验不过就拒绝安装)、认得每个平台该拿哪种
//! 产物、并且在 macOS / Linux 上是**原地替换**当前这个应用包,不需要用户再动手。
//!
//! 端点和公钥都在 `tauri.conf.json` 的 `plugins.updater` 里,这里不再重复一份。

use serde::Serialize;
use tauri::Emitter;
use tauri_plugin_updater::UpdaterExt;

/// 更新检查结果。字段保持原样——前端 `App.vue` 按这个形状读。
#[derive(Serialize, Clone)]
pub struct UpdateInfo {
    pub available: bool,
    pub current_version: String,
    pub latest_version: String,
    pub body: String,
    pub download_size: u64,
}

/// 查一次有没有新版本。不下载任何东西。
pub async fn check(app: &tauri::AppHandle) -> Result<UpdateInfo, String> {
    let current = app.package_info().version.to_string();

    let updater = app.updater().map_err(|e| format!("更新器不可用: {}", e))?;

    match updater.check().await {
        Ok(Some(update)) => {
            eprintln!(
                "[update] current={} latest={} → 有更新",
                update.current_version, update.version
            );
            Ok(UpdateInfo {
                available: true,
                latest_version: update.version.clone(),
                current_version: update.current_version.clone(),
                body: update
                    .body
                    .clone()
                    .unwrap_or_else(|| format!("版本 {}", update.version)),
                // 清单里没有体积,下载时才知道。留 0,前端本来也没用它。
                download_size: 0,
            })
        }
        Ok(None) => {
            eprintln!("[update] current={} → 已是最新", current);
            Ok(UpdateInfo {
                available: false,
                latest_version: current.clone(),
                current_version: current,
                body: String::new(),
                download_size: 0,
            })
        }
        Err(e) => Err(format!("检查更新失败: {}", e)),
    }
}

/// 下载并安装。装完退出应用,由用户重新打开。
///
/// 签名验不过时插件会在这里返回错误,**不会**装上去——这正是要它的原因。
pub async fn download_and_install(app: &tauri::AppHandle) -> Result<String, String> {
    let updater = app.updater().map_err(|e| format!("更新器不可用: {}", e))?;

    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => return Ok("已是最新版本".to_string()),
        Err(e) => return Err(format!("检查更新失败: {}", e)),
    };

    let version = update.version.clone();
    let _ = app.emit("update-progress", "正在下载更新...");

    // 进度回调只用来喂界面。总长度可能是 None(服务端没给 Content-Length),
    // 那时就只报已下载的字节数,不要算出一个假的百分比。
    let app_for_progress = app.clone();
    let mut downloaded: u64 = 0;
    update
        .download_and_install(
            move |chunk, total| {
                downloaded += chunk as u64;
                let msg = match total {
                    Some(total) if total > 0 => {
                        format!("下载中 {}%", downloaded * 100 / total)
                    }
                    _ => format!("下载中 {} KB", downloaded / 1024),
                };
                let _ = app_for_progress.emit("update-progress", msg);
            },
            || {},
        )
        .await
        .map_err(|e| format!("更新失败: {}", e))?;

    eprintln!("[update] {} 已安装,退出应用", version);
    let _ = app.emit("update-progress", "安装完成，应用即将关闭");
    app.exit(0);
    Ok(format!("已更新到 {}，请重新打开应用", version))
}
