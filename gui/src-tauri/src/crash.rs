//! 上次异常退出:panic 进日志、会话标记、找系统崩溃报告、告诉用户。
//!
//! v2.3.1 连着两次 SIGTRAP 闪退(非主线程调了只许主线程调的系统 API),客户端日志
//! 停在半截,下次启动一切如常——谁也不知道崩过。最后是手动翻
//! `~/Library/Logs/DiagnosticReports/Retired/*.ips` 才找到原因。这里补三件事:
//!
//! 1. **Rust panic 进日志**([`install_panic_hook`])。非主线程的 panic 不会让进程退出,
//!    以前只打到没人看的 stderr,现在带线程名和调用栈写进日志文件。
//! 2. **异常退出靠会话标记发现**。信号崩溃(SIGTRAP / SIGSEGV / abort)不经过任何钩子,
//!    只能反过来做:启动时写一个 `sessions/session-<pid>.json`,正常退出时删掉
//!    ([`end_session`])。下次启动时还留着、而且那个 pid 已经不是在跑的本应用,
//!    就说明上次没走到正常退出。
//!
//!    标记按 pid 分文件,不是一个固定文件名:装完更新重启时新旧两个进程会短暂并存,
//!    共用一个文件的话,旧进程退出时删掉的可能是新进程刚写的那份。
//! 3. **找系统的崩溃报告并说明原因**。macOS 的 `.ips` 解析出异常类型和出错线程的栈顶;
//!    Windows 只定位 WER 报告 / dump;Linux 没有标准位置,只记一行日志。找到报告才在
//!    主界面提示一次;没有报告(强制退出、断电、被系统结束)只记日志,不打扰用户。
//!
//! 扫描都在后台线程里做,只看比上次启动更新的文件,并且限量。崩溃报告只读,绝不删改。

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::Emitter;

use crate::i18n::t;

// ───────────────────────── panic 钩子 ─────────────────────────

/// 调用栈最多记这么多行。release 包里一次 panic 的栈常有上百帧,大半是 tokio / tauri
/// 的调度层,全记下来会把日志文件的 1 MB 额度吃掉一截。
const BACKTRACE_MAX_LINES: usize = 80;

/// 在 `run()` 一开头装:越早装,能兜住的 panic 越多(包括 `setup()` 里的)。
///
/// 记完日志再交给原来的钩子,stderr 上的输出和以前一样。
pub fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let thread = std::thread::current();
        let thread_name = thread.name().unwrap_or("<unnamed>").to_string();
        let message = info
            .payload_as_str()
            .unwrap_or("<非字符串 panic 负载>")
            .to_string();
        let location = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "?".into());
        let backtrace = std::backtrace::Backtrace::force_capture().to_string();
        let text = format!(
            "[panic] 线程「{}」panic: {}\n  位置: {}\n{}",
            thread_name,
            message,
            location,
            truncate_lines(&backtrace, BACKTRACE_MAX_LINES)
        );
        // panic 恰好发生在写日志的过程中时(这个线程已经拿着日志锁),再去写日志只会
        // 死锁,只能退回 stderr。
        if crate::log::is_logging_on_this_thread() {
            eprintln!("{}", text);
        } else {
            // 日志文件是逐行直接 write 的(没有用户态缓冲),这一行返回时已经进了内核,
            // 进程紧接着被 abort 也不会丢。
            crate::log_error!("{}", text);
        }
        previous(info);
    }));
}

fn truncate_lines(text: &str, max: usize) -> String {
    let total = text.lines().count();
    let mut out: Vec<&str> = text.lines().take(max).collect();
    let omitted = total.saturating_sub(max);
    let tail = format!("  …(省略 {} 行)", omitted);
    if omitted > 0 {
        out.push(&tail);
    }
    out.join("\n")
}

// ───────────────────────── 会话标记 ─────────────────────────

/// 每次启动写一份,正常退出时删掉。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SessionMarker {
    pub pid: u32,
    pub version: String,
    pub build_id: String,
    /// 启动时刻,Unix 秒。找崩溃报告时只看比它新的文件。
    pub started_at: i64,
}

const SESSION_DIR: &str = "sessions";
const MARKER_PREFIX: &str = "session-";
const MARKER_EXT: &str = ".json";
const LAST_CRASH_FILE: &str = "last-crash.json";

fn marker_path(dir: &Path, pid: u32) -> PathBuf {
    dir.join(format!("{}{}{}", MARKER_PREFIX, pid, MARKER_EXT))
}

/// `session-1234.json` → 1234。别的文件(包括 `last-crash.json`)返回 None。
fn marker_pid_from_name(name: &str) -> Option<u32> {
    name.strip_prefix(MARKER_PREFIX)?
        .strip_suffix(MARKER_EXT)?
        .parse()
        .ok()
}

fn write_marker(dir: &Path, marker: &SessionMarker) -> std::io::Result<PathBuf> {
    fs::create_dir_all(dir)?;
    let path = marker_path(dir, marker.pid);
    let json = serde_json::to_string(marker).map_err(std::io::Error::other)?;
    fs::write(&path, json)?;
    Ok(path)
}

/// 目录里除 `own_pid` 以外的全部标记,按启动时间从旧到新。
///
/// 内容坏了(比如写到一半就崩了)的标记也要算上:pid 从文件名里拿,启动时间退回
/// 文件的修改时间——宁可多报一次「异常退出」,也不能因为标记坏了就当没发生。
fn stale_markers(dir: &Path, own_pid: u32) -> Vec<(PathBuf, SessionMarker)> {
    let Ok(entries) = fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<(PathBuf, SessionMarker)> = entries
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            let pid = marker_pid_from_name(&name)?;
            if pid == own_pid {
                return None;
            }
            let path = e.path();
            let parsed = fs::read_to_string(&path)
                .ok()
                .and_then(|s| serde_json::from_str::<SessionMarker>(&s).ok())
                .filter(|m| m.pid == pid);
            let marker = parsed.unwrap_or_else(|| SessionMarker {
                pid,
                version: "?".into(),
                build_id: "?".into(),
                started_at: e
                    .metadata()
                    .and_then(|m| m.modified())
                    .map(unix_secs)
                    .unwrap_or(0),
            });
            Some((path, marker))
        })
        .collect();
    out.sort_by_key(|(_, m)| m.started_at);
    out
}

