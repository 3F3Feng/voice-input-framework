//! 客户端日志:stderr + 内存环形缓冲 + 本地文件 + `gui-log` 事件。
//!
//! 以前只有 stderr 和一个 `gui-log` 事件,而前端**从来没监听过这个事件**——
//! 「快捷键创建失败(缺输入监控)」「Wayland 下快捷键不工作」「config.json 解析
//! 失败已备份」「自动启动失败」这些写得很清楚的诊断,一条都到不了日志页。
//! 就算前端去听,也还是会漏:`setup()` 里打的日志发生在 webview 加载之前,
//! 那时事件发出去没有任何人收。打包后的应用又没有终端,stderr 等于没有。
//!
//! 所以现在每一行同时进三个地方:
//! - 内存环形缓冲(最近 [`BUFFER_CAP`] 行):前端挂载后用 `get_gui_logs` 把
//!   启动阶段的补拉回去,之后靠 `gui-log` 事件实时追加;
//! - 应用日志目录下的文件(超过 [`FILE_CAP_BYTES`] 轮转一次):应用崩了、界面
//!   打不开时,用户还有东西可以发过来;
//! - stderr:开发时照旧能在终端里看到。
//!
//! 不起后台任务、不依赖 tokio:`setup()` 里、快捷键线程里、任何地方都能直接调。

use serde::Serialize;
use std::collections::VecDeque;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, OnceLock};
use tauri::{Emitter, Manager};

/// 内存里留多少行。和前端日志框的上限一致,补拉一次正好填满。
pub const BUFFER_CAP: usize = 500;
/// 单个日志文件的上限。超过就把它挪成 `.1`(只留一份旧的),防止一直长下去。
pub const FILE_CAP_BYTES: u64 = 1024 * 1024;
const FILE_NAME: &str = "voice-input.log";

/// 一行日志。`seq` 单调递增,前端靠它把「补拉的缓冲」和「补拉期间收到的事件」
/// 去重、接上,不会重复也不会漏。
#[derive(Debug, Clone, Serialize)]
pub struct LogLine {
    pub seq: u64,
    /// `INFO` / `ERROR`。
    pub level: String,
    /// 给界面看的整行:`HH:MM:SS [LEVEL] 消息`。文件里另外带上日期。
    pub text: String,
}

/// `get_gui_logs` 的返回:缓冲里的全部行 + 日志文件在哪。
#[derive(Debug, Clone, Serialize)]
pub struct LogSnapshot {
    pub lines: Vec<LogLine>,
    /// 当前日志文件的绝对路径。日志目录建不出来时为 `None`(只剩内存和 stderr)。
    pub file: Option<String>,
}

struct FileSink {
    file: File,
    path: PathBuf,
    size: u64,
}

struct LogState {
    next_seq: u64,
    lines: VecDeque<LogLine>,
    sink: Option<FileSink>,
}

static STATE: Mutex<LogState> = Mutex::new(LogState {
    next_seq: 1,
    lines: VecDeque::new(),
    sink: None,
});

static APP_HANDLE: OnceLock<tauri::AppHandle> = OnceLock::new();

/// 日志是最后一道诊断手段,不能因为某个线程在持锁时 panic 了就跟着哑掉。
fn state() -> MutexGuard<'static, LogState> {
    STATE.lock().unwrap_or_else(|e| e.into_inner())
}

/// 追加一项,超出上限时从头部丢最旧的。
fn push_bounded<T>(buf: &mut VecDeque<T>, item: T, cap: usize) {
    buf.push_back(item);
    while buf.len() > cap {
        buf.pop_front();
    }
}

/// 写入 `incoming` 字节之前要不要先轮转。空文件永远不轮转——哪怕一行就比上限
/// 还长,也要先写进去,否则这一行永远写不进任何文件。
fn needs_rotation(current: u64, incoming: u64, cap: u64) -> bool {
    current > 0 && current.saturating_add(incoming) > cap
}

fn open_append(path: &Path) -> std::io::Result<FileSink> {
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    let size = file.metadata().map(|m| m.len()).unwrap_or(0);
    Ok(FileSink {
        file,
        path: path.to_path_buf(),
        size,
    })
}

impl FileSink {
    fn write_line(&mut self, line: &str) {
        let bytes = line.len() as u64 + 1;
        if needs_rotation(self.size, bytes, FILE_CAP_BYTES) {
            let old = self.path.with_extension("log.1");
            // rename 或重新打开失败时继续往原句柄里追加:宁可超过上限也不丢日志。
            // (Windows 上旧的 `.1` 被别的程序占着时 rename 会失败,就是这种情况。)
            if fs::rename(&self.path, &old).is_ok() {
                if let Ok(fresh) = open_append(&self.path) {
                    *self = fresh;
                }
            }
        }
        if writeln!(self.file, "{}", line).is_ok() {
            self.size += bytes;
        }
    }
}

/// 初始化:记下 AppHandle,打开日志文件,并把 init 之前已经缓冲的行补写进文件。
///
/// 应在 `setup()` 一开头调用——读配置时的「解析失败已备份」就是在这之后打的。
pub fn init(app_handle: &tauri::AppHandle) {
    let _ = APP_HANDLE.set(app_handle.clone());

    let opened = app_handle
        .path()
        .app_log_dir()
        .map_err(|e| e.to_string())
        .and_then(|dir| {
            fs::create_dir_all(&dir).map_err(|e| format!("{}: {}", dir.display(), e))?;
            open_append(&dir.join(FILE_NAME)).map_err(|e| format!("{}: {}", dir.display(), e))
        });

    match opened {
        Ok(mut sink) => {
            let mut st = state();
            let date = chrono::Local::now().format("%Y-%m-%d");
            for l in &st.lines {
                sink.write_line(&format!("{} {}", date, l.text));
            }
            st.sink = Some(sink);
        }
        // 锁已释放再记:`__log_inner` 自己要拿这把锁。
        Err(e) => __log_inner(
            "ERROR",
            &format!("[log] 日志文件打不开,只保留内存日志: {}", e),
        ),
    }
}

