//! 本地 STT / LLM Python 服务的进程管理。
//!
//! 设计上只有一条铁律:**先探测,后拉起;只停自己拉起的**。
//!
//! 用户经常自己在终端里 `python -m services.stt_server`,这时应用再 spawn 一个
//! 就会撞端口(第二个进程起不来,或者更糟——起来了但连的不是同一个)。所以
//! `start` 永远先打 `/health`:端口上已经有健康的服务,就「采纳」(adopt)它,
//! 只把它显示成运行中,绝不另起一个,也绝不去停它。
//!
//! 「是不是自己拉起的」不靠标志位记账,而是每次看状态时现算:
//! 端口健康 + 本进程手里有对应的活着的子进程 = 自己的(可停);
//! 端口健康 + 手里没有 = 外部的(不可停)。这样即使中途状态错乱也会自愈。
//!
//! 唯一的例外是「上次会话的遗孤」:应用被 SIGKILL / 崩溃时来不及杀子进程,
//! 下次启动时那两个服务还在监听。纯靠上面的规则会把它们判成「外部进程」,
//! 用户明明是从应用里启动的却停不掉。为此 spawn 时把 pid 落盘
//! (`managed-servers.json`),启动时校验 pid 仍然活着 **且** 命令行确实是对应
//! 的模块,才认领回来(`Handle::Reclaimed`)——pid 会被系统复用,只比对 pid
//! 是不够的。

use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// UI 里回显的最近日志行数上限(同时也是内存环形缓冲的容量)。
const LOG_TAIL_LINES: usize = 200;
/// `/health` 探测超时。本地回环,给 1.5s 足够;太长会让「刷新状态」卡住 UI。
const HEALTH_TIMEOUT: Duration = Duration::from_millis(1500);
/// 停止时等待进程自己退出的时间,超时才升级到 SIGKILL。
const TERM_GRACE: Duration = Duration::from_secs(5);

// ── 基本类型 ──

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServerKind {
    Stt,
    Llm,
}

impl ServerKind {
    /// `python -m <module>`。
    pub fn module(self) -> &'static str {
        match self {
            ServerKind::Stt => "services.stt_server",
            ServerKind::Llm => "services.llm_server",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            ServerKind::Stt => "STT",
            ServerKind::Llm => "LLM",
        }
    }

    fn log_file_name(self) -> &'static str {
        match self {
            ServerKind::Stt => "stt-server.log",
            ServerKind::Llm => "llm-server.log",
        }
    }

    fn state_key(self) -> &'static str {
        match self {
            ServerKind::Stt => "stt",
            ServerKind::Llm => "llm",
        }
    }
}

/// 单个服务对外报告的状态。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServerState {
    /// 本地管理模式下缺路径(仓库 / 解释器),连尝试启动的条件都不具备。
    NotConfigured,
    /// 端口上没有服务,本应用也没有在跑的子进程。
    Stopped,
    /// 子进程已拉起,但 `/health` 还没通——加载模型要几秒到几十秒。
    Starting,
    /// `/health` 通了。
    Running,
    /// 子进程退出了(或者压根没起来)。`detail` 里是原因。
    Failed,
}

/// 一个服务的完整状态快照,直接丢给前端。
#[derive(Debug, Clone, Serialize)]
pub struct ServerStatus {
    pub kind: ServerKind,
    pub state: ServerState,
    pub port: u16,
    /// 是否由本应用负责生命周期。
    ///
    /// `false` 且 `state == Running` 表示这是用户自己在终端起的(或上次遗留下来
    /// 又认领不回来的)进程:**停止按钮必须禁用**,否则就是在误导用户——
    /// 点了也不会有反应,而如果真去停了,就是在杀别人的进程。
    pub managed: bool,
    pub pid: Option<u32>,
    /// `/health` 报告的当前模型,运行中才有。
    pub current_model: Option<String>,
    /// 失败原因 / 给用户的提示,没有则为空。
    pub detail: Option<String>,
    /// 子进程日志文件路径(只有本应用拉起过才有)。
    pub log_path: Option<String>,
    /// 最近若干行 stdout/stderr,失败时用来定位问题。
    pub recent_logs: Vec<String>,
}

/// 两个服务 + 当前模式,一次性给前端。
#[derive(Debug, Clone, Serialize)]
pub struct ServerReport {
    pub mode: crate::config::ServerMode,
    pub stt: ServerStatus,
    pub llm: ServerStatus,
    /// 本地模式下当前生效的仓库 / 解释器路径,以及它们是否真的存在。
    pub local_paths: LocalPathReport,
    /// 远程模式下连的地址,原样回显。
    pub remote_url: String,
}

/// 路径可用性。UI 要能明确告诉用户「没探测到」,而不是按钮点了没反应。
#[derive(Debug, Clone, Serialize)]
pub struct LocalPathReport {
    pub repo_path: Option<String>,
    pub python_path: Option<String>,
    /// `<repo>/services/stt_server.py` 是否存在。
    pub repo_ok: bool,
    /// 解释器文件是否存在。
    pub python_ok: bool,
    /// 路径不可用时的中文说明,可直接显示。
    pub problem: Option<String>,
}

// ── 子进程句柄 ──

/// 本应用能停的进程有两种来源。
enum Handle {
    /// 本次会话亲自 spawn 的,能 `try_wait` 收尸。
    Own(Child),
    /// 上次会话遗留、本次启动时校验后认领回来的,只有 pid。
    Reclaimed(u32),
}