fn unix_secs(t: SystemTime) -> i64 {
    t.duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn from_unix_secs(secs: i64) -> SystemTime {
    UNIX_EPOCH + Duration::from_secs(secs.max(0) as u64)
}

fn format_local(secs: i64) -> String {
    use chrono::TimeZone;
    chrono::Local
        .timestamp_opt(secs, 0)
        .single()
        .map(|d| d.format("%Y-%m-%d %H:%M:%S").to_string())
        .unwrap_or_else(|| "?".into())
}

struct Session {
    dir: PathBuf,
    pid: u32,
}

static SESSION: OnceLock<Session> = OnceLock::new();

/// 这次启动检测到、还没被用户关掉的那次崩溃。只有找到了崩溃报告才会放进来。
static PENDING: Mutex<Option<CrashRecord>> = Mutex::new(None);

fn pending() -> std::sync::MutexGuard<'static, Option<CrashRecord>> {
    PENDING.lock().unwrap_or_else(|e| e.into_inner())
}

/// 启动时调用:写下本次的会话标记,然后在后台线程里检查上次有没有异常退出。
///
/// `data_dir` 是应用数据目录;标记放在它下面的 `sessions/`。
pub fn start_session(data_dir: &Path, app: tauri::AppHandle) {
    let dir = data_dir.join(SESSION_DIR);
    let build = crate::BuildInfo::current();
    let marker = SessionMarker {
        pid: std::process::id(),
        version: build.version,
        build_id: build.build_id,
        started_at: unix_secs(SystemTime::now()),
    };
    if let Err(e) = write_marker(&dir, &marker) {
        crate::log_error!(
            "[crash] 会话标记写不进 {}: {};本次异常退出将无法在下次启动时发现",
            dir.display(),
            e
        );
        return;
    }
    let _ = SESSION.set(Session {
        dir: dir.clone(),
        pid: marker.pid,
    });
    let spawned = std::thread::Builder::new()
        .name("crash-check".into())
        .spawn(move || check_previous_sessions(&dir, marker.pid, &app));
    if let Err(e) = spawned {
        crate::log_error!("[crash] 检查上次退出情况的线程起不来: {}", e);
    }
}

/// 正常退出时调用(`RunEvent::Exit`,包括装完更新重启;Windows 的更新安装器退出前
/// 也会调)。可以重复调。
pub fn end_session() {
    if let Some(s) = SESSION.get() {
        let _ = fs::remove_file(marker_path(&s.dir, s.pid));
    }
}

/// 系统写崩溃报告要时间:实测 macOS 在进程死后约 30 秒才把 `.ips` 落盘。用户崩完
/// 马上重开,第一次扫描多半扫不到,所以隔一阵再扫几次(累计约两分钟)。
const RESCAN_DELAYS: [u64; 5] = [0, 10, 20, 30, 60];

fn check_previous_sessions(dir: &Path, own_pid: u32, app: &tauri::AppHandle) {
    let stale = stale_markers(dir, own_pid);
    let count = stale.len();
    for (i, (path, marker)) in stale.into_iter().enumerate() {
        if pid_is_running_instance(marker.pid) {
            crate::log_info!(
                "[crash] 会话标记 pid {} 对应的进程还在运行,不算异常退出",
                marker.pid
            );
            continue;
        }
        // 先删标记再找报告:这次找没找到都只报这一回,下次启动不会再为它提示。
        let _ = fs::remove_file(&path);
        crate::log_warn!(
            "[crash] 上次会话没有正常退出(v{} · build {} · pid {} · 启动于 {}),正在找系统崩溃报告",
            marker.version,
            marker.build_id,
            marker.pid,
            format_local(marker.started_at)
        );

        // 只有最近那一次值得等报告落盘;更早的(连着几次都没正常退出,少见)扫一遍就算。
        let newest = i + 1 == count;
        let delays: &[u64] = if newest { &RESCAN_DELAYS } else { &[0] };
        let mut report = None;
        for &d in delays {
            std::thread::sleep(Duration::from_secs(d));
            report = find_report(&marker);
            if report.is_some() {
                break;
            }
        }

        let record = CrashRecord {
            session: marker,
            report,
            detected_at: unix_secs(SystemTime::now()),
        };
        if record.report.is_some() {
            crate::log_error!("[crash] {}", format_summary(&record, false));
        } else {
            crate::log_warn!(
                "[crash] 上次会话异常退出,但没找到系统崩溃报告(强制退出、断电、被系统结束时都没有),不在界面上提示"
            );
        }
        persist_last_crash(dir, &record);
        if newest && record.report.is_some() {
            *pending() = Some(record.clone());
            let _ = app.emit("last-crash", notice_for(&record));
        }
    }
}

fn persist_last_crash(dir: &Path, record: &CrashRecord) {
    if let Ok(json) = serde_json::to_string_pretty(record) {
        let _ = fs::write(dir.join(LAST_CRASH_FILE), json);
    }
}

fn load_last_crash(dir: &Path) -> Option<CrashRecord> {
    let s = fs::read_to_string(dir.join(LAST_CRASH_FILE)).ok()?;
    serde_json::from_str(&s).ok()
}

/// 这个 pid 现在是不是一个在跑的本应用。只看「活着」不够:重启过电脑之后 pid 早被
/// 别的程序用上了,那样会把一次真实的崩溃当成「还在运行」永远跳过。
#[cfg(unix)]
fn pid_is_running_instance(pid: u32) -> bool {
    std::process::Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "comm="])
        .output()
        .map(|o| comm_is_us(&String::from_utf8_lossy(&o.stdout), &exe_stem()))
        .unwrap_or(false)
}

#[cfg(windows)]
fn pid_is_running_instance(pid: u32) -> bool {
    let mut cmd = std::process::Command::new("tasklist");
    cmd.args(["/FI", &format!("PID eq {}", pid), "/FO", "CSV", "/NH"]);
    crate::server_manager::no_console(&mut cmd)
        .output()
        .map(|o| {
            // 形如 `"voice-input.exe","1234",...`;没有匹配时是一行不带引号的 INFO 提示。
            let out = String::from_utf8_lossy(&o.stdout);
            let stem = exe_stem().to_lowercase();
            out.lines().any(|l| {
                let mut f = l.split("\",\"").map(|s| s.trim_matches('"').trim());
                let image = f.next().unwrap_or("").to_lowercase();
                let p = f.next().and_then(|s| s.parse::<u32>().ok());
                p == Some(pid) && image.starts_with(&stem)
            })
        })
        .unwrap_or(false)
}

/// `ps -o comm=` 的输出是不是本应用。macOS 给的是可执行文件的完整路径,Linux 给的是
/// 截到 15 个字符的进程名,所以比较最后一段、并容忍截断。
#[cfg_attr(windows, allow(dead_code))]
fn comm_is_us(comm: &str, stem: &str) -> bool {
    let comm = comm.trim();
    if comm.is_empty() {
        return false;
    }
    let last = comm.rsplit('/').next().unwrap_or(comm);
    last == stem || (last.len() >= 15 && stem.starts_with(last))
}