#[doc(hidden)]
pub fn __log_inner(level: &str, msg: &str) {
    let now = chrono::Local::now();
    let text = format!("{} [{}] {}", now.format("%H:%M:%S"), level, msg);
    eprintln!("{}", text);

    let line = {
        let mut st = state();
        let line = LogLine {
            seq: st.next_seq,
            level: level.to_string(),
            text,
        };
        st.next_seq += 1;
        if let Some(sink) = st.sink.as_mut() {
            sink.write_line(&format!("{} {}", now.format("%Y-%m-%d"), line.text));
        }
        push_bounded(&mut st.lines, line.clone(), BUFFER_CAP);
        line
    };

    // 发事件放在锁外:emit 要走 IPC,不该让别的线程的日志排在它后面等。
    if let Some(app) = APP_HANDLE.get() {
        let _ = app.emit("gui-log", &line);
    }
}

/// 缓冲里的全部行,以及日志文件路径。
pub fn snapshot() -> LogSnapshot {
    let st = state();
    LogSnapshot {
        lines: st.lines.iter().cloned().collect(),
        file: st.sink.as_ref().map(|s| s.path.display().to_string()),
    }
}

/// 前端挂载时补拉启动阶段的日志。
#[tauri::command]
pub async fn get_gui_logs() -> Result<LogSnapshot, String> {
    Ok(snapshot())
}

/// 在系统文件管理器里打开日志目录。
///
/// 不走 shell 插件的 `open`:它的默认 scope 只放行 http(s) / mailto / tel,
/// 本地路径会被拒;为一个目录去放宽 scope 反而把前端能打开的东西变多了。
#[tauri::command]
pub async fn open_log_dir() -> Result<(), String> {
    let dir = {
        let st = state();
        st.sink
            .as_ref()
            .and_then(|s| s.path.parent().map(Path::to_path_buf))
    }
    .ok_or_else(|| {
        crate::i18n::t(
            "日志目录不可用(日志文件没能创建)",
            "Log folder unavailable (the log file couldn't be created)",
        )
        .to_string()
    })?;

    #[cfg(target_os = "macos")]
    let opener = "open";
    #[cfg(target_os = "windows")]
    let opener = "explorer";
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    let opener = "xdg-open";

    std::process::Command::new(opener)
        .arg(&dir)
        .spawn()
        .map(|_| ())
        .map_err(|e| crate::tr!("打不开 {}: {}", "Can't open {}: {}", dir.display(), e))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ring_buffer_keeps_only_the_newest() {
        let mut buf = VecDeque::new();
        for i in 0..(BUFFER_CAP + 37) {
            push_bounded(&mut buf, i, BUFFER_CAP);
        }
        assert_eq!(buf.len(), BUFFER_CAP);
        assert_eq!(buf.front(), Some(&37));
        assert_eq!(buf.back(), Some(&(BUFFER_CAP + 36)));
    }

    #[test]
    fn ring_buffer_below_cap_keeps_everything() {
        let mut buf = VecDeque::new();
        push_bounded(&mut buf, "a", 3);
        push_bounded(&mut buf, "b", 3);
        assert_eq!(buf.iter().copied().collect::<Vec<_>>(), vec!["a", "b"]);
    }

    #[test]
    fn rotation_threshold() {
        assert!(!needs_rotation(0, 10, 100));
        assert!(!needs_rotation(90, 10, 100));
        assert!(needs_rotation(91, 10, 100));
        // 空文件哪怕这一行超长也先写,不然它哪儿都进不去。
        assert!(!needs_rotation(0, 1000, 100));
        assert!(!needs_rotation(u64::MAX, 0, u64::MAX));
    }

    /// 真写文件:超过上限后旧内容挪到 `.log.1`,新文件从这一行重新开始。
    #[test]
    fn file_sink_rotates_into_dot_one() {
        let dir = std::env::temp_dir().join(format!("vif-log-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join(FILE_NAME);

        let mut sink = open_append(&path).unwrap();
        // 每行连换行符正好 1024 字节,写满上限一行不多一行不少。
        let line = "x".repeat(1023);
        let per_file = (FILE_CAP_BYTES / 1024) as usize;
        for _ in 0..per_file {
            sink.write_line(&line);
        }
        assert!(!dir.join("voice-input.log.1").exists());
        assert_eq!(fs::metadata(&path).unwrap().len(), FILE_CAP_BYTES);
        sink.write_line("after-rotation");

        let rotated = fs::read_to_string(dir.join("voice-input.log.1")).unwrap();
        assert_eq!(rotated.lines().count(), per_file);
        assert_eq!(fs::read_to_string(&path).unwrap(), "after-rotation\n");

        // 重新打开(下次启动)时接着已有大小算,不是从 0 开始。
        drop(sink);
        let reopened = open_append(&path).unwrap();
        assert_eq!(reopened.size, "after-rotation\n".len() as u64);
        let _ = fs::remove_dir_all(&dir);
    }
}