struct Slot {
    handle: Handle,
    pid: u32,
    port: u16,
    /// 环形缓冲:最近 `LOG_TAIL_LINES` 行 stdout/stderr。
    logs: Arc<Mutex<VecDeque<String>>>,
    log_path: PathBuf,
    /// 进程退出后记下来的原因,供状态查询时报 `Failed`。
    exit_note: Option<String>,
}

impl Slot {
    /// 进程是否还活着。`Own` 用 `try_wait` 顺带收尸,避免僵尸进程。
    fn alive(&mut self) -> bool {
        match &mut self.handle {
            Handle::Own(child) => match child.try_wait() {
                Ok(Some(status)) => {
                    if self.exit_note.is_none() {
                        self.exit_note = Some(match status.code() {
                            Some(code) => format!("进程已退出,退出码 {}", code),
                            None => "进程被信号终止".to_string(),
                        });
                    }
                    false
                }
                Ok(None) => true,
                // 查不到状态时按「已退出」处理,总比一直显示运行中好。
                Err(e) => {
                    if self.exit_note.is_none() {
                        self.exit_note = Some(format!("无法查询子进程状态: {}", e));
                    }
                    false
                }
            },
            Handle::Reclaimed(pid) => pid_alive(*pid),
        }
    }

    fn recent_logs(&self) -> Vec<String> {
        self.logs
            .lock()
            .map(|l| l.iter().cloned().collect())
            .unwrap_or_default()
    }
}

// ── 管理器 ──

pub struct ServerManager {
    stt: Option<Slot>,
    llm: Option<Slot>,
    /// 子进程日志 + pid 记账文件所在目录。
    data_dir: PathBuf,
}

impl ServerManager {
    pub fn new(data_dir: PathBuf) -> Self {
        let _ = std::fs::create_dir_all(&data_dir);
        Self {
            stt: None,
            llm: None,
            data_dir,
        }
    }

    fn slot(&mut self, kind: ServerKind) -> &mut Option<Slot> {
        match kind {
            ServerKind::Stt => &mut self.stt,
            ServerKind::Llm => &mut self.llm,
        }
    }

    fn pid_file(&self) -> PathBuf {
        self.data_dir.join("managed-servers.json")
    }

    /// 把当前活着的子进程 pid 落盘,供下次启动认领遗孤。
    fn persist_pids(&mut self) {
        let mut map = serde_json::Map::new();
        for kind in [ServerKind::Stt, ServerKind::Llm] {
            // 先判活(要 &mut,`alive` 顺带收尸),再取字段,避免同时可变借用。
            let live = self.slot(kind).as_mut().is_some_and(|s| s.alive());
            if !live {
                continue;
            }
            if let Some(s) = self.slot(kind).as_ref() {
                map.insert(
                    kind.state_key().to_string(),
                    serde_json::json!({ "pid": s.pid, "port": s.port }),
                );
            }
        }
        let path = self.pid_file();
        if let Ok(json) = serde_json::to_string_pretty(&serde_json::Value::Object(map)) {
            let _ = std::fs::write(path, json);
        }
    }

    /// 启动时调用:把上次会话残留的子进程认领回来。
    ///
    /// 只认领「pid 还活着 **且** 命令行确实是对应模块」的进程——pid 会被系统
    /// 复用,只比对 pid 就可能把无辜进程当成自己的,进而在「停止」时杀错。
    pub fn reclaim_orphans(&mut self) -> Vec<String> {
        let path = self.pid_file();
        let Ok(data) = std::fs::read_to_string(&path) else {
            return Vec::new();
        };
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&data) else {
            return Vec::new();
        };