/// 本应用可执行文件的名字(不带扩展名),崩溃报告的文件名以它开头。
fn exe_stem() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
        .unwrap_or_else(|| "voice-input".into())
}

// ───────────────────────── 找崩溃报告 ─────────────────────────

/// 一次异常退出的全部已知信息。落盘成 `last-crash.json`,诊断信息里也带上它。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CrashRecord {
    pub session: SessionMarker,
    pub report: Option<FoundReport>,
    /// 发现它的时刻,Unix 秒。
    pub detected_at: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FoundReport {
    pub path: String,
    /// 解析出来的内容。Windows 的报告只定位不解析,这里是 None。
    pub details: Option<IpsDetails>,
}

#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct IpsDetails {
    pub pid: Option<u32>,
    pub app_version: Option<String>,
    pub os_version: Option<String>,
    /// `EXC_BREAKPOINT` 之类。
    pub exception_type: Option<String>,
    /// `SIGTRAP` 之类。
    pub signal: Option<String>,
    /// `Trace/BPT trap: 5` 之类。
    pub termination: Option<String>,
    /// 系统附带的说明(`asi`),比如「Must only be used from the main thread」。
    pub notes: Vec<String>,
    pub thread_index: Option<usize>,
    pub thread_name: Option<String>,
    /// 摘出来的栈帧(见 [`pick_frames`]),符号已尽量还原成可读的 Rust 路径。
    pub frames: Vec<FrameLine>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FrameLine {
    /// 在出错线程栈里的序号(0 = 栈顶)。
    pub index: usize,
    pub image: String,
    pub symbol: String,
}

/// 一次最多细看几个候选文件。正常只有一个;这只是防备目录里堆了一大堆。
// Linux 不找报告,下面这些只在 macOS / Windows 上用得到。
#[cfg_attr(not(any(target_os = "macos", windows)), allow(dead_code))]
const MAX_CANDIDATES: usize = 5;
/// 比这还大的不是我们认得的报告,不读。真实的 `.ips` 约 100 KB。
#[cfg_attr(not(any(target_os = "macos", windows)), allow(dead_code))]
const MAX_REPORT_BYTES: u64 = 16 * 1024 * 1024;
/// 文件系统时间戳的粒度和时钟误差留一点余量。
#[cfg_attr(not(any(target_os = "macos", windows)), allow(dead_code))]
const MTIME_SLACK_SECS: i64 = 2;

#[cfg_attr(not(any(target_os = "macos", windows)), allow(dead_code))]
#[derive(Debug, Clone)]
struct Candidate {
    path: PathBuf,
    modified: SystemTime,
}

/// 在 `dirs` 里找名字以 `prefix` 开头(不分大小写)、以 `suffix` 结尾、修改时间不早于
/// `since` 的条目,从新到旧,最多 `cap` 个。目录不存在就跳过。
#[cfg_attr(not(any(target_os = "macos", windows)), allow(dead_code))]
fn list_candidates(
    dirs: &[PathBuf],
    prefix: &str,
    suffix: &str,
    since: SystemTime,
    cap: usize,
) -> Vec<Candidate> {
    let prefix = prefix.to_lowercase();
    let suffix = suffix.to_lowercase();
    let mut out: Vec<Candidate> = dirs
        .iter()
        .filter_map(|d| fs::read_dir(d).ok())
        .flat_map(|rd| rd.flatten())
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_lowercase();
            if !name.starts_with(&prefix) || !name.ends_with(&suffix) {
                return None;
            }
            let meta = e.metadata().ok()?;
            let modified = meta.modified().ok()?;
            if modified < since || (meta.is_file() && meta.len() > MAX_REPORT_BYTES) {
                return None;
            }
            Some(Candidate {
                path: e.path(),
                modified,
            })
        })
        .collect();
    out.sort_by_key(|c| std::cmp::Reverse(c.modified));
    out.truncate(cap);
    out
}

#[cfg_attr(not(any(target_os = "macos", windows)), allow(dead_code))]
fn since_for(marker: &SessionMarker) -> SystemTime {
    from_unix_secs(marker.started_at - MTIME_SLACK_SECS)
}

/// 从候选 `.ips` 里挑出属于这次会话的那份:报告里的 pid 对得上的优先;解析不了的
/// (格式变了)退而求其次,总比什么都不给强;pid 明确对不上的不要——那是别的会话的。
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn choose_ips(candidates: &[Candidate], pid: u32) -> Option<FoundReport> {
    let mut fallback = None;
    for c in candidates {
        let parsed = fs::read_to_string(&c.path)
            .ok()
            .and_then(|s| parse_ips(&s).ok());
        let path = c.path.display().to_string();
        match parsed {
            Some(d) if d.pid == Some(pid) || d.pid.is_none() => {
                return Some(FoundReport {
                    path,
                    details: Some(d),
                })
            }
            Some(_) => {}
            None if fallback.is_none() => {
                fallback = Some(FoundReport {
                    path,
                    details: None,
                })
            }
            None => {}
        }
    }
    fallback
}

#[cfg(target_os = "macos")]
fn find_report(marker: &SessionMarker) -> Option<FoundReport> {
    let home = crate::server_manager::home_dir()?;
    let base = home.join("Library/Logs/DiagnosticReports");
    let dirs = [base.join("Retired"), base];
    let prefix = format!("{}-", exe_stem());
    let found = list_candidates(&dirs, &prefix, ".ips", since_for(marker), MAX_CANDIDATES);
    choose_ips(&found, marker.pid)
}

/// Windows 只定位:开了 LocalDumps 时 `%LOCALAPPDATA%\CrashDumps` 里有 dump(文件名
/// 带 pid),否则 WER 在 `ReportArchive` / `ReportQueue` 下为每次崩溃建一个目录
/// (`AppCrash_voice-input.exe_…`)。dump 和 `Report.wer` 都不解析。
#[cfg(windows)]
fn find_report(marker: &SessionMarker) -> Option<FoundReport> {
    let local = PathBuf::from(std::env::var_os("LOCALAPPDATA")?);
    let stem = exe_stem();
    let since = since_for(marker);
    let dumps = list_candidates(
        &[local.join("CrashDumps")],
        &stem,
        ".dmp",
        since,
        MAX_CANDIDATES,
    );
    // `voice-input.exe.1234.dmp`:pid 对得上的优先。
    let pid_tag = format!(".{}.dmp", marker.pid);
    if let Some(c) = dumps
        .iter()
        .find(|c| c.path.to_string_lossy().to_lowercase().ends_with(&pid_tag))
        .or(dumps.first())
    {
        return Some(FoundReport {
            path: c.path.display().to_string(),
            details: None,
        });
    }
    let wer = local.join(r"Microsoft\Windows\WER");
    let prefix = format!("appcrash_{}", stem);
    list_candidates(
        &[wer.join("ReportArchive"), wer.join("ReportQueue")],
        &prefix,
        "",
        since,
        MAX_CANDIDATES,
    )
    .into_iter()
    .next()
    .map(|c| FoundReport {
        path: c.path.display().to_string(),
        details: None,
    })
}

