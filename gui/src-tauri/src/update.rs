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

use crate::i18n::t;
use crate::tr;

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

    let updater = app
        .updater()
        .map_err(|e| tr!("更新器不可用: {}", "Updater unavailable: {}", e))?;

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
                    .unwrap_or_else(|| tr!("版本 {}", "Version {}", update.version)),
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
        Err(e) => Err(tr!("检查更新失败: {}", "Couldn't check for updates: {}", e)),
    }
}

/// 装完更新、正在重启进新版本。见 `download_and_install` 末尾。
pub static RESTARTING_FOR_UPDATE: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// 下载并安装。装完退出应用,由用户重新打开。
///
/// 签名验不过时插件会在这里返回错误,**不会**装上去——这正是要它的原因。
pub async fn download_and_install(app: &tauri::AppHandle) -> Result<String, String> {
    // Windows 上插件拉起安装器后直接 `process::exit(0)`,不经过 `RunEvent::Exit`:
    // 不在这里删会话标记,装完更新后第一次启动就会误报「上次异常退出」。
    // 插件自己的钩子(`cleanup_before_exit`)会被这里覆盖,所以照样调一次。
    #[cfg(windows)]
    let updater = {
        let handle = app.clone();
        app.updater_builder()
            .on_before_exit(move || {
                crate::crash::end_session();
                handle.cleanup_before_exit();
            })
            .build()
    };
    #[cfg(not(windows))]
    let updater = app.updater();
    let updater = updater.map_err(|e| tr!("更新器不可用: {}", "Updater unavailable: {}", e))?;

    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => return Ok(t("已是最新版本", "You're up to date").to_string()),
        Err(e) => return Err(tr!("检查更新失败: {}", "Couldn't check for updates: {}", e)),
    };

    let version = update.version.clone();
    let _ = app.emit(
        "update-progress",
        t("正在下载更新...", "Downloading update…"),
    );

    // 进度回调只用来喂界面。两处要当心:
    //
    // 1. 回调是**每收到一块数据**就调一次,几十兆的包能有上千块。每块都 emit
    //    一次到 webview 会把 IPC 刷爆、界面反而卡住。所以只在百分比真的变了
    //    (或没有总长时每攒够 1 MB)才发一次。
    // 2. 总长度可能是 None(服务端没给 Content-Length),那时不要拿它算百分比,
    //    算出来的是假的;老老实实报已下载的字节数。
    let app_for_progress = app.clone();
    let mut downloaded: u64 = 0;
    let mut last_tick: u64 = u64::MAX;
    update
        .download_and_install(
            move |chunk, total| {
                downloaded += chunk as u64;
                let (tick, msg) = match total {
                    Some(total) if total > 0 => {
                        let pct = downloaded * 100 / total;
                        (pct, tr!("下载中 {}%", "Downloading {}%", pct))
                    }
                    _ => {
                        let mb = downloaded / (1024 * 1024);
                        (mb, tr!("下载中 {} MB", "Downloading {} MB", mb))
                    }
                };
                if tick != last_tick {
                    last_tick = tick;
                    let _ = app_for_progress.emit("update-progress", msg);
                }
            },
            || {},
        )
        .await
        .map_err(|e| tr!("更新失败: {}", "Update failed: {}", e))?;

    // 以前装完直接 exit(0),用户看到的是「应用自己关了」,还得自己再去打开。现在
    // 直接重启进新版本。
    //
    // 从非主线程调 `restart()` 时 Tauri 会先走一遍 `RunEvent::Exit`,而那里会停掉
    // 本地的 STT / LLM 服务;打上这个标记,退出处理就跳过停服务,新进程启动时按 pid
    // 记账把它们认领回来(`reclaim_orphans`),不用把几个 G 的模型重新加载一遍。
    // (Windows 上 NSIS 安装器会自己结束并重新拉起应用,走不到这里。)
    RESTARTING_FOR_UPDATE.store(true, std::sync::atomic::Ordering::SeqCst);
    eprintln!("[update] {} 已安装,重启应用", version);
    let _ = app.emit(
        "update-progress",
        t("安装完成，正在重启…", "Installed. Restarting…"),
    );
    tokio::time::sleep(std::time::Duration::from_millis(600)).await;
    app.restart()
}