        let mut claimed = Vec::new();
        for kind in [ServerKind::Stt, ServerKind::Llm] {
            let Some(entry) = value.get(kind.state_key()) else {
                continue;
            };
            let (Some(pid), Some(port)) = (
                entry.get("pid").and_then(|v| v.as_u64()),
                entry.get("port").and_then(|v| v.as_u64()),
            ) else {
                continue;
            };
            let pid = pid as u32;
            if !pid_runs_module(pid, kind.module()) {
                continue;
            }
            let log_path = self.data_dir.join(kind.log_file_name());
            *self.slot(kind) = Some(Slot {
                handle: Handle::Reclaimed(pid),
                pid,
                port: port as u16,
                // 遗孤的输出管道已经随上次会话的读取线程一起没了,只能指向
                // 上次写的日志文件;内存缓冲从空开始。
                logs: Arc::new(Mutex::new(VecDeque::new())),
                log_path,
                exit_note: None,
            });
            claimed.push(format!("{}(pid {})", kind.label(), pid));
        }
        claimed
    }

    /// 拉起一个服务。调用方必须已经确认端口上没有健康的服务(见 `start`)。
    fn spawn(&mut self, kind: ServerKind, opts: &SpawnOptions) -> Result<u32, String> {
        let repo = Path::new(&opts.repo_path);
        let python = Path::new(&opts.python_path);
        if !repo.join("services/stt_server.py").exists() {
            return Err(format!(
                "仓库路径不对:{} 下找不到 services/stt_server.py",
                opts.repo_path
            ));
        }
        if !python.exists() {
            return Err(format!("Python 解释器不存在:{}", opts.python_path));
        }

        // 下面会整个覆盖掉这个 slot,所以手里如果还攥着一个活的子进程,必须先
        // 停掉——否则句柄一丢,它就变成了谁也管不着的孤儿。最典型的触发路径是
        // 用户改了端口:新端口探测为空,于是走到这里,而老进程还在老端口上跑。
        if self.slot(kind).as_mut().is_some_and(|s| s.alive()) {
            let _ = self.stop(kind);
        }

        let log_path = self.data_dir.join(kind.log_file_name());
        // 每次启动截断:日志是用来看「这次为什么没起来」的,不是历史档案。
        let log_file = File::create(&log_path)
            .map_err(|e| format!("无法创建日志文件 {}: {}", log_path.display(), e))?;
        drop(log_file);

        let mut cmd = Command::new(python);
        cmd.arg("-m")
            .arg(kind.module())
            .current_dir(repo)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            // 不加这个,Python 的 stdout 会攒在块缓冲里,日志要等进程退出才出来,
            // 「实时看启动进度」就无从谈起。
            .env("PYTHONUNBUFFERED", "1")
            // 只绑回环:本应用连的是 127.0.0.1,没有理由把服务暴露到局域网。
            .env("VIF_STT_HOST", "127.0.0.1")
            .env("VIF_LLM_HOST", "127.0.0.1")
            .env("VIF_STT_PORT", opts.stt_port.to_string())
            // STT 服务要反代到 LLM 服务,所以两个端口都得告诉它。
            .env("VIF_LLM_PORT", opts.llm_port.to_string());
        if let Some(model) = opts.stt_model.as_deref().filter(|s| !s.trim().is_empty()) {
            cmd.env("VIF_STT_MODEL", model);
        }
        if let Some(model) = opts.llm_model.as_deref().filter(|s| !s.trim().is_empty()) {
            cmd.env("VIF_LLM_MODEL", model);
        }

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("启动 {} 服务失败: {}", kind.label(), e))?;
        let pid = child.id();

        let logs = Arc::new(Mutex::new(VecDeque::with_capacity(LOG_TAIL_LINES)));
        if let Some(out) = child.stdout.take() {
            pump(out, logs.clone(), log_path.clone(), kind, "out");
        }
        if let Some(err) = child.stderr.take() {
            pump(err, logs.clone(), log_path.clone(), kind, "err");
        }

        *self.slot(kind) = Some(Slot {
            handle: Handle::Own(child),
            pid,
            port: match kind {
                ServerKind::Stt => opts.stt_port,
                ServerKind::Llm => opts.llm_port,
            },
            logs,
            log_path,
            exit_note: None,
        });
        self.persist_pids();
        Ok(pid)
    }

    /// 停止本应用拉起(或认领)的进程。
    ///
    /// 手里没有句柄 = 这个服务不是本应用的,直接拒绝。这是「绝不杀别人进程」
    /// 那条铁律的落地点。
    fn stop(&mut self, kind: ServerKind) -> Result<String, String> {
        // 句柄直接取走:无论停成没停成,这个 slot 都不该再留着。
        // (丢弃 `Child` 不会杀进程,所以取走是安全的。)
        let Some(mut slot) = self.slot(kind).take() else {
            return Err(format!(
                "{} 服务不是由本应用启动的,无法从这里停止。请到启动它的终端里停。",
                kind.label()
            ));
        };
        let pid = slot.pid;

        if !slot.alive() {
            self.persist_pids();
            return Ok(format!("{} 服务(pid {})已经不在运行", kind.label(), pid));
        }

        // 先 SIGTERM:uvicorn 收到会走正常的 shutdown,释放端口、落盘状态。
        // 直接 SIGKILL 可能留下半写的模型缓存。
        terminate(pid);

        let deadline = Instant::now() + TERM_GRACE;
        while Instant::now() < deadline {
            if !slot.alive() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }

        let forced = if slot.alive() {
            force_kill(pid);
            // 收尸,避免留下僵尸进程。
            if let Handle::Own(child) = &mut slot.handle {
                let _ = child.wait();
            }
            true
        } else {
            false
        };

        drop(slot);
        self.persist_pids();
        Ok(if forced {
            format!("{} 服务(pid {})未响应,已强制结束", kind.label(), pid)
        } else {
            format!("{} 服务(pid {})已停止", kind.label(), pid)
        })
    }

    /// 应用退出时调用:把所有自己拉起的子进程带走,不留孤儿。
    pub fn shutdown_all(&mut self) {
        for kind in [ServerKind::Stt, ServerKind::Llm] {
            if self.slot(kind).is_some() {
                let _ = self.stop(kind);
            }
        }
        // 全停干净了,记账文件也清掉,免得下次启动去认领已经不存在的 pid。
        let _ = std::fs::remove_file(self.pid_file());
    }

    /// 取某个服务的本地快照(不含健康探测——那个是异步的,在锁外做)。
    fn snapshot(&mut self, kind: ServerKind) -> Option<SlotSnapshot> {
        let slot = self.slot(kind).as_mut()?;
        let alive = slot.alive();
        Some(SlotSnapshot {
            pid: slot.pid,
            port: slot.port,
            alive,
            exit_note: slot.exit_note.clone(),
            log_path: slot.log_path.display().to_string(),
            recent_logs: slot.recent_logs(),
        })
    }
}

struct SlotSnapshot {
    pid: u32,
    port: u16,
    alive: bool,
    exit_note: Option<String>,
    log_path: String,
    recent_logs: Vec<String>,
}

/// spawn 一个服务需要的全部输入,从 `LocalServerConfig` 解析而来。
struct SpawnOptions {
    repo_path: String,
    python_path: String,
    stt_port: u16,
    llm_port: u16,
    stt_model: Option<String>,
    llm_model: Option<String>,
}