/// Linux 没有标准的崩溃报告位置(systemd-coredump / apport 各发行版不一样),只报异常退出。
#[cfg(all(not(target_os = "macos"), not(windows)))]
fn find_report(_marker: &SessionMarker) -> Option<FoundReport> {
    None
}

// ───────────────────────── 解析 .ips ─────────────────────────

/// 出错线程的栈顶看几帧。
const TOP_FRAMES: usize = 8;
/// 栈顶之外,再补几帧我们自己代码里离崩溃最近的(栈顶常常全是系统库)。
const EXTRA_OWN_FRAMES: usize = 3;
/// 认得出「我们自己的代码」的符号片段(lib crate 名)。
const OWN_CRATE: &str = "voice_input";

/// `.ips`:第一行是一小段 JSON 头(app_name、app_version、os_version…),其余是正文 JSON。
/// 老格式或者被别的工具改过的文件可能只有一段,那就整段当正文。
pub fn parse_ips(text: &str) -> Result<IpsDetails, String> {
    let (header_line, rest) = text.split_once('\n').unwrap_or((text, ""));
    let header: serde_json::Value = serde_json::from_str(header_line.trim()).unwrap_or_default();
    let body: serde_json::Value = if rest.trim().is_empty() {
        serde_json::from_str(text).map_err(|e| e.to_string())?
    } else {
        serde_json::from_str(rest).map_err(|e| e.to_string())?
    };
    let s = |v: &serde_json::Value| v.as_str().map(str::to_string);

    let exception = &body["exception"];
    let notes = match &body["asi"] {
        serde_json::Value::Object(m) => m
            .values()
            .filter_map(|v| v.as_array())
            .flatten()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect(),
        _ => Vec::new(),
    };
    let thread_index = body["faultingThread"].as_u64().map(|n| n as usize);
    let thread = thread_index.map(|i| &body["threads"][i]);

    let images: Vec<Option<String>> = body["usedImages"]
        .as_array()
        .map(|a| a.iter().map(|img| s(&img["name"])).collect())
        .unwrap_or_default();
    let proc_name = s(&body["procName"]).or_else(|| s(&header["name"]));
    let main_image = proc_name
        .clone()
        .or_else(|| images.first().cloned().flatten());

    let raw_frames: Vec<RawFrame> = thread
        .and_then(|t| t["frames"].as_array())
        .map(|frames| {
            frames
                .iter()
                .map(|f| {
                    let image = f["imageIndex"]
                        .as_u64()
                        .and_then(|i| images.get(i as usize).cloned().flatten())
                        .unwrap_or_else(|| "???".into());
                    let symbol = match s(&f["symbol"]) {
                        Some(sym) => demangle(&sym),
                        None => format!("0x{:x}", f["imageOffset"].as_u64().unwrap_or(0)),
                    };
                    RawFrame {
                        is_main: main_image.as_deref() == Some(image.as_str()),
                        image,
                        symbol,
                    }
                })
                .collect()
        })
        .unwrap_or_default();

    Ok(IpsDetails {
        pid: body["pid"].as_u64().map(|p| p as u32),
        app_version: s(&header["app_version"])
            .or_else(|| s(&body["bundleInfo"]["CFBundleShortVersionString"])),
        os_version: s(&header["os_version"]).or_else(|| s(&body["osVersion"]["train"])),
        exception_type: s(&exception["type"]),
        signal: s(&exception["signal"]),
        termination: s(&body["termination"]["indicator"]),
        notes,
        thread_index,
        thread_name: thread.and_then(|t| s(&t["name"]).or_else(|| s(&t["queue"]))),
        frames: pick_frames(&raw_frames),
    })
}

struct RawFrame {
    image: String,
    symbol: String,
    is_main: bool,
}

/// 栈顶 [`TOP_FRAMES`] 帧,再补上更深处我们自己代码里最靠前的 [`EXTRA_OWN_FRAMES`] 帧。
///
/// 只看栈顶不够:v2.3.1 那次栈顶 6 帧全是 libdispatch / HIToolbox,真正该改的
/// `input::press_paste` 在第 10 帧。
fn pick_frames(frames: &[RawFrame]) -> Vec<FrameLine> {
    let line = |(i, f): (usize, &RawFrame)| FrameLine {
        index: i,
        image: f.image.clone(),
        symbol: f.symbol.clone(),
    };
    let mut out: Vec<FrameLine> = frames
        .iter()
        .enumerate()
        .take(TOP_FRAMES)
        .map(line)
        .collect();
    out.extend(
        frames
            .iter()
            .enumerate()
            .skip(TOP_FRAMES)
            .filter(|(_, f)| f.is_main && f.symbol.contains(OWN_CRATE))
            .take(EXTRA_OWN_FRAMES)
            .map(line),
    );
    out
}

// ───────────────────────── 符号还原 ─────────────────────────

/// 把 Rust 的符号名还原成 `crate::module::fn`。认不出的原样返回。
///
/// 不引 `rustc-demangle`:报告里我们关心的只是自家代码那几帧的路径,覆盖常见形态
/// (嵌套路径、闭包、旧式 `_ZN…E`)就够了;泛型实例、trait impl 之类的复杂符号原样给出。
pub fn demangle(sym: &str) -> String {
    let v0 = ["__R", "_R", "R"]
        .iter()
        .find_map(|p| sym.strip_prefix(p))
        .filter(|rest| rest.starts_with(|c: char| c.is_ascii_uppercase()))
        .and_then(|rest| {
            V0 {
                s: rest.as_bytes(),
                i: 0,
            }
            .path()
        });
    if let Some(s) = v0 {
        return s;
    }
    let legacy = ["__ZN", "_ZN", "ZN"]
        .iter()
        .find_map(|p| sym.strip_prefix(p))
        .and_then(demangle_legacy);
    if let Some(s) = legacy {
        return s;
    }
    // 系统已经替我们还原过的旧式符号(`crate::f::h0123…`):只剩末尾的哈希段要去掉。
    match sym.rsplit_once("::") {
        Some((path, last)) if is_hash_segment(last) => path.to_string(),
        _ => sym.to_string(),
    }
}

/// 旧式修饰末尾的 `h` + 16 位十六进制哈希。
fn is_hash_segment(s: &str) -> bool {
    s.len() == 17 && s.starts_with('h') && s[1..].chars().all(|c| c.is_ascii_hexdigit())
}

/// v0 修饰(RFC 2603)里 `C`(crate 根)和 `N`(嵌套路径)这一小部分。
struct V0<'a> {
    s: &'a [u8],
    i: usize,
}

impl V0<'_> {
    fn peek(&self) -> Option<u8> {
        self.s.get(self.i).copied()
    }
    fn next(&mut self) -> Option<u8> {
        let c = self.peek()?;
        self.i += 1;
        Some(c)
    }
    fn eat(&mut self, c: u8) -> bool {
        if self.peek() == Some(c) {
            self.i += 1;
            true
        } else {
            false
        }
    }
    /// `{0-9a-zA-Z} "_"`,只跳过,不关心值。
    fn skip_base62(&mut self) -> Option<()> {
        loop {
            match self.next()? {
                b'_' => return Some(()),
                c if c.is_ascii_alphanumeric() => {}
                _ => return None,
            }
        }
    }
    fn decimal(&mut self) -> Option<usize> {
        let start = self.i;
        if self.eat(b'0') {
            return Some(0);
        }
        while self.peek().is_some_and(|c| c.is_ascii_digit()) {
            self.i += 1;
        }
        std::str::from_utf8(&self.s[start..self.i])
            .ok()?
            .parse()
            .ok()
    }
    /// `[s <base62>] [u] <decimal> [_] <bytes>`。punycode(`u`)不还原。
    fn ident(&mut self) -> Option<&str> {
        if self.eat(b's') {
            self.skip_base62()?;
        }
        if self.eat(b'u') {
            return None;
        }
        let n = self.decimal()?;
        self.eat(b'_');
        let bytes = self.s.get(self.i..self.i + n)?;
        self.i += n;
        std::str::from_utf8(bytes).ok()
    }
    fn path(&mut self) -> Option<String> {
        match self.next()? {
            b'C' => self.ident().map(str::to_string),
            b'N' => {
                let ns = self.next()?;
                let parent = self.path()?;
                let name = self.ident()?;
                Some(match ns {
                    b'C' => format!("{}::{{closure}}", parent),
                    b'S' => format!("{}::{{shim}}", parent),
                    c if c.is_ascii_lowercase() => format!("{}::{}", parent, name),
                    c => format!("{}::{{{}}}", parent, c as char),
                })
            }
            // impl 路径、泛型参数、回引用:要解析类型,这里不做。
            _ => None,
        }
    }
}

/// 旧式 `_ZN 3foo 3bar 17h0123456789abcdef E`:去掉末尾的哈希段,转回 `::` 和常见转义。
fn demangle_legacy(rest: &str) -> Option<String> {
    let b = rest.as_bytes();
    let mut i = 0;
    let mut parts: Vec<&str> = Vec::new();
    while b.get(i) != Some(&b'E') {
        let start = i;
        while b.get(i).is_some_and(|c| c.is_ascii_digit()) {
            i += 1;
        }
        let n: usize = rest.get(start..i)?.parse().ok()?;
        parts.push(rest.get(i..i + n)?);
        i += n;
    }
    if parts.last().is_some_and(|p| is_hash_segment(p)) {
        parts.pop();
    }
    if parts.is_empty() {
        return None;
    }
    let mut s = parts.join("::");
    for (from, to) in [
        ("$LT$", "<"),
        ("$GT$", ">"),
        ("$RF$", "&"),
        ("$BP$", "*"),
        ("$C$", ","),
        ("$SP$", "@"),
        ("$LP$", "("),
        ("$RP$", ")"),
        ("$u20$", " "),
        ("$u27$", "'"),
        ("$u5b$", "["),
        ("$u5d$", "]"),
        ("$u7b$", "{"),
        ("$u7d$", "}"),
        ("$u7e$", "~"),
        ("..", "::"),
    ] {
        s = s.replace(from, to);
    }
    Some(s.replace("::_$", "::$"))
}

// ───────────────────────── 摘要与界面 ─────────────────────────

/// 一行说明:异常类型 · 系统附注 · 出错线程 · 我们自己代码里离崩溃最近的那一帧。
fn reason_line(record: &CrashRecord, en: bool) -> String {
    let Some(report) = &record.report else {
        return pick(en, "没有找到系统崩溃报告", "No system crash report found").into();
    };
    let Some(d) = &report.details else {
        return pick(
            en,
            "系统记录了一次崩溃(详情见崩溃报告)",
            "The system recorded a crash (see the crash report)",
        )
        .into();
    };
    let mut parts = Vec::new();
    match (&d.exception_type, &d.signal) {
        (Some(e), Some(s)) => parts.push(format!("{} ({})", e, s)),
        (Some(x), None) | (None, Some(x)) => parts.push(x.clone()),
        (None, None) => {}
    }
    parts.extend(d.notes.iter().take(1).cloned());
    if let Some(name) = &d.thread_name {
        parts.push(if en {
            format!("thread {}", name)
        } else {
            format!("{} 线程", name)
        });
    }
    if let Some(f) = d
        .frames
        .iter()
        .find(|f| f.symbol.contains(OWN_CRATE))
        .or_else(|| d.frames.first())
    {
        parts.push(f.symbol.clone());
    }
    if parts.is_empty() {
        pick(
            en,
            "原因未知(崩溃报告里没有异常信息)",
            "Unknown (no exception info in the crash report)",
        )
        .into()
    } else {
        parts.join(" · ")
    }
}

fn pick<'a>(en: bool, zh: &'a str, english: &'a str) -> &'a str {
    if en {
        english
    } else {
        zh
    }
}