impl SpawnOptions {
    /// 从配置解析;路径缺失 / 不存在时给出可直接显示给用户的中文原因。
    fn from_config(local: &crate::config::LocalServerConfig) -> Result<Self, String> {
        let repo_path = local
            .repo_path
            .clone()
            .filter(|p| !p.trim().is_empty())
            .ok_or("没有设置仓库路径,且自动探测没找到。请在「服务器」里手动填写。")?;
        let python_path = local
            .python_path
            .clone()
            .filter(|p| !p.trim().is_empty())
            .ok_or("没有设置 Python 解释器路径,且自动探测没找到。请在「服务器」里手动填写。")?;
        Ok(Self {
            repo_path,
            python_path,
            stt_port: local.stt_port,
            llm_port: local.llm_port,
            stt_model: local.stt_model.clone(),
            llm_model: local.llm_model.clone(),
        })
    }
}

// ── 日志抽水线程 ──

/// 把子进程的一个输出流读进环形缓冲 + 追加写日志文件。
///
/// 必须消费掉管道:不读的话管道缓冲写满后子进程会阻塞在 write 上卡死。
fn pump<R: std::io::Read + Send + 'static>(
    stream: R,
    logs: Arc<Mutex<VecDeque<String>>>,
    log_path: PathBuf,
    kind: ServerKind,
    tag: &'static str,
) {
    let _ = std::thread::Builder::new()
        .name(format!("{}-{}-log", kind.state_key(), tag))
        .spawn(move || {
            let reader = BufReader::new(stream);
            let mut file = OpenOptions::new().append(true).open(&log_path).ok();
            for line in reader.lines() {
                let Ok(line) = line else { break };
                if let Some(f) = file.as_mut() {
                    let _ = writeln!(f, "{}", line);
                }
                if let Ok(mut buf) = logs.lock() {
                    if buf.len() == LOG_TAIL_LINES {
                        buf.pop_front();
                    }
                    buf.push_back(line);
                }
            }
        });
}

// ── 进程探测 / 信号(平台相关)──