/// 多行摘要:日志里(固定中文)、复制诊断信息和诊断输出里(跟界面语言)用的都是它。
pub fn format_summary(record: &CrashRecord, en: bool) -> String {
    let m = &record.session;
    let mut out = if en {
        format!(
            "Previous session exited abnormally: v{} · build {} · pid {} · started {}",
            m.version,
            m.build_id,
            m.pid,
            format_local(m.started_at)
        )
    } else {
        format!(
            "上次会话异常退出:v{} · build {} · pid {} · 启动于 {}",
            m.version,
            m.build_id,
            m.pid,
            format_local(m.started_at)
        )
    };
    let Some(report) = &record.report else {
        out.push('\n');
        out.push_str(pick(
            en,
            "没有找到系统崩溃报告(强制退出、断电、被系统结束时都没有)",
            "No system crash report found (there is none after a force quit, power loss or the system killing the app)",
        ));
        return out;
    };
    if let Some(d) = &report.details {
        let exception = [
            d.exception_type.clone(),
            d.signal.clone().map(|s| format!("({})", s)),
            d.termination.clone().map(|t| format!("· {}", t)),
        ]
        .into_iter()
        .flatten()
        .collect::<Vec<_>>()
        .join(" ");
        if !exception.is_empty() {
            out.push_str(&format!(
                "\n{}{}",
                pick(en, "异常:", "Exception: "),
                exception
            ));
        }
        for n in &d.notes {
            out.push_str(&format!("\n{}{}", pick(en, "系统附注:", "Note: "), n));
        }
        if let Some(v) = &d.os_version {
            out.push_str(&format!("\n{}{}", pick(en, "系统:", "OS: "), v));
        }
        if d.thread_index.is_some() || d.thread_name.is_some() {
            out.push_str(&format!(
                "\n{}#{} {}",
                pick(en, "出错线程:", "Crashed thread: "),
                d.thread_index
                    .map(|i| i.to_string())
                    .unwrap_or_else(|| "?".into()),
                d.thread_name.as_deref().unwrap_or("")
            ));
        }
        let width = d
            .frames
            .iter()
            .map(|f| f.image.chars().count())
            .max()
            .unwrap_or(0);
        let mut prev: Option<usize> = None;
        for f in &d.frames {
            if prev.is_some_and(|p| f.index > p + 1) {
                out.push_str("\n   …");
            }
            out.push_str(&format!(
                "\n  {:>3}  {:<width$}  {}",
                f.index,
                f.image,
                f.symbol,
                width = width
            ));
            prev = Some(f.index);
        }
    }
    out.push_str(&format!(
        "\n{}{}",
        pick(en, "崩溃报告:", "Crash report: "),
        report.path
    ));
    out
}

/// 给前端的提示内容。
#[derive(Debug, Clone, Serialize)]
pub struct CrashNotice {
    /// 一行原因。
    pub reason: String,
    /// 崩掉的那次是哪个版本、什么时候启动的。
    pub version: String,
    pub started_at: String,
    pub report_path: Option<String>,
}

fn notice_for(record: &CrashRecord) -> CrashNotice {
    CrashNotice {
        reason: reason_line(record, crate::i18n::is_en()),
        version: record.session.version.clone(),
        started_at: format_local(record.session.started_at),
        report_path: record.report.as_ref().map(|r| r.path.clone()),
    }
}

/// `get_diagnostics` 末尾附的一段;从没检测到过异常退出时为空串。
pub fn diagnostics_section() -> String {
    let Some(record) = SESSION.get().and_then(|s| load_last_crash(&s.dir)) else {
        return String::new();
    };
    let en = crate::i18n::is_en();
    format!(
        "\n{}{}{}\n{}\n",
        pick(
            en,
            "── 最近一次异常退出(检测于 ",
            "── Last abnormal exit (detected "
        ),
        format_local(record.detected_at),
        pick(en, ")──", ") ──"),
        format_summary(&record, en)
    )
}

/// 本次启动检测到、用户还没关掉的崩溃提示。没有就是 None。
#[tauri::command]
pub async fn get_last_crash() -> Result<Option<CrashNotice>, String> {
    Ok(pending().as_ref().map(notice_for))
}

/// 用户关掉提示。本次会话里不会再出现;下次启动也不会(标记已经删了)。
#[tauri::command]
pub async fn dismiss_last_crash() -> Result<(), String> {
    *pending() = None;
    Ok(())
}

/// 「复制诊断信息」:当前版本、系统 + 那次崩溃的摘要,报问题时直接粘。
#[tauri::command]
pub async fn get_crash_report_text() -> Result<String, String> {
    let record = pending()
        .clone()
        .or_else(|| SESSION.get().and_then(|s| load_last_crash(&s.dir)))
        .ok_or_else(|| t("没有记录到异常退出", "No abnormal exit recorded").to_string())?;
    let build = crate::BuildInfo::current();
    Ok(crate::tr!(
        "Voice Input v{} · build {} · {}\n系统: {} {}\n\n{}\n",
        "Voice Input v{} · build {} · {}\nSystem: {} {}\n\n{}\n",
        build.version,
        build.build_id,
        build.built_at,
        std::env::consts::OS,
        std::env::consts::ARCH,
        format_summary(&record, crate::i18n::is_en())
    ))
}