#[cfg(unix)]
fn pid_alive(pid: u32) -> bool {
    // `kill -0` 只做权限与存在性检查,不发信号。
    Command::new("kill")
        .arg("-0")
        .arg(pid.to_string())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn pid_alive(pid: u32) -> bool {
    Command::new("tasklist")
        .args(["/FI", &format!("PID eq {}", pid), "/NH"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
        .unwrap_or(false)
}

/// pid 是否确实在跑指定模块。认领遗孤前的防串号校验。
#[cfg(unix)]
fn pid_runs_module(pid: u32, module: &str) -> bool {
    Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).contains(module))
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn pid_runs_module(pid: u32, _module: &str) -> bool {
    // Windows 上 tasklist 拿不到完整命令行,退化成「进程还在就认领」。
    pid_alive(pid)
}

#[cfg(unix)]
fn terminate(pid: u32) {
    let _ = Command::new("kill")
        .arg("-TERM")
        .arg(pid.to_string())
        .status();
}

#[cfg(not(unix))]
fn terminate(pid: u32) {
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T"])
        .status();
}

#[cfg(unix)]
fn force_kill(pid: u32) {
    let _ = Command::new("kill")
        .arg("-KILL")
        .arg(pid.to_string())
        .status();
}

#[cfg(not(unix))]
fn force_kill(pid: u32) {
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .status();
}

// ── 健康探测 ──

#[derive(Debug, Deserialize)]
struct Health {
    status: String,
    current_model: Option<String>,
}

/// 打一次 `/health`。通了返回模型名,不通返回 `None`。
///
/// 这是「采纳还是拉起」的唯一判据:能应答 `/health` 的就是可用的服务,
/// 不关心它是谁起的。
async fn probe(port: u16) -> Option<Health> {
    let client = reqwest::Client::builder()
        .timeout(HEALTH_TIMEOUT)
        .build()
        .ok()?;
    let resp = client
        .get(format!("http://127.0.0.1:{}/health", port))
        .send()
        .await
        .ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let health: Health = resp.json().await.ok()?;
    (health.status == "ok").then_some(health)
}

// ── 对外的异步 API ──
//
// 健康探测是异步的,而 `ServerManager` 在 `Mutex` 里;跨 await 持锁会把
// 整个应用拖死,所以这几个函数一律「锁外探测 → 锁内取快照」。

fn port_of(kind: ServerKind, local: &crate::config::LocalServerConfig) -> u16 {
    match kind {
        ServerKind::Stt => local.stt_port,
        ServerKind::Llm => local.llm_port,
    }
}

/// 单个服务的状态。
pub async fn status(
    manager: &Mutex<ServerManager>,
    cfg: &crate::config::ServerConfig,
    kind: ServerKind,
) -> ServerStatus {
    let port = port_of(kind, &cfg.local);
    let health = probe(port).await;

    let snapshot = manager.lock().ok().and_then(|mut m| m.snapshot(kind));
    let paths_ok = SpawnOptions::from_config(&cfg.local).is_ok();

    match (health, snapshot) {
        // 端口健康 + 自己的进程还活着 **且就在这个端口上** = 本应用管理中,可停。
        //
        // 端口这一项不能省:用户在 UI 里改了端口之后,手里那个子进程还在老端口
        // 上跑,新端口上应答的是别人。少比这一下就会把别人的进程标成「本应用
        // 启动」,停止按钮点下去就是杀错人。
        (Some(h), Some(snap)) if snap.alive && snap.port == port => ServerStatus {
            kind,
            state: ServerState::Running,
            port,
            managed: true,
            pid: Some(snap.pid),
            current_model: h.current_model,
            detail: None,
            log_path: Some(snap.log_path),
            recent_logs: snap.recent_logs,
        },
        // 端口健康,但本应用手里没有活着的进程 = 外部进程,只连不管。
        (Some(h), other) => ServerStatus {
            kind,
            state: ServerState::Running,
            port,
            managed: false,
            pid: None,
            current_model: h.current_model,
            detail: Some("外部进程(不是本应用启动的),只能连接,不能从这里停止".into()),
            log_path: other.as_ref().map(|s| s.log_path.clone()),
            recent_logs: other.map(|s| s.recent_logs).unwrap_or_default(),
        },
        // 进程还活着但 `/health` 没通 = 正在加载模型(同样要求端口一致)。
        (None, Some(snap)) if snap.alive && snap.port == port => ServerStatus {
            kind,
            state: ServerState::Starting,
            port,
            managed: true,
            pid: Some(snap.pid),
            current_model: None,
            detail: Some("已启动,正在加载模型...".into()),
            log_path: Some(snap.log_path),
            recent_logs: snap.recent_logs,
        },
        // 进程没了且端口也不通 = 起失败了,把退出原因和日志尾巴一起给出去。
        (None, Some(snap)) => ServerStatus {
            kind,
            state: ServerState::Failed,
            port,
            managed: false,
            pid: None,
            current_model: None,
            detail: Some(if snap.alive {
                // 活着但端口对不上:用户改了端口却没重启服务。
                format!(
                    "本应用启动的进程在 {} 端口,与当前配置的 {} 端口不一致,请重启服务以应用新端口。",
                    snap.port, port
                )
            } else {
                snap.exit_note.unwrap_or_else(|| "进程已退出".into())
            }),
            log_path: Some(snap.log_path),
            recent_logs: snap.recent_logs,
        },
        // 从没起过。本地模式下路径没配好就报 NotConfigured,让 UI 能说清原因。
        (None, None) => ServerStatus {
            kind,
            state: if cfg.mode == crate::config::ServerMode::Local && !paths_ok {
                ServerState::NotConfigured
            } else {
                ServerState::Stopped
            },
            port,
            managed: false,
            pid: None,
            current_model: None,
            detail: if cfg.mode == crate::config::ServerMode::Local && !paths_ok {
                SpawnOptions::from_config(&cfg.local).err()
            } else {
                None
            },
            log_path: None,
            recent_logs: Vec::new(),
        },
    }
}

/// 两个服务 + 模式 + 路径可用性,一次性报告。
pub async fn report(
    manager: &Mutex<ServerManager>,
    cfg: &crate::config::ServerConfig,
) -> ServerReport {
    ServerReport {
        mode: cfg.mode,
        stt: status(manager, cfg, ServerKind::Stt).await,
        llm: status(manager, cfg, ServerKind::Llm).await,
        local_paths: path_report(&cfg.local),
        remote_url: cfg.effective_stt_url(),
    }
}

/// 启动一个服务:**先探测,已有则采纳,没有才拉起**。
pub async fn start(
    manager: &Mutex<ServerManager>,
    cfg: &crate::config::ServerConfig,
    kind: ServerKind,
) -> Result<String, String> {
    let port = port_of(kind, &cfg.local);

    // 关键的一步:端口上已经有健康服务就绝不再 spawn。用户自己在终端跑着的
    // 进程、或者上次会话遗留下来的,都在这里被采纳。
    if probe(port).await.is_some() {
        let ours = manager
            .lock()
            .ok()
            .and_then(|mut m| m.snapshot(kind))
            .map(|s| s.alive && s.port == port)
            .unwrap_or(false);
        return Ok(if ours {
            format!("{} 服务已在运行(本应用启动)", kind.label())
        } else {
            format!(
                "{} 服务已在 {} 端口运行(外部进程),已直接连接,未重复启动",
                kind.label(),
                port
            )
        });
    }

    let opts = SpawnOptions::from_config(&cfg.local)?;
    let mut guard = manager.lock().map_err(|e| e.to_string())?;
    let pid = guard.spawn(kind, &opts)?;
    Ok(format!(
        "{} 服务已启动(pid {}),正在加载模型...",
        kind.label(),
        pid
    ))
}

/// 停止一个服务。只停本应用拉起 / 认领的。
pub fn stop(manager: &Mutex<ServerManager>, kind: ServerKind) -> Result<String, String> {
    let mut guard = manager.lock().map_err(|e| e.to_string())?;
    guard.stop(kind)
}

/// 重启:停(不是自己的就跳过)再起。
pub async fn restart(
    manager: &Mutex<ServerManager>,
    cfg: &crate::config::ServerConfig,
    kind: ServerKind,
) -> Result<String, String> {
    // 探测放在加锁之前:`Mutex` 的 guard 不是 Send,跨 await 持有会让整个
    // 命令的 Future 不满足 tauri 的 Send 约束(而且会把别的调用者堵死)。
    let healthy = probe(port_of(kind, &cfg.local)).await.is_some();
    {
        let mut guard = manager.lock().map_err(|e| e.to_string())?;
        if guard.slot(kind).is_some() {
            guard.stop(kind)?;
        } else if healthy {
            // 外部进程停不了,这时候「重启」只会变成「又拉起一个」——
            // 与其偷偷只做一半,不如直说。
            return Err(format!(
                "{} 服务是外部进程,本应用不能重启它。请到启动它的终端里操作。",
                kind.label()
            ));
        }
    }
    start(manager, cfg, kind).await
}

// ── 路径自动探测 ──

/// 检查一个目录是不是 voice-input-framework 仓库根。
fn is_repo_root(path: &Path) -> bool {
    path.join("services/stt_server.py").exists() && path.join("services/llm_server.py").exists()
}

/// 在仓库里找可用的 Python 解释器。
fn find_python(repo: &Path) -> Option<String> {
    for rel in [
        ".venv/bin/python",
        "venv/bin/python",
        ".venv/Scripts/python.exe",
    ] {
        let p = repo.join(rel);
        if p.exists() {
            return Some(p.display().to_string());
        }
    }
    None
}

/// 首次运行时自动探测仓库位置。
///
/// 应用装在 `/Applications`,仓库在用户目录某处,两者没有固定关系,所以只能
/// 猜常见位置 + 从当前工作目录向上找(开发时从 `gui/src-tauri` 里跑)。
/// 猜不到就返回 `None`,由 UI 明确告诉用户「没探测到,请手动填」。
pub fn detect_repo() -> Option<String> {
    let home = std::env::var("HOME").ok().map(PathBuf::from);
    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Some(home) = &home {
        for rel in [
            "voice-input-framework",
            "dev/voice-input-framework",
            "Projects/voice-input-framework",
            "Documents/voice-input-framework",
            "src/voice-input-framework",
            "code/voice-input-framework",
        ] {
            candidates.push(home.join(rel));
        }
    }
    // 开发模式:cwd 通常在 <repo>/gui/src-tauri,向上找。
    if let Ok(cwd) = std::env::current_dir() {
        let mut p = cwd.as_path();
        loop {
            candidates.push(p.to_path_buf());
            match p.parent() {
                Some(parent) => p = parent,
                None => break,
            }
        }
    }

    candidates.into_iter().find(|p| is_repo_root(p)).map(|p| {
        // 规范化,避免把 `.../gui/src-tauri/../..` 这种路径写进配置。
        std::fs::canonicalize(&p).unwrap_or(p).display().to_string()
    })
}

/// 自动探测结果:仓库 + 解释器。给前端「自动探测」按钮用。
#[derive(Debug, Clone, Serialize)]
pub struct DetectResult {
    pub repo_path: Option<String>,
    pub python_path: Option<String>,
    /// 没探测到时的中文说明,直接显示。
    pub problem: Option<String>,
}

pub fn detect() -> DetectResult {
    match detect_repo() {
        Some(repo) => {
            let python = find_python(Path::new(&repo));
            let problem = python.is_none().then(|| {
                format!(
                    "找到仓库 {},但里面没有 .venv/bin/python。请先创建虚拟环境,或手动指定解释器路径。",
                    repo
                )
            });
            DetectResult {
                repo_path: Some(repo),
                python_path: python,
                problem,
            }
        }
        None => DetectResult {
            repo_path: None,
            python_path: None,
            problem: Some(
                "没有自动找到 voice-input-framework 仓库(在 ~ 下的常见位置都找过了)。请手动填写仓库路径。"
                    .into(),
            ),
        },
    }
}

/// 当前配置里的路径是否可用,供 UI 明示问题。
fn path_report(local: &crate::config::LocalServerConfig) -> LocalPathReport {
    let repo_ok = local
        .repo_path
        .as_deref()
        .map(|p| is_repo_root(Path::new(p)))
        .unwrap_or(false);
    let python_ok = local
        .python_path
        .as_deref()
        .map(|p| Path::new(p).exists())
        .unwrap_or(false);

    let problem = match (local.repo_path.as_deref(), local.python_path.as_deref()) {
        (None, _) | (Some(""), _) => Some("未设置仓库路径".to_string()),
        (Some(repo), _) if !repo_ok => Some(format!("{} 下找不到 services/stt_server.py", repo)),
        (_, None) | (_, Some("")) => Some("未设置 Python 解释器路径".to_string()),
        (_, Some(py)) if !python_ok => Some(format!("解释器不存在:{}", py)),
        _ => None,
    };

    LocalPathReport {
        repo_path: local.repo_path.clone(),
        python_path: local.python_path.clone(),
        repo_ok,
        python_ok,
        problem,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::LocalServerConfig;

    #[test]
    fn module_paths_match_the_python_entrypoints() {
        assert_eq!(ServerKind::Stt.module(), "services.stt_server");
        assert_eq!(ServerKind::Llm.module(), "services.llm_server");
    }

    #[test]
    fn state_serializes_as_snake_case() {
        assert_eq!(
            serde_json::to_string(&ServerState::NotConfigured).unwrap(),
            "\"not_configured\""
        );
    }

    /// 路径没配时必须给出可显示的原因,而不是静默失败。
    #[test]
    fn missing_paths_produce_a_readable_problem() {
        let local = LocalServerConfig::default();
        let report = path_report(&local);
        assert!(!report.repo_ok);
        assert_eq!(report.problem.as_deref(), Some("未设置仓库路径"));
        assert!(SpawnOptions::from_config(&local).is_err());
    }

    #[test]
    fn bogus_repo_path_is_reported_with_the_path() {
        let local = LocalServerConfig {
            repo_path: Some("/nonexistent/repo".into()),
            ..Default::default()
        };
        let report = path_report(&local);
        assert!(!report.repo_ok);
        assert!(report.problem.unwrap().contains("/nonexistent/repo"));
    }

    /// 一个不存在的 pid 不该被认领成自己的进程。
    #[test]
    fn dead_pid_is_not_claimed() {
        // 0 在 macOS/Linux 上不是普通进程的 pid,`ps -p 0` 不会给出我们的模块名。
        assert!(!pid_runs_module(0, "services.stt_server"));
    }

    #[test]
    fn repo_root_detection_needs_both_servers() {
        let dir = std::env::temp_dir().join(format!("vif-sm-test-{}", std::process::id()));
        let services = dir.join("services");
        std::fs::create_dir_all(&services).unwrap();
        assert!(!is_repo_root(&dir));
        std::fs::write(services.join("stt_server.py"), "").unwrap();
        assert!(!is_repo_root(&dir));
        std::fs::write(services.join("llm_server.py"), "").unwrap();
        assert!(is_repo_root(&dir));
        std::fs::remove_dir_all(&dir).ok();
    }
}

/// 拉起真实子进程的端到端测试。
///
/// 默认 `#[ignore]`:它要绑端口、起进程,不适合跟普通单测一起跑。
/// 手动执行:`cargo test --lib -- --ignored --test-threads=1`
///
/// 用的是**假仓库**——`services/stt_server.py` 只是个应答 `/health` 的
/// http.server,不加载任何模型;端口也挪到 7544/7545,避开用户自己在
/// 6544/6545 上跑的真服务。走的却是和线上完全相同的代码路径:
/// `Command` 组装、env 注入、日志抽水线程、SIGTERM + 收尸。
#[cfg(test)]
mod e2e {
    use super::*;
    use crate::config::{LocalServerConfig, ServerConfig, ServerMode};

    const TEST_STT_PORT: u16 = 7544;
    const TEST_LLM_PORT: u16 = 7545;

    /// 假服务:先睡 1.5 秒(模拟加载模型,好让 `Starting` 状态可观测),
    /// 再在指定端口上应答 `/health`。
    fn fake_server_py(port_env: &str) -> String {
        format!(
            r#"
import http.server, json, os, sys, time
PORT = int(os.environ["{port_env}"])
print("fake server booting on", PORT, flush=True)
time.sleep(1.5)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({{"status": "ok", "current_model": "fake-model"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a):
        pass
print("fake server listening", flush=True)
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
"#
        )
    }

    struct FakeRepo {
        root: PathBuf,
    }

    impl FakeRepo {
        fn create() -> Self {
            let root = std::env::temp_dir().join(format!("vif-e2e-{}", std::process::id()));
            let services = root.join("services");
            std::fs::create_dir_all(&services).unwrap();
            std::fs::write(services.join("__init__.py"), "").unwrap();
            std::fs::write(
                services.join("stt_server.py"),
                fake_server_py("VIF_STT_PORT"),
            )
            .unwrap();
            std::fs::write(
                services.join("llm_server.py"),
                fake_server_py("VIF_LLM_PORT"),
            )
            .unwrap();
            Self { root }
        }

        fn config(&self) -> ServerConfig {
            ServerConfig {
                host: "127.0.0.1".into(),
                port: TEST_STT_PORT,
                mode: ServerMode::Local,
                local: LocalServerConfig {
                    repo_path: Some(self.root.display().to_string()),
                    python_path: Some(system_python()),
                    stt_port: TEST_STT_PORT,
                    llm_port: TEST_LLM_PORT,
                    ..Default::default()
                },
            }
        }

        fn data_dir(&self) -> PathBuf {
            self.root.join("appdata")
        }
    }

    impl Drop for FakeRepo {
        fn drop(&mut self) {
            std::fs::remove_dir_all(&self.root).ok();
        }
    }

    fn system_python() -> String {
        for p in ["/usr/bin/python3", "/opt/homebrew/bin/python3"] {
            if Path::new(p).exists() {
                return p.to_string();
            }
        }
        panic!("找不到系统 python3");
    }

    /// 轮询等待 `/health` 变成期望的通 / 不通状态。
    async fn wait_health(port: u16, want: bool, secs: u64) -> bool {
        let deadline = Instant::now() + Duration::from_secs(secs);
        while Instant::now() < deadline {
            if probe(port).await.is_some() == want {
                return true;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        false
    }

    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7544 端口"]
    async fn spawn_then_adopt_then_stop() {
        let repo = FakeRepo::create();
        let cfg = repo.config();
        let manager = Mutex::new(ServerManager::new(repo.data_dir()));

        // 前置检查:测试端口必须是空的,否则后面的断言全不成立。
        assert!(
            probe(TEST_STT_PORT).await.is_none(),
            "{} 端口上已经有服务了,测试无法进行",
            TEST_STT_PORT
        );

        // ── 1. 拉起 ──
        let msg = start(&manager, &cfg, ServerKind::Stt).await.unwrap();
        assert!(msg.contains("已启动"), "{}", msg);

        // 健康还没通,但进程活着 → Starting(假服务故意睡了 1.5 秒)。
        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Starting, "{:?}", st);
        assert!(st.managed);
        assert!(st.pid.is_some());

        // ── 2. 起来之后是 Running,且带模型名 ──
        assert!(wait_health(TEST_STT_PORT, true, 15).await, "服务没起来");
        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Running);
        assert!(st.managed, "自己拉起的必须标成 managed");
        assert_eq!(st.current_model.as_deref(), Some("fake-model"));

        // ── 3. 日志被抓到了(文件 + 内存尾巴)──
        let log_path = st.log_path.clone().expect("应该有日志路径");
        let log_text = std::fs::read_to_string(&log_path).unwrap();
        assert!(log_text.contains("fake server booting"), "{}", log_text);
        assert!(
            st.recent_logs.iter().any(|l| l.contains("fake server")),
            "{:?}",
            st.recent_logs
        );

        // ── 4. 重复 start 不会再拉起一个 ──
        let msg = start(&manager, &cfg, ServerKind::Stt).await.unwrap();
        assert!(msg.contains("本应用启动"), "{}", msg);

        // ── 5. 停得掉 ──
        stop(&manager, ServerKind::Stt).unwrap();
        assert!(wait_health(TEST_STT_PORT, false, 10).await, "没停干净");
        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Stopped);

        // ── 6. 外部进程:采纳,但不许停 ──
        let mut external = Command::new(system_python())
            .arg("-m")
            .arg("services.stt_server")
            .current_dir(&repo.root)
            .env("VIF_STT_PORT", TEST_STT_PORT.to_string())
            .env("PYTHONUNBUFFERED", "1")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        assert!(
            wait_health(TEST_STT_PORT, true, 15).await,
            "外部假服务没起来"
        );

        let msg = start(&manager, &cfg, ServerKind::Stt).await.unwrap();
        assert!(msg.contains("外部进程"), "不该重复拉起: {}", msg);

        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Running);
        assert!(!st.managed, "外部进程绝不能标成本应用管理");
        assert!(st.detail.unwrap().contains("不能从这里停止"));

        // 停止必须被拒绝——这是「绝不杀别人进程」那条铁律的断言。
        let err = stop(&manager, ServerKind::Stt).unwrap_err();
        assert!(err.contains("不是由本应用启动的"), "{}", err);

        // 外部进程由测试自己收拾(模拟用户关掉自己的终端)。
        external.kill().ok();
        external.wait().ok();
        assert!(wait_health(TEST_STT_PORT, false, 10).await);
    }

    /// 改端口之后重新启动,不能把老进程丢成孤儿。
    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7544/7546 端口"]
    async fn changing_port_does_not_orphan_the_old_child() {
        const NEW_PORT: u16 = 7546;
        let repo = FakeRepo::create();
        let mut cfg = repo.config();
        let manager = Mutex::new(ServerManager::new(repo.data_dir()));

        assert!(probe(TEST_STT_PORT).await.is_none(), "测试端口不干净");
        assert!(probe(NEW_PORT).await.is_none(), "测试端口不干净");

        start(&manager, &cfg, ServerKind::Stt).await.unwrap();
        assert!(wait_health(TEST_STT_PORT, true, 15).await);
        let old_pid = status(&manager, &cfg, ServerKind::Stt).await.pid.unwrap();

        // 用户在 UI 里改了端口。老进程还在老端口上跑着。
        cfg.local.stt_port = NEW_PORT;
        cfg.port = NEW_PORT;

        // 端口对不上,所以不算「本应用管理中」——否则停止按钮会作用到
        // 新端口上的别人身上。
        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert!(!st.managed, "端口不一致时不能标成 managed: {:?}", st);

        // 在新端口上重新启动:老的必须被带走,不能留成孤儿。
        start(&manager, &cfg, ServerKind::Stt).await.unwrap();
        assert!(wait_health(NEW_PORT, true, 15).await, "新端口没起来");
        assert!(!pid_alive(old_pid), "老进程 {} 成了孤儿", old_pid);
        assert!(wait_health(TEST_STT_PORT, false, 10).await, "老端口没释放");

        manager.lock().unwrap().shutdown_all();
        assert!(wait_health(NEW_PORT, false, 10).await);
    }

    /// 应用退出时必须把自己拉起的子进程带走。
    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7545 端口"]
    async fn shutdown_all_leaves_nothing_behind() {
        let repo = FakeRepo::create();
        let cfg = repo.config();
        let manager = Mutex::new(ServerManager::new(repo.data_dir()));

        assert!(probe(TEST_LLM_PORT).await.is_none(), "测试端口不干净");

        start(&manager, &cfg, ServerKind::Llm).await.unwrap();
        assert!(wait_health(TEST_LLM_PORT, true, 15).await);
        let pid = status(&manager, &cfg, ServerKind::Llm).await.pid.unwrap();

        manager.lock().unwrap().shutdown_all();

        assert!(wait_health(TEST_LLM_PORT, false, 10).await, "端口没释放");
        assert!(!pid_alive(pid), "子进程 {} 还活着,成了孤儿", pid);
    }
}

#[cfg(test)]
mod detect_smoke {
    use super::*;

    /// 在真机上跑一次自动探测。`#[ignore]`:结果取决于这台机器上仓库放哪儿,
    /// 不适合当常规单测。手动执行:`cargo test --lib -- --ignored detect`
    #[test]
    #[ignore = "依赖本机仓库位置"]
    fn detect_finds_a_real_repo() {
        let d = detect();
        println!("repo   = {:?}", d.repo_path);
        println!("python = {:?}", d.python_path);
        println!("problem= {:?}", d.problem);
        let repo = d.repo_path.expect("应该探测到仓库");
        assert!(is_repo_root(Path::new(&repo)));
        assert!(d.python_path.is_some(), "应该在仓库里找到 .venv/bin/python");
    }
}