/// 在 Finder / 资源管理器里选中崩溃报告。路径只用我们自己找到的那个,不接受前端传入。
#[tauri::command]
pub async fn reveal_crash_report() -> Result<(), String> {
    let path = pending()
        .as_ref()
        .and_then(|r| r.report.as_ref().map(|p| p.path.clone()))
        .ok_or_else(|| t("没有崩溃报告", "No crash report").to_string())?;

    #[cfg(target_os = "macos")]
    let result = std::process::Command::new("open")
        .arg("-R")
        .arg(&path)
        .spawn();
    #[cfg(windows)]
    let result = {
        use std::os::windows::process::CommandExt;
        std::process::Command::new("explorer")
            .raw_arg(format!("/select,\"{}\"", path))
            .spawn()
    };
    #[cfg(all(not(target_os = "macos"), not(windows)))]
    let result = std::process::Command::new("xdg-open")
        .arg(Path::new(&path).parent().unwrap_or(Path::new("/")))
        .spawn();

    result
        .map(|_| ())
        .map_err(|e| crate::tr!("打不开 {}: {}", "Can't open {}: {}", path, e))
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE: &str = include_str!("../tests/fixtures/sample-crash.ips");

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("vif-crash-{}-{}", tag, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn marker(pid: u32, started_at: i64) -> SessionMarker {
        SessionMarker {
            pid,
            version: "9.9.9".into(),
            build_id: "test-build".into(),
            started_at,
        }
    }

    fn set_mtime(path: &Path, secs: i64) {
        let f = fs::File::options().write(true).open(path).unwrap();
        f.set_modified(from_unix_secs(secs)).unwrap();
    }

    #[test]
    fn marker_file_names() {
        assert_eq!(marker_pid_from_name("session-1234.json"), Some(1234));
        assert_eq!(marker_pid_from_name("session-.json"), None);
        assert_eq!(marker_pid_from_name("last-crash.json"), None);
        assert_eq!(marker_pid_from_name("session-12.json.tmp"), None);
    }

    /// 写 → 自己的不算陈旧 → 别人的(上次会话)算 → 删掉后不再出现。
    #[test]
    fn marker_lifecycle() {
        let dir = temp_dir("lifecycle");
        let mine = marker(100, 2_000);
        let old = marker(99, 1_000);
        let older = marker(98, 500);
        write_marker(&dir, &mine).unwrap();
        write_marker(&dir, &old).unwrap();
        write_marker(&dir, &older).unwrap();
        fs::write(dir.join(LAST_CRASH_FILE), "{}").unwrap();

        let stale = stale_markers(&dir, 100);
        assert_eq!(
            stale.iter().map(|(_, m)| m.clone()).collect::<Vec<_>>(),
            vec![older.clone(), old.clone()],
            "从旧到新,不含自己,也不把 last-crash.json 当标记"
        );

        // 正常退出:删掉自己的,别人的不动。
        fs::remove_file(marker_path(&dir, 100)).unwrap();
        assert_eq!(stale_markers(&dir, 100).len(), 2);
        for (p, _) in stale {
            fs::remove_file(p).unwrap();
        }
        assert!(stale_markers(&dir, 100).is_empty());
        let _ = fs::remove_dir_all(&dir);
    }

    /// 标记写到一半就崩了:pid 从文件名拿,启动时间退回修改时间,照样报。
    #[test]
    fn corrupt_marker_still_counts() {
        let dir = temp_dir("corrupt");
        let p = marker_path(&dir, 4321);
        fs::write(&p, "{\"pid\": 43").unwrap();
        set_mtime(&p, 1_700_000_000);
        let stale = stale_markers(&dir, 1);
        assert_eq!(stale.len(), 1);
        assert_eq!(stale[0].1.pid, 4321);
        assert_eq!(stale[0].1.started_at, 1_700_000_000);
        assert_eq!(stale[0].1.version, "?");
        let _ = fs::remove_dir_all(&dir);
    }

    /// 内容里的 pid 和文件名对不上(被手改过)也按坏标记处理,不信内容。
    #[test]
    fn marker_pid_must_match_file_name() {
        let dir = temp_dir("mismatch");
        let p = marker_path(&dir, 7);
        fs::write(&p, serde_json::to_string(&marker(8, 123)).unwrap()).unwrap();
        let stale = stale_markers(&dir, 1);
        assert_eq!(stale[0].1.pid, 7);
        assert_eq!(stale[0].1.version, "?");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn comm_matching() {
        assert!(comm_is_us(
            "/Applications/Voice Input.app/Contents/MacOS/voice-input\n",
            "voice-input"
        ));
        assert!(comm_is_us("voice-input", "voice-input"));
        // Linux 截到 15 个字符。
        assert!(comm_is_us("a-very-long-app", "a-very-long-app-name"));
        assert!(!comm_is_us("", "voice-input"));
        assert!(!comm_is_us("/usr/bin/python3", "voice-input"));
        assert!(!comm_is_us("voice-input-helper", "voice-input"));
    }

    #[test]
    fn parses_fixture() {
        let d = parse_ips(FIXTURE).unwrap();
        assert_eq!(d.pid, Some(4242));
        assert_eq!(d.app_version.as_deref(), Some("9.9.9"));
        assert_eq!(d.os_version.as_deref(), Some("macOS 99.1 (00X000)"));
        assert_eq!(d.exception_type.as_deref(), Some("EXC_BREAKPOINT"));
        assert_eq!(d.signal.as_deref(), Some("SIGTRAP"));
        assert_eq!(d.termination.as_deref(), Some("Trace/BPT trap: 5"));
        assert_eq!(
            d.notes,
            vec!["Must only be used from the main thread".to_string()]
        );
        assert_eq!(d.thread_index, Some(1));
        assert_eq!(d.thread_name.as_deref(), Some("tokio-rt-worker"));

        // 栈顶 8 帧 + 更深处我们自己的帧(第 10–12 帧,限 3 帧,第 13 帧不要);
        // 第 8、9 帧是 enigo / tokio 的,不补。
        let idx: Vec<usize> = d.frames.iter().map(|f| f.index).collect();
        assert_eq!(idx, vec![0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12]);
        assert_eq!(d.frames[0].image, "libdispatch.dylib");
        assert_eq!(d.frames[0].symbol, "_dispatch_assert_queue_fail");
        assert_eq!(d.frames[3].image, "HIToolbox");
        assert_eq!(
            d.frames[5].symbol,
            "enigo::platform::macos_impl::keycode_to_string"
        );
        // 没有 symbol 的帧用偏移。
        assert_eq!(d.frames[7].symbol, "0x1234");
        assert_eq!(d.frames[8].symbol, "voice_input_lib::input::press_paste");
        assert_eq!(
            d.frames[9].symbol,
            "voice_input_lib::deliver_text::{closure}"
        );
        assert_eq!(d.frames[9].image, "voice-input");
    }

    #[test]
    fn parse_rejects_garbage() {
        assert!(parse_ips("not json at all").is_err());
        assert!(parse_ips("{\"app_name\":\"x\"}\n{ broken").is_err());
        // 只有一段 JSON(没有头)也能读。
        let d = parse_ips(
            "{\"pid\": 5, \"exception\": {\"type\": \"EXC_CRASH\", \"signal\": \"SIGABRT\"}}",
        )
        .unwrap();
        assert_eq!(d.pid, Some(5));
        assert_eq!(d.signal.as_deref(), Some("SIGABRT"));
        assert!(d.frames.is_empty());
    }

    #[test]
    fn demangles_common_shapes() {
        assert_eq!(
            demangle("_RNvNtCsAbC123_15voice_input_lib5input11press_paste"),
            "voice_input_lib::input::press_paste"
        );
        assert_eq!(
            demangle("_RNCNvCsAbC123_15voice_input_lib12deliver_text0B3_"),
            "voice_input_lib::deliver_text::{closure}"
        );
        assert_eq!(demangle("_RNvCs_4core5panic"), "core::panic");
        assert_eq!(
            demangle("__ZN3std9panicking11begin_panic17h0123456789abcdefE"),
            "std::panicking::begin_panic"
        );
        let ident =
            "drop_in_place$LT$std..rt..lang_start$LT$$LP$$RP$$GT$..$u7b$$u7b$closure$u7d$$u7d$$GT$";
        assert_eq!(
            demangle(&format!(
                "_ZN4core3ptr{}{}17h0123456789abcdefE",
                ident.len(),
                ident
            )),
            "core::ptr::drop_in_place<std::rt::lang_start<()>::{{closure}}>"
        );
        assert_eq!(
            demangle("voice_input_lib::indicator::show::h05e9e31ffc3fb189"),
            "voice_input_lib::indicator::show"
        );
        assert_eq!(demangle("foo::hello"), "foo::hello");
        // 系统库的 C / ObjC 符号、认不出的 v0(trait impl)原样返回。
        assert_eq!(
            demangle("_dispatch_assert_queue_fail"),
            "_dispatch_assert_queue_fail"
        );
        assert_eq!(
            demangle("-[NSWindow orderFront:]"),
            "-[NSWindow orderFront:]"
        );
        let impl_sym = "_RNvXs_NtCsAbC_5enigoNtB4_5EnigoNtB4_8Keyboard3key";
        assert_eq!(demangle(impl_sym), impl_sym);
        // 截断的符号不越界。
        assert_eq!(demangle("_RNvCs_4co"), "_RNvCs_4co");
        assert_eq!(demangle("_ZN3fo"), "_ZN3fo");
    }

    /// 只要比上次启动新的报告;同一会话的优先;pid 对不上的不要。
    #[test]
    fn selects_report_newer_than_marker() {
        let dir = temp_dir("select");
        let retired = dir.join("Retired");
        fs::create_dir_all(&retired).unwrap();
        let start = 1_800_000_000;

        let with_pid = |pid: u32| FIXTURE.replace("\"pid\" : 4242", &format!("\"pid\" : {}", pid));
        // 上上次会话的报告:比这次的标记还早。
        let old = retired.join("voice-input-old.ips");
        fs::write(&old, with_pid(4242)).unwrap();
        set_mtime(&old, start - 600);
        // 别的应用的报告,再新也不要。
        let other = dir.join("SomethingElse-2099.ips");
        fs::write(&other, with_pid(4242)).unwrap();
        set_mtime(&other, start + 60);
        // 同名前缀但不是 .ips。
        let diag = dir.join("voice-input-x.diag");
        fs::write(&diag, "x").unwrap();
        set_mtime(&diag, start + 60);

        let dirs = [retired.clone(), dir.clone()];
        let m = marker(4242, start);
        let c = list_candidates(&dirs, "voice-input-", ".ips", since_for(&m), MAX_CANDIDATES);
        assert!(c.is_empty(), "只有旧报告和无关文件时一个都不选: {:?}", c);
        assert_eq!(choose_ips(&c, m.pid), None);

        // 这次会话之后的两份:较新的那份是别的 pid(不属于这次会话),较早那份 pid 对得上。
        let ours = dir.join("voice-input-ours.ips");
        fs::write(&ours, with_pid(4242)).unwrap();
        set_mtime(&ours, start + 30);
        let foreign = retired.join("voice-input-foreign.ips");
        fs::write(&foreign, with_pid(1)).unwrap();
        set_mtime(&foreign, start + 90);

        let c = list_candidates(&dirs, "voice-input-", ".ips", since_for(&m), MAX_CANDIDATES);
        assert_eq!(c.len(), 2);
        assert_eq!(c[0].path, foreign, "从新到旧");
        let chosen = choose_ips(&c, m.pid).unwrap();
        assert_eq!(chosen.path, ours.display().to_string());
        assert_eq!(chosen.details.unwrap().pid, Some(4242));

        // 数量有上限。
        assert_eq!(
            list_candidates(&dirs, "voice-input-", ".ips", since_for(&m), 1).len(),
            1
        );

        // 解析不了的报告(格式变了)在没有更好的选择时也给出来,只是没有详情。
        let m2 = marker(777, start);
        let garbled = dir.join("voice-input-garbled.ips");
        fs::write(&garbled, "garbage").unwrap();
        set_mtime(&garbled, start + 10);
        let c = list_candidates(
            &dirs,
            "voice-input-",
            ".ips",
            since_for(&m2),
            MAX_CANDIDATES,
        );
        let chosen = choose_ips(&c, m2.pid).unwrap();
        assert_eq!(chosen.path, garbled.display().to_string());
        assert!(chosen.details.is_none());
        let _ = fs::remove_dir_all(&dir);
    }

    fn record_with_fixture() -> CrashRecord {
        CrashRecord {
            session: marker(4242, 1_800_000_000),
            report: Some(FoundReport {
                path: "/tmp/voice-input-sample.ips".into(),
                details: Some(parse_ips(FIXTURE).unwrap()),
            }),
            detected_at: 1_800_000_100,
        }
    }

    #[test]
    fn summary_formatting() {
        let r = record_with_fixture();
        let zh = format_summary(&r, false);
        let lines: Vec<&str> = zh.lines().collect();
        assert!(
            lines[0].starts_with("上次会话异常退出:v9.9.9 · build test-build · pid 4242 · 启动于 ")
        );
        assert_eq!(
            lines[1],
            "异常:EXC_BREAKPOINT (SIGTRAP) · Trace/BPT trap: 5"
        );
        assert_eq!(lines[2], "系统附注:Must only be used from the main thread");
        assert_eq!(lines[3], "系统:macOS 99.1 (00X000)");
        assert_eq!(lines[4], "出错线程:#1 tokio-rt-worker");
        assert_eq!(
            lines[5],
            "    0  libdispatch.dylib  _dispatch_assert_queue_fail"
        );
        // 第 7 帧之后跳到第 10 帧,中间有省略号。
        assert_eq!(lines[13], "   …");
        assert_eq!(
            lines[14],
            "   10  voice-input        voice_input_lib::input::press_paste"
        );
        assert_eq!(
            lines.last().unwrap(),
            &"崩溃报告:/tmp/voice-input-sample.ips"
        );

        let en = format_summary(&r, true);
        assert!(en.starts_with("Previous session exited abnormally: v9.9.9"));
        assert!(en.contains("\nCrashed thread: #1 tokio-rt-worker\n"));

        // 原因里点名的是我们自己代码里离崩溃最近的那帧,不是栈顶的系统库。
        assert_eq!(
            reason_line(&r, false),
            "EXC_BREAKPOINT (SIGTRAP) · Must only be used from the main thread · tokio-rt-worker 线程 · voice_input_lib::input::press_paste"
        );
        assert!(reason_line(&r, true).contains(" · thread tokio-rt-worker · "));
    }

    #[test]
    fn summary_without_report_or_details() {
        let mut r = record_with_fixture();
        r.report.as_mut().unwrap().details = None;
        let s = format_summary(&r, false);
        assert_eq!(s.lines().count(), 2);
        assert!(s.ends_with("崩溃报告:/tmp/voice-input-sample.ips"));
        assert_eq!(reason_line(&r, false), "系统记录了一次崩溃(详情见崩溃报告)");

        r.report = None;
        let s = format_summary(&r, false);
        assert!(s.contains("没有找到系统崩溃报告"));
    }

    /// 落盘的记录读得回来(诊断信息靠它在之后的会话里也带上最近一次崩溃)。
    #[test]
    fn last_crash_round_trip() {
        let dir = temp_dir("persist");
        let r = record_with_fixture();
        persist_last_crash(&dir, &r);
        assert_eq!(load_last_crash(&dir), Some(r));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn truncates_long_backtraces() {
        let text = (0..10)
            .map(|i| i.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        assert_eq!(truncate_lines(&text, 20), text);
        assert_eq!(truncate_lines(&text, 3), "0\n1\n2\n  …(省略 7 行)");
    }
}
