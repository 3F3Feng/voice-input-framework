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
//! 端口健康 + 本进程手里有对应的活着的子进程 = 自己的(可停)。
//! 这样即使中途状态错乱也会自愈。
//!
//! 一个例外是「上次会话的遗孤」:应用被 SIGKILL / 崩溃时来不及杀子进程,
//! 下次启动时那两个服务还在监听。纯靠上面的规则会把它们判成「外部进程」,
//! 用户明明是从应用里启动的却停不掉。为此 spawn 时把 pid 落盘
//! (`managed-servers.json`),启动时校验 pid 仍然活着 **且** 命令行确实是对应
//! 的模块,才认领回来(`Handle::Reclaimed`)——pid 会被系统复用,只比对 pid
//! 是不够的。
//!
//! 手里没有句柄、端口却健康,曾经一律判成「外部进程,不可停」。这条规矩定得
//! 太宽了:它本意是防止误杀「碰巧占着这个端口的陌生进程」,结果连用户自己在
//! 终端里 `python -m services.stt_server` 起的**本项目**服务也一并锁死——想从
//! 界面上管自己的服务,得先回终端把它杀掉,纯粹的摩擦。现在改成先去问一句
//! 「端口上监听的到底是谁」(见 `identify_external`):认得出是本项目的服务就
//! 允许停,认不出才拒绝。铁律没有松动,只是从「不是我起的就不碰」收紧成了
//! 「认不出身份的不碰」——见 `ServerOwner`。

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
    /// 子进程退出了(或者压根没起来),或者进程活着但模型加载失败。`detail` 里是原因。
    Failed,
}

/// 端口上那个进程和本应用是什么关系。决定「停止 / 重启」能不能点。
///
/// 分三档而不是两档,是这次改动的核心。原来只有「本应用启动 / 外部」两档,
/// 外部一律不可停;可「外部」里其实混着两种完全不同的东西:用户自己在终端里
/// 跑的**本项目**服务,和一个碰巧占着这个端口的陌生进程。前者理应能从界面上
/// 停掉,后者绝对不能碰。两者合并成一档,就只能按后者的标准一刀切,代价是
/// 前者也被锁死。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServerOwner {
    /// 本应用 spawn 的,或上次会话遗留、启动时认领回来的。
    App,
    /// 不是本应用启动的,但身份校验通过:跑的是本项目的模块,且工作目录就是
    /// 当前配置的仓库根(见 `pid_is_project_server`)。可以停。
    ExternalProject,
    /// 端口上有健康服务,但对不上号——认不出来,或者认出来是别的 checkout。
    /// 一律不碰。没有任何进程时也取这一档(最保守的那个)。
    ExternalUnknown,
}

impl ServerOwner {
    /// 允许从界面停止 / 重启吗?只有前两档可以。
    pub fn can_manage(self) -> bool {
        matches!(self, ServerOwner::App | ServerOwner::ExternalProject)
    }
}

/// 一个服务的完整状态快照,直接丢给前端。
#[derive(Debug, Clone, Serialize)]
pub struct ServerStatus {
    pub kind: ServerKind,
    pub state: ServerState,
    pub port: u16,
    /// 这个进程和本应用的关系,决定停止 / 重启按钮的可用性。
    ///
    /// `ExternalUnknown` 且 `state == Running` 表示端口上有服务但认不出身份:
    /// **停止按钮必须禁用**,否则就是在误导用户——真去停了就是在杀别人的进程。
    pub owner: ServerOwner,
    /// 「停止 / 重启」能不能点。规则只在 `ServerOwner::can_manage` 里写一遍,
    /// 前端直接用,不要自己再推一遍——两处各写一份迟早会对不上。
    pub can_stop: bool,
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
    /// 看不到命令行的平台(Windows)干脆不认领,见 `pid_runs_module`。
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
        if let Some(mut old) = self.slot(kind).take() {
            if old.alive() {
                let _ = Self::stop_slot(kind, old);
            }
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
            // 管道另一头是我们,不是终端。Python 这时按系统区域设置编码输出,
            // 中文 Windows 上就是 GBK——日志里全是乱码。明确要 UTF-8。
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1")
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

        let mut child = no_console(&mut cmd)
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

    /// 把句柄从管理器里取走。取走之后等待进程退出的那几秒就不必再占着锁了,
    /// 见 `stop`。丢弃 `Child` 不会杀进程,所以取走本身是安全的。
    fn take_slot(&mut self, kind: ServerKind) -> Option<Slot> {
        self.slot(kind).take()
    }

    /// 停一个不是本应用启动、但校验过确实属于本项目的服务。
    ///
    /// 这是整个模块里唯一会对「别人的进程」发信号的地方,所以身份必须**在发
    /// 信号的那一刻**重新算一遍,而不能沿用状态轮询时的结论:轮询和用户点按钮
    /// 之间隔着几秒,这几秒里进程完全可能已经退出、pid 被系统分配给了别的程序。
    /// 拿旧结论去杀新 pid,就是在赌。
    fn stop_external(
        kind: ServerKind,
        local: &crate::config::LocalServerConfig,
    ) -> Result<String, String> {
        // 这一句就是「发信号前重新校验」:`identify_external` 每次都现查 lsof + ps。
        let Some(pid) = identify_external(kind, local) else {
            return Err(format!(
                "{} 服务不是由本应用启动的,也认不出是本项目的服务,无法从这里停止。请到启动它的终端里停。",
                kind.label()
            ));
        };
        let repo = PathBuf::from(local.repo_path.clone().unwrap_or_default());
        let stopped = format!("{} 服务(pid {},本项目的外部进程)已停止", kind.label(), pid);

        terminate(pid);
        let deadline = Instant::now() + TERM_GRACE;
        while Instant::now() < deadline {
            // 等待条件用的是「它**还是不是**本项目的那个服务」,而不是单纯的 pid
            // 判活。差别在两个真实情况上:进程退成僵尸时 `kill -0` 仍然成功(命令行
            // 已经变成 `<defunct>`),pid 被复用时也成功——两种情况下继续死等都是
            // 错的,最后还会对着一个不知道是谁的 pid 发 SIGKILL。
            if !pid_is_project_server(pid, kind.module(), &repo) {
                return Ok(stopped);
            }
            std::thread::sleep(Duration::from_millis(200));
        }

        // 赖着不走才升级到 SIGKILL。发信号前再确认一次身份——上面的循环条件已经
        // 是这个校验了,这里再查一次是为了把「绝不对没通过校验的 pid 发信号」这条
        // 写死在发信号的那一行旁边,而不是依赖读者去推循环的退出条件。
        if !pid_is_project_server(pid, kind.module(), &repo) {
            return Ok(stopped);
        }
        force_kill(pid);
        Ok(format!(
            "{} 服务(pid {},本项目的外部进程)未响应,已强制结束",
            kind.label(),
            pid
        ))
    }

    /// 停止本应用拉起(或认领)的进程。调用方负责把 slot 取出来交进来,
    /// 并在返回之后自己更新 pid 记账(`persist_pids`)——这个函数会在里面
    /// 干等最多 `TERM_GRACE`,不该在这段时间里占着管理器的锁。
    fn stop_slot(kind: ServerKind, mut slot: Slot) -> Result<String, String> {
        let pid = slot.pid;

        if !slot.alive() {
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
        Ok(if forced {
            format!("{} 服务(pid {})未响应,已强制结束", kind.label(), pid)
        } else {
            format!("{} 服务(pid {})已停止", kind.label(), pid)
        })
    }

    /// 应用退出时调用:把所有自己拉起的子进程带走,不留孤儿。
    pub fn shutdown_all(&mut self) {
        for kind in [ServerKind::Stt, ServerKind::Llm] {
            // 只带走自己手里的句柄。退出应用**不该**顺手停掉用户自己在终端里
            // 跑的服务,哪怕现在已经有能力停了——那是用户的进程,不是我们的。
            if let Some(slot) = self.slot(kind).take() {
                let _ = Self::stop_slot(kind, slot);
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

/// 单行日志的字节上限。一直不换行的输出(比如某个库把整个进度条画在一行里)
/// 攒到这么长就先切一刀,免得内存里的「半行」无限长大。
const MAX_LOG_LINE_BYTES: usize = 16 * 1024;

/// 把字节流切成日志行。`\n` 和 `\r` 都算换行。
///
/// 以前用的是 `BufRead::lines()`,它有两个问题:
///
/// - **遇到一行非 UTF-8 就返回 Err,抽水线程随之 `break` 退出。** 之后再没人读
///   这根管道,缓冲写满(约 64 KB)后 Python 服务阻塞在 write 上,整个服务卡死,
///   而界面上看到的只是「一直在加载」。中文 Windows 上 Python 很可能按 GBK 输出,
///   这不是理论风险。现在一律 `from_utf8_lossy`,坏字节变成 `�`,绝不停止读取。
/// - **只认 `\n`。** 下载模型时 tqdm 用 `\r` 原地刷新进度条,整个下载过程在它
///   看来是同一行,内存里攒成一个越来越长的字符串,日志尾巴上也一直看不到进度。
#[derive(Default)]
struct LineSplitter {
    pending: Vec<u8>,
}

impl LineSplitter {
    /// 喂一段字节,吐出其中已经完整的行(空行丢掉:`\r\n` 会切出一个空行)。
    fn feed(&mut self, chunk: &[u8]) -> Vec<String> {
        let mut out = Vec::new();
        for &b in chunk {
            if b == b'\n' || b == b'\r' {
                self.flush_into(&mut out);
            } else {
                self.pending.push(b);
                if self.pending.len() >= MAX_LOG_LINE_BYTES {
                    self.flush_into(&mut out);
                }
            }
        }
        out
    }

    /// 流结束时剩下的那半行。
    fn finish(mut self) -> Option<String> {
        let mut out = Vec::new();
        self.flush_into(&mut out);
        out.pop()
    }

    fn flush_into(&mut self, out: &mut Vec<String>) {
        if !self.pending.is_empty() {
            out.push(String::from_utf8_lossy(&self.pending).into_owned());
            self.pending.clear();
        }
    }
}

/// 把子进程的一个输出流读进环形缓冲 + 追加写日志文件。
///
/// 必须消费掉管道:不读的话管道缓冲写满后子进程会阻塞在 write 上卡死。所以
/// 这个线程只在 EOF(进程退了)或读出错时才退出,内容再怪也照读不误。
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
            let mut reader = BufReader::new(stream);
            let mut file = OpenOptions::new().append(true).open(&log_path).ok();
            let mut splitter = LineSplitter::default();
            let mut record = |line: String| {
                if let Some(f) = file.as_mut() {
                    let _ = writeln!(f, "{}", line);
                }
                if let Ok(mut buf) = logs.lock() {
                    if buf.len() == LOG_TAIL_LINES {
                        buf.pop_front();
                    }
                    buf.push_back(line);
                }
            };
            loop {
                let chunk = match reader.fill_buf() {
                    Ok([]) => break,
                    Ok(chunk) => chunk,
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(_) => break,
                };
                let n = chunk.len();
                for line in splitter.feed(chunk) {
                    record(line);
                }
                reader.consume(n);
            }
            if let Some(line) = splitter.finish() {
                record(line);
            }
        });
}

// ── 进程探测 / 信号(平台相关)──

/// Windows 上 `CreateProcess` 的 `CREATE_NO_WINDOW`。
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// 让子进程不弹控制台窗口。
///
/// 本应用是 GUI 子系统程序,自己没有控制台;从它起一个控制台程序(`python.exe`、
/// `tasklist`、`taskkill`)时,Windows 会给子进程新开一个黑窗口。服务启动时弹一个
/// 还算看得见原因,状态轮询每 3 秒经 `pid_alive` 跑一次 `tasklist`,就是每 3 秒
/// 闪一下黑框。其它平台什么都不做。
fn no_console(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

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
    no_console(Command::new("tasklist").args([
        "/FI",
        &format!("PID eq {}", pid),
        "/FO",
        "CSV",
        "/NH",
    ]))
    .output()
    .map(|o| tasklist_lists_pid(&String::from_utf8_lossy(&o.stdout), pid))
    .unwrap_or(false)
}

/// `tasklist /FO CSV /NH` 的输出里有没有这个 pid。
///
/// 以前是对整段输出做 `contains(pid)`:pid 12 会命中 pid 1234 那一行,内存占用
/// 那一栏(`"12,345 K"`)也能命中——死掉的进程被当成活着,「停止」就会一直等、
/// 最后对着一个不相干的 pid 发 `/F`。现在只比第二栏(PID)整值相等。没有匹配
/// 进程时 tasklist 打的是一行「INFO: ...」提示,不带引号,自然不会命中。
#[cfg_attr(unix, allow(dead_code))]
fn tasklist_lists_pid(stdout: &str, pid: u32) -> bool {
    stdout.lines().any(|line| {
        line.split("\",\"")
            .nth(1)
            .map(|field| field.trim_matches('"').trim())
            .and_then(|field| field.parse::<u32>().ok())
            == Some(pid)
    })
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

/// Windows 上一律返回 false,也就是**不认领遗孤**。
///
/// 以前这里退化成「进程还在就认领」:`tasklist` 拿不到命令行,只能比 pid。可是
/// 重启过电脑之后,记账文件里那个 pid 早就被别的程序用上了——认领回来,应用
/// 退出时 `shutdown_all` 会对它 `taskkill /T`,把一整棵毫不相干的进程树杀掉,
/// 「停止」按钮同理。
///
/// 不认领的代价很小:遗孤如果真还在跑,端口是通的,`start` / `status` 会把它当成
/// 外部进程采纳(只连接,不碰)。宁可让用户回任务管理器里结束它,也不能凭一个
/// 可能已经被复用的 pid 去杀进程。等哪天接上 `Win32_Process.CommandLine` 再放开。
#[cfg(not(unix))]
fn pid_runs_module(_pid: u32, _module: &str) -> bool {
    false
}

// ── 端口上监听的到底是谁 ──
//
// 「手里没句柄」不等于「不认识」。下面这一组函数就是去把端口上的进程认出来,
// 好让用户自己在终端里起的本项目服务也能从界面上管起来。

/// 正在监听某个 TCP 端口的进程 pid。
///
/// `-t` 只输出 pid;`-nP` 关掉 DNS 和服务名反查(纯粹是提速,本地回环没必要
/// 查);`-sTCP:LISTEN` 把 ESTABLISHED 的连接排除掉——不加这一条,正连着这个
/// 端口的客户端(包括本应用自己)也会被算进来,那就南辕北辙了。
///
/// 同一个进程同时监听 v4/v6 时 `-t` 会吐出重复的 pid,去重。`lsof` 不在(某些
/// 精简的 Linux 发行版)或查不动时返回空——调用方会因此判成「认不出」,
/// 也就是退回改动之前的行为,安全。
#[cfg(unix)]
fn port_listener_pids(port: u16) -> Vec<u32> {
    let Ok(out) = Command::new("lsof")
        .args(["-nP", &format!("-iTCP:{}", port), "-sTCP:LISTEN", "-t"])
        .stderr(Stdio::null())
        .output()
    else {
        return Vec::new();
    };
    // 注意不看退出码:`lsof` 查不到东西时就是非 0,那是正常情况而不是错误。
    let mut pids: Vec<u32> = String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|l| l.trim().parse::<u32>().ok())
        .collect();
    pids.sort_unstable();
    pids.dedup();
    pids
}

/// 进程的当前工作目录。
///
/// `-a` 是把 `-p`(进程)和 `-d cwd`(只要 cwd 这一个「fd」)两个条件**求交**——
/// `lsof` 默认是求并,不加 `-a` 会把该进程打开的所有文件都列出来。`-Fn` 输出
/// 机器可读的字段行(`p<pid>` / `fcwd` / `n<路径>`),我们要的是 `n` 那一行。
#[cfg(unix)]
fn pid_cwd(pid: u32) -> Option<PathBuf> {
    let out = Command::new("lsof")
        .args(["-p", &pid.to_string(), "-a", "-d", "cwd", "-Fn"])
        .stderr(Stdio::null())
        .output()
        .ok()?;
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .find_map(|l| l.strip_prefix('n'))
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
}

/// 两个路径是不是同一个目录。软链接、`..`、结尾斜杠都规范化掉再比。
///
/// macOS 上这一步不是可选的:临时目录 `/var/folders/...` 实际是
/// `/private/var/folders/...` 的软链,`lsof` 报后者而配置里存的可能是前者。
fn same_dir(a: &Path, b: &Path) -> bool {
    match (std::fs::canonicalize(a), std::fs::canonicalize(b)) {
        (Ok(a), Ok(b)) => a == b,
        // 规范化失败(目录已经被删了)就退回字面比较,不做任何猜测。
        _ => a == b,
    }
}

/// 这个 pid 是不是「本项目的 `module` 服务」。两道关卡都得过:
///
/// 1. 命令行里确实有 `services.stt_server` / `services.llm_server`;
/// 2. 进程的工作目录就是当前配置的那个仓库根。
///
/// 第二条不能拿命令行顶替。用户在终端里敲的是
/// `.venv/bin/python -m services.stt_server`——解释器是**相对路径**,整条命令行
/// 里压根没有仓库路径可比(这是在用户真实跑着的进程上核对过的)。而
/// `python -m services.stt_server` 能跑起来这件事本身就说明 `services` 包是相对
/// cwd 解析出来的,所以 cwd 才是那个既拿得到、又真正说明问题的锚点。
///
/// 严到什么程度是个取舍:只比模块名太松,别的 checkout 里的同名服务会被当成
/// 自己的,那就退回了「杀错人」的老风险;再往严了走(比如要求解释器路径也在
/// 仓库里)又会把 `conda` / 系统 python 起的服务挡在外面,重新制造这次要解决的
/// 摩擦。「模块名 + cwd」正好卡在中间:它唯一放过的情况,是同一个仓库目录下
/// 用户用别的方式跑起来的同一个服务——而那本来就该算是本项目的服务。
///
/// 拿不到 cwd(`lsof` 不在、权限不够、进程刚退出)一律返回 false:宁可让用户
/// 回终端去停,也不能凭猜测发信号。
#[cfg(unix)]
fn pid_is_project_server(pid: u32, module: &str, repo: &Path) -> bool {
    if !pid_runs_module(pid, module) {
        return false;
    }
    match pid_cwd(pid) {
        Some(cwd) => same_dir(&cwd, repo),
        None => false,
    }
}

/// Windows 上没有 `lsof`,`tasklist` 既给不出完整命令行也给不出工作目录,拿不到
/// 任何能把端口上的进程和本项目对上号的证据。所以这里一律返回 false:所有
/// 「端口健康但手里没句柄」的情况都停在 `ExternalUnknown`,功能上退回改动之前
/// (用户仍需回终端停自己的服务),但绝不会误杀无关进程。等哪天接上 WMI 的
/// `Win32_Process.CommandLine` 再放开。
#[cfg(not(unix))]
fn pid_is_project_server(_pid: u32, _module: &str, _repo: &Path) -> bool {
    false
}

#[cfg(not(unix))]
fn port_listener_pids(_port: u16) -> Vec<u32> {
    Vec::new()
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
    let _ = no_console(Command::new("taskkill").args(["/PID", &pid.to_string(), "/T"])).status();
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
    let _ =
        no_console(Command::new("taskkill").args(["/PID", &pid.to_string(), "/T", "/F"])).status();
}

// ── 健康探测 ──

#[derive(Debug, Deserialize)]
struct Health {
    status: String,
    current_model: Option<String>,
    /// `status == "error"` 时服务端给出的加载失败原因。
    #[serde(default)]
    error: Option<String>,
    /// 加载进度(STT 服务端 `/health.loading`,见 services/stt_engine.py)。
    #[serde(default)]
    loading: Option<LoadingProgress>,
}

#[derive(Debug, Clone, Deserialize)]
struct LoadingProgress {
    #[serde(default)]
    elapsed_s: f64,
    #[serde(default)]
    downloaded_bytes: u64,
}

/// 「启动中」那一行怎么说。首次用一个模型要下几百 MB 到几 GB,以前全程只有一句
/// 「正在加载模型...」,看不出是在下载、卡住了还是坏了。
fn loading_text(progress: Option<&LoadingProgress>) -> String {
    match progress {
        Some(p) if p.downloaded_bytes >= 1024 * 1024 => format!(
            "正在下载模型… 已下载 {} MB({:.0} 秒)",
            p.downloaded_bytes / (1024 * 1024),
            p.elapsed_s
        ),
        Some(p) if p.elapsed_s >= 1.0 => format!("正在加载模型…({:.0} 秒)", p.elapsed_s),
        _ => "正在加载模型...".to_string(),
    }
}

/// 端口上那个服务说它的模型怎么样了。
#[derive(Debug, Clone, PartialEq, Eq)]
enum Answer {
    /// `status == "ok"`,能用。
    Ready,
    /// 还在加载(或者是个不认识的状态——老服务端只会说 ok / loading)。
    Loading,
    /// 加载失败,带原因。服务不会自己好起来。
    Failed(String),
}

impl Health {
    fn answer(&self) -> Answer {
        match self.status.as_str() {
            "ok" => Answer::Ready,
            "error" => Answer::Failed(
                self.error
                    .clone()
                    .filter(|e| !e.trim().is_empty())
                    .unwrap_or_else(|| "原因未知,见日志".into()),
            ),
            _ => Answer::Loading,
        }
    }
}

/// 打一次 `/health`,只有模型就绪(`status == "ok"`)才算数。线上代码都改用
/// `probe_raw` 按「端口有应答」和 `Health::answer` 判断了,这个只剩测试在用。
#[cfg(test)]
async fn probe(port: u16) -> Option<Health> {
    probe_raw(port).await.filter(|health| health.status == "ok")
}

/// 打一次 `/health`,不管 `status` 是什么都原样返回。只有 `status` 能区分
/// 「还在加载」和「加载失败了」——后者不报出来,界面会永远停在「正在加载模型」。
async fn probe_raw(port: u16) -> Option<Health> {
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
    resp.json().await.ok()
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

/// 端口上那个不是本应用启动的服务,认得出是本项目的吗?认得出就给出它的 pid。
///
/// 只在「端口健康但手里没有句柄」时才需要问这个问题。没配仓库路径就无从比对,
/// 直接放弃(而不是退化成只比模块名——那正是要避免的松)。
///
/// 端口上可能有不止一个监听者(v4/v6 被不同进程分别占、或者 `SO_REUSEPORT`),
/// 挑法很直白:取第一个通过身份校验的。通不过的那些本来也不该碰。
fn identify_external(kind: ServerKind, local: &crate::config::LocalServerConfig) -> Option<u32> {
    let repo = local
        .repo_path
        .as_deref()
        .map(str::trim)
        .filter(|p| !p.is_empty())?;
    let repo = Path::new(repo);
    let module = kind.module();
    port_listener_pids(port_of(kind, local))
        .into_iter()
        .find(|&pid| pid_is_project_server(pid, module, repo))
}

/// 单个服务的状态。
pub async fn status(
    manager: &Mutex<ServerManager>,
    cfg: &crate::config::ServerConfig,
    kind: ServerKind,
) -> ServerStatus {
    let port = port_of(kind, &cfg.local);
    let raw = probe_raw(port).await;
    // 端口有应答、但模型还没就绪(加载中 / 加载失败)。下面两类分支都要它:
    // 自己的进程要分开报「加载中」和「加载失败」,别人的进程也不能当成「未运行」。
    let pending = raw
        .as_ref()
        .map(Health::answer)
        .filter(|a| *a != Answer::Ready);
    // 进程活着、端口也应答,但模型加载失败了:得和「还在加载」分开报。
    let load_error = match &pending {
        Some(Answer::Failed(reason)) => Some(reason.clone()),
        _ => None,
    };
    let loading = loading_text(raw.as_ref().and_then(|h| h.loading.as_ref()));
    let health = raw.filter(|h| h.status == "ok");

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
            owner: ServerOwner::App,
            can_stop: ServerOwner::App.can_manage(),
            pid: Some(snap.pid),
            current_model: h.current_model,
            detail: None,
            log_path: Some(snap.log_path),
            recent_logs: snap.recent_logs,
        },
        // 端口健康,但本应用手里没有活着的进程。**不再一律推给用户**:先去问
        // 一句端口上监听的到底是谁。认得出是本项目的服务(用户自己在终端里起
        // 的那个)就允许从界面上停;认不出才是真正的「别人的进程」,拒绝。
        (Some(h), other) => {
            let (owner, pid, detail) = match identify_external(kind, &cfg.local) {
                Some(pid) => (
                    ServerOwner::ExternalProject,
                    Some(pid),
                    "这个服务不是本应用启动的,但确认是本项目的服务,可以从这里停止",
                ),
                None => (
                    ServerOwner::ExternalUnknown,
                    None,
                    "外部进程(不是本应用启动的),只能连接,不能从这里停止",
                ),
            };
            ServerStatus {
                kind,
                state: ServerState::Running,
                port,
                owner,
                can_stop: owner.can_manage(),
                pid,
                current_model: h.current_model,
                detail: Some(detail.into()),
                log_path: other.as_ref().map(|s| s.log_path.clone()),
                recent_logs: other.map(|s| s.recent_logs).unwrap_or_default(),
            }
        }
        // 进程还活着,`/health` 明确说模型加载失败了。不能再报「启动中」:
        // 服务不会自己好起来,等多久都一样。
        (None, Some(snap)) if snap.alive && snap.port == port && load_error.is_some() => {
            ServerStatus {
                kind,
                state: ServerState::Failed,
                port,
                owner: ServerOwner::App,
                can_stop: ServerOwner::App.can_manage(),
                pid: Some(snap.pid),
                current_model: None,
                detail: Some(format!("模型加载失败:{}", load_error.unwrap_or_default())),
                log_path: Some(snap.log_path),
                recent_logs: snap.recent_logs,
            }
        }
        // 进程还活着但 `/health` 没通 = 正在加载模型(同样要求端口一致)。
        (None, Some(snap)) if snap.alive && snap.port == port => ServerStatus {
            kind,
            state: ServerState::Starting,
            port,
            owner: ServerOwner::App,
            can_stop: ServerOwner::App.can_manage(),
            pid: Some(snap.pid),
            current_model: None,
            detail: Some(format!("已启动,{}", loading)),
            log_path: Some(snap.log_path),
            recent_logs: snap.recent_logs,
        },
        // 端口有应答、模型还没就绪,但应答的**不是**本应用手里那个进程——典型是
        // 用户在终端里起的服务正在加载模型。以前这里要么落进下面的「未运行」
        // (用户于是点「启动」,又拉起一个进程去抢端口),要么落进「进程已退出」
        // (手里还攥着一个早就死掉的旧句柄时)。
        (None, other) if pending.is_some() => external_pending_status(
            kind,
            port,
            pending.unwrap_or(Answer::Loading),
            identify_external(kind, &cfg.local),
            other,
            &loading,
        ),
        // 进程没了且端口也不通 = 起失败了,把退出原因和日志尾巴一起给出去。
        (None, Some(snap)) => ServerStatus {
            kind,
            state: ServerState::Failed,
            port,
            // 失败的那个进程确实是本应用起的,如实标出来:用户看到「本应用启动
            // / 失败」才知道该去翻下面的日志尾巴。
            owner: ServerOwner::App,
            can_stop: ServerOwner::App.can_manage(),
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
            // 压根没有进程,这个字段没有意义,取最保守的一档。
            owner: ServerOwner::ExternalUnknown,
            can_stop: ServerOwner::ExternalUnknown.can_manage(),
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

/// 端口上有别人的服务在应答,但模型还没就绪时的状态。纯函数:身份识别
/// (`identify_external`,要查 lsof / ps)由调用方做好传进来,这里只管怎么报。
fn external_pending_status(
    kind: ServerKind,
    port: u16,
    answer: Answer,
    external_pid: Option<u32>,
    stale: Option<SlotSnapshot>,
    loading: &str,
) -> ServerStatus {
    let (owner, pid, owner_note) = match external_pid {
        Some(pid) => (
            ServerOwner::ExternalProject,
            Some(pid),
            "不是本应用启动的,但确认是本项目的服务,可以从这里停止",
        ),
        None => (
            ServerOwner::ExternalUnknown,
            None,
            "外部进程(不是本应用启动的),只能连接,不能从这里停止",
        ),
    };
    let (state, what) = match answer {
        Answer::Failed(reason) => (ServerState::Failed, format!("模型加载失败:{}", reason)),
        _ => (ServerState::Starting, loading.to_string()),
    };
    ServerStatus {
        kind,
        state,
        port,
        owner,
        can_stop: owner.can_manage(),
        pid,
        current_model: None,
        detail: Some(format!("{}({})", what, owner_note)),
        log_path: stale.as_ref().map(|s| s.log_path.clone()),
        recent_logs: stale.map(|s| s.recent_logs).unwrap_or_default(),
    }
}

/// 端口上已经有服务在应答时,`start` 怎么回答。纯函数,便于单测。
///
/// 以前只采纳 `status == "ok"` 的服务:用户在终端里起的服务正在加载模型时点
/// 「启动」,会再拉一个进程去抢端口——新进程绑不上端口直接退出,界面报失败,
/// 而终端里那个其实好好的。现在只要端口有应答就不再拉起:
///
/// - 就绪 / 加载中:采纳,如实说在干什么;
/// - 加载失败:返回错误并带上原因。再起一个也绑不上端口,只能先停掉它。
fn adopt_existing(
    kind: ServerKind,
    port: u16,
    answer: &Answer,
    ours: bool,
    external_pid: Option<u32>,
) -> Result<String, String> {
    let who = if ours {
        "本应用启动".to_string()
    } else {
        match external_pid {
            Some(pid) => format!("外部进程 pid {},确认是本项目的服务", pid),
            None => "外部进程".to_string(),
        }
    };
    match answer {
        Answer::Ready if ours => Ok(format!("{} 服务已在运行(本应用启动)", kind.label())),
        Answer::Ready => Ok(format!(
            "{} 服务已在 {} 端口运行({}),已直接连接,未重复启动",
            kind.label(),
            port,
            who
        )),
        Answer::Loading => Ok(format!(
            "{} 服务已在 {} 端口运行({}),正在加载模型,未重复启动",
            kind.label(),
            port,
            who
        )),
        Answer::Failed(reason) => Err(format!(
            "{} 服务已在 {} 端口运行({}),但模型加载失败:{}。端口被它占着,再启动一个也起不来——请先停止它,排除原因后再启动",
            kind.label(),
            port,
            who,
            reason
        )),
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

    // 关键的一步:端口上已经有服务在应答就绝不再 spawn——不管它的模型是就绪、
    // 还在加载,还是加载失败了(见 `adopt_existing`)。用户自己在终端跑着的
    // 进程、或者上次会话遗留下来的,都在这里被采纳。
    if let Some(health) = probe_raw(port).await {
        let ours = manager
            .lock()
            .ok()
            .and_then(|mut m| m.snapshot(kind))
            .map(|s| s.alive && s.port == port)
            .unwrap_or(false);
        let external_pid = if ours {
            None
        } else {
            identify_external(kind, &cfg.local)
        };
        return adopt_existing(kind, port, &health.answer(), ours, external_pid);
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

/// 停止一个服务。只停本应用拉起 / 认领的,以及校验过属于本项目的外部进程。
///
/// 停一个进程要先 SIGTERM 再最多干等 `TERM_GRACE`,这几秒**不能占着管理器的锁**:
/// 设置面板每 3 秒拉一次 `get_server_report`,而那条路也要这把锁,占着就等于让
/// 整个服务器面板跟着卡住。所以这里只在「把句柄取出来」和「更新 pid 记账」两个
/// 瞬间加锁,中间的等待在锁外做。
///
/// 代价是这几秒里句柄不在管理器手上,`status` 会把那个正在退出的进程按「外部」
/// 报一下(端口还通着,而手里没句柄)。这是个会自己消失的瞬态:进程一退,端口
/// 就不通了,下一轮轮询报的就是「未运行」。
pub fn stop(
    manager: &Mutex<ServerManager>,
    cfg: &crate::config::ServerConfig,
    kind: ServerKind,
) -> Result<String, String> {
    let taken = manager.lock().map_err(|e| e.to_string())?.take_slot(kind);
    let result = match taken {
        Some(slot) => ServerManager::stop_slot(kind, slot),
        None => ServerManager::stop_external(kind, &cfg.local),
    };
    // 记账文件里那条已经没意义了,清掉——否则下次启动会去认领一个死 pid。
    if let Ok(mut guard) = manager.lock() {
        guard.persist_pids();
    }
    result
}

/// 重启:停再起。
pub async fn restart(
    manager: &Mutex<ServerManager>,
    cfg: &crate::config::ServerConfig,
    kind: ServerKind,
) -> Result<String, String> {
    // 探测放在加锁之前:`Mutex` 的 guard 不是 Send,跨 await 持有会让整个
    // 命令的 Future 不满足 tauri 的 Send 约束(而且会把别的调用者堵死)。
    //
    // 看的是「端口有没有应答」,不是「模型是否就绪」:正在加载、或者加载失败的
    // 外部服务同样占着端口,不先停掉它,后面的 `start` 只会原样采纳它(或者报
    // 「加载失败」)——「重启」就成了什么都没做。
    let answering = probe_raw(port_of(kind, &cfg.local)).await.is_some();
    // 手里有句柄、或者端口上有服务在应答,都得先停掉再拉起。外部进程里认得出
    // 是本项目的那些现在也停得掉;认不出来源的会在这里报错——「重启」在那种
    // 情况下只会变成「又拉起一个」,与其偷偷只做一半,不如直说。
    //
    // 走上面那个 `stop` 而不是自己锁起来做:停进程要等最多 TERM_GRACE,
    // 那几秒不该把状态轮询一起堵死(理由见 `stop`)。
    let has_slot = manager
        .lock()
        .map_err(|e| e.to_string())?
        .slot(kind)
        .is_some();
    if has_slot || answering {
        stop(manager, cfg, kind)?;
    }
    start(manager, cfg, kind).await
}

// ── LLM 后处理开关与 LLM 服务的联动 ──
//
// 「LLM 后处理」这个开关以前只翻 STT 服务端的一个标志位,LLM 服务自己在不在跑
// 和它毫无关系——后处理关着,几个 G 的模型照样占着内存;而 STT 那边标志位开着、
// LLM 端口是空的时,每次转录都要白等一次反代超时。现在把服务的生命周期挂到开关
// 上:开 → 起,关 → 停。
//
// 「关 → 停」这一步必须收窄到**本应用拉起的那个进程**,比停止按钮更严。停止按钮
// 敢动「外部(本项目)」,是因为那是用户瞄着某一个进程按下去的、明确的一下;而拨
// 一下「后处理」开关顺手杀掉他在终端里跑着的服务,是另一回事——终端会话毫无征兆
// 地没了,而他刚才做的事根本不叫「停止服务」。所以这里退回最保守的那条铁律:
// 只停自己拉起的,别的一律保留并把原因说出来。
//
// 下面三个 `plan_*` 都是纯函数,不碰进程也不发请求:判断规则只在这里写一遍,
// 调用方照着执行。这样「什么情况下才停」这条规矩是可以单测的,而不用真去起一个
// 服务才能验证。

/// 关掉 LLM 后处理时,对 LLM 服务的处置。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LlmShutdownPlan {
    /// 本应用拉起(或上次会话认领回来)的进程,跟着开关一起停。
    Stop,
    /// 端口上本来就没有在跑的 LLM 服务,没什么可停的。
    NothingToStop,
    /// 在跑,但不是本应用拉起的。保留,并把原因说给用户听。
    KeepForeign(ServerOwner),
}

impl LlmShutdownPlan {
    /// 保留了别人的进程时,给用户的解释。UI 直接显示,不要让开关看起来「没生效」。
    pub fn keep_reason(self) -> Option<&'static str> {
        match self {
            LlmShutdownPlan::KeepForeign(ServerOwner::ExternalProject) => Some(
                "LLM 服务不是本应用启动的(外部/本项目),已保留——你在终端里跑的进程不该因为拨一下开关就消失。要停它请用「服务器」面板上的「停止」。",
            ),
            LlmShutdownPlan::KeepForeign(_) => Some(
                "LLM 端口上是认不出来源的外部进程,本应用只连接、不会碰它,已保留。",
            ),
            _ => None,
        }
    }
}

/// 关后处理时该不该动 LLM 服务。
pub fn plan_llm_shutdown(status: &ServerStatus) -> LlmShutdownPlan {
    // `Starting` 也算在跑:那是自己刚拉起、还在加载模型的进程,不停掉它就等于
    // 开关关了而内存照占。`Failed` 分两种:进程活着但模型加载失败(`pid` 有值),
    // 它照样占着端口和内存,得停——打开开关时加载失败要回收的正是它;进程已经
    // 退出(`pid` 为空)就没什么可停的。`Stopped` / `NotConfigured` 都没有进程。
    let has_process = match status.state {
        ServerState::Running | ServerState::Starting => true,
        ServerState::Failed => status.pid.is_some(),
        ServerState::Stopped | ServerState::NotConfigured => false,
    };
    if !has_process {
        return LlmShutdownPlan::NothingToStop;
    }
    match status.owner {
        ServerOwner::App => LlmShutdownPlan::Stop,
        other => LlmShutdownPlan::KeepForeign(other),
    }
}

/// 应用启动时按顺序拉起哪些服务。
///
/// LLM 排在 STT 前面是老规矩:STT 要反代到 LLM,让它先就位,转录时的后处理就
/// 不会撞上一个还没起来的端口。`llm_enabled` 为假时干脆不拉 LLM——这正是这次
/// 改动的目的:后处理关着就别占那几个 G 的内存。
pub fn auto_start_plan(llm_enabled: bool) -> Vec<ServerKind> {
    if llm_enabled {
        vec![ServerKind::Llm, ServerKind::Stt]
    } else {
        vec![ServerKind::Stt]
    }
}

/// STT 起来之后,拿服务端的权威标志和启动时用的本地缓存对账的结果。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LlmReconcile {
    /// 缓存和权威一致,什么都不用做。
    InSync,
    /// 权威说后处理开着,启动时却没拉 LLM——补上。
    StartLlm,
    /// 权威说后处理关着,启动时却拉了 LLM——收回去(照样只收自己拉起的)。
    StopLlm,
}

/// 启动时按缓存做的决定(`started_llm`),和 STT 报出来的权威值(`authoritative`)
/// 对一次账。
///
/// 这一步是「两个标志位不会悄悄走散」的全部依靠:启动那一刻 STT 还没起来,
/// `/llm/enabled` 根本问不到,只能先信本地缓存;缓存要是过期了,唯一能发现的
/// 时机就是 STT 健康之后的这一次比对。少了它,两边各说各话而且没有任何地方
/// 会察觉。
pub fn plan_llm_reconcile(started_llm: bool, authoritative: bool) -> LlmReconcile {
    match (started_llm, authoritative) {
        (false, true) => LlmReconcile::StartLlm,
        (true, false) => LlmReconcile::StopLlm,
        _ => LlmReconcile::InSync,
    }
}

/// `wait_ready` 的结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Readiness {
    /// `/health` 报 `ok`,模型能用了。
    Ready,
    /// `/health` 明确说模型加载失败了,带原因。不会自己好起来,不必再等。
    Failed(String),
    /// 等到超时还在加载(或者端口一直没应答)。进程可能还活着,只是没加载完。
    TimedOut,
}

/// 等某个服务的模型就绪。
///
/// 打开后处理时必须等到 `Ready` 才敢去翻 STT 的标志位:`start` 返回只说明
/// spawn 成功,之后还有几秒钟端口是死的,这段时间里 STT 去反代就是撞空。
///
/// 以前只返回「通 / 没通」:模型加载失败(比如非 Apple 平台根本没有 mlx_lm)
/// 和「还在加载」分不开,打开开关要白等满 30 秒,然后说一句「还在加载模型」。
/// 现在 `/health` 一报 `error` 就立刻返回原因。
pub async fn wait_ready(
    cfg: &crate::config::ServerConfig,
    kind: ServerKind,
    timeout: Duration,
) -> Readiness {
    let port = port_of(kind, &cfg.local);
    let deadline = Instant::now() + timeout;
    loop {
        match probe_raw(port).await.as_ref().map(Health::answer) {
            Some(Answer::Ready) => return Readiness::Ready,
            Some(Answer::Failed(reason)) => return Readiness::Failed(reason),
            _ => {}
        }
        if Instant::now() >= deadline {
            return Readiness::TimedOut;
        }
        tokio::time::sleep(Duration::from_millis(300)).await;
    }
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

/// 用户主目录。
///
/// 以前只读 `HOME`:Windows 上通常没有这个变量(那边叫 `USERPROFILE`),于是
/// `~/voice-input-framework` 这些常见位置一个都没去找,自动探测基本必然失败。
pub fn home_dir() -> Option<PathBuf> {
    home_from(
        std::env::var("HOME").ok(),
        std::env::var("USERPROFILE").ok(),
    )
}

/// `home_dir` 的纯逻辑部分:`HOME` 优先,空的不算(Git Bash 之类有时会把它设成空串)。
fn home_from(home: Option<String>, user_profile: Option<String>) -> Option<PathBuf> {
    [home, user_profile]
        .into_iter()
        .flatten()
        .find(|p| !p.trim().is_empty())
        .map(PathBuf::from)
}

/// 首次运行时自动探测仓库位置。
///
/// 应用装在 `/Applications`,仓库在用户目录某处,两者没有固定关系,所以只能
/// 猜常见位置 + 从当前工作目录向上找(开发时从 `gui/src-tauri` 里跑)。
/// 猜不到就返回 `None`,由 UI 明确告诉用户「没探测到,请手动填」。
pub fn detect_repo() -> Option<String> {
    let home = home_dir();
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

    /// 记账文件里的 pid 活着,但已经不是我们的服务了(重启后被复用)——绝不能认领。
    /// 这里拿测试进程自己的 pid 冒充:它活着,命令行里却没有服务模块名。
    #[test]
    fn a_live_but_reused_pid_is_not_reclaimed() {
        let dir = std::env::temp_dir().join(format!("vif-reclaim-{}", std::process::id()));
        let mut m = ServerManager::new(dir.clone());
        let me = std::process::id();
        std::fs::write(
            m.pid_file(),
            format!(
                r#"{{"stt": {{"pid": {me}, "port": 6544}}, "llm": {{"pid": {me}, "port": 6545}}}}"#
            ),
        )
        .unwrap();
        assert!(m.reclaim_orphans().is_empty());
        assert!(m.snapshot(ServerKind::Stt).is_none());
        std::fs::remove_dir_all(&dir).ok();
    }

    /// Windows 上看不到命令行,任何 pid 都不认领(以前是「活着就认领」)。
    #[cfg(not(unix))]
    #[test]
    fn windows_never_reclaims_by_pid_alone() {
        assert!(!pid_runs_module(std::process::id(), ""));
    }

    /// 三档归属里,只有前两档允许从界面动它。第三档是「认不出身份」的兜底,
    /// 这条断言就是那条铁律本身。
    #[test]
    fn only_identified_processes_can_be_managed() {
        assert!(ServerOwner::App.can_manage());
        assert!(ServerOwner::ExternalProject.can_manage());
        assert!(!ServerOwner::ExternalUnknown.can_manage());
    }

    #[test]
    fn owner_serializes_as_snake_case() {
        assert_eq!(
            serde_json::to_string(&ServerOwner::ExternalProject).unwrap(),
            "\"external_project\""
        );
        assert_eq!(
            serde_json::to_string(&ServerOwner::ExternalUnknown).unwrap(),
            "\"external_unknown\""
        );
    }

    /// 身份校验的第一道关卡就该把不存在的 pid 挡掉,不能走到发信号那一步。
    #[test]
    fn bogus_pid_is_not_a_project_server() {
        assert!(!pid_is_project_server(
            0,
            "services.stt_server",
            Path::new("/nonexistent/repo")
        ));
    }

    /// 没配仓库路径 = 没有比对的基准,只能认不出,绝不能退化成「只比模块名」。
    #[test]
    fn without_repo_path_nothing_is_identified() {
        let local = LocalServerConfig::default();
        assert!(identify_external(ServerKind::Stt, &local).is_none());
        let blank = LocalServerConfig {
            repo_path: Some("   ".into()),
            ..Default::default()
        };
        assert!(identify_external(ServerKind::Stt, &blank).is_none());
    }

    /// 路径比对必须先规范化。macOS 上这不是洁癖:临时目录 `/var/folders/...`
    /// 是 `/private/var/folders/...` 的软链,`lsof` 报后者,配置里存的常是前者,
    /// 直接比字符串会把同一个目录判成两个,用户的服务就又变回「认不出」了。
    #[test]
    fn same_dir_sees_through_symlinks_and_dots() {
        let dir = std::env::temp_dir().join(format!("vif-samedir-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("sub")).unwrap();

        // 绕一圈再回来,是同一个目录。
        assert!(same_dir(&dir, &dir.join("sub").join("..")));
        // macOS 上 /var 是 /private/var 的软链,两种写法必须判成同一个。
        if Path::new("/private/var").exists() {
            assert!(same_dir(Path::new("/var"), Path::new("/private/var")));
        }
        assert!(!same_dir(&dir, Path::new("/nonexistent/elsewhere")));

        std::fs::remove_dir_all(&dir).ok();
    }

    /// 造一个 LLM 的状态快照,只关心 `plan_llm_shutdown` 看的那两个字段。
    fn llm_status(state: ServerState, owner: ServerOwner) -> ServerStatus {
        ServerStatus {
            kind: ServerKind::Llm,
            state,
            port: 6545,
            owner,
            can_stop: owner.can_manage(),
            pid: Some(4242),
            current_model: None,
            detail: None,
            log_path: None,
            recent_logs: Vec::new(),
        }
    }

    /// 后处理关着就不该拉 LLM——这条就是这次改动本身。
    #[test]
    fn auto_start_skips_the_llm_server_when_post_processing_is_off() {
        assert_eq!(auto_start_plan(false), vec![ServerKind::Stt]);
    }

    /// 开着的时候顺序不能变:LLM 在前,STT 的反代才有东西可连。
    #[test]
    fn auto_start_keeps_llm_before_stt_when_post_processing_is_on() {
        assert_eq!(
            auto_start_plan(true),
            vec![ServerKind::Llm, ServerKind::Stt]
        );
    }

    /// 自己拉起的,跟着开关一起停。
    #[test]
    fn toggling_off_stops_our_own_llm_server() {
        assert_eq!(
            plan_llm_shutdown(&llm_status(ServerState::Running, ServerOwner::App)),
            LlmShutdownPlan::Stop
        );
        // 还在加载模型的也要停,否则开关关了内存照占。
        assert_eq!(
            plan_llm_shutdown(&llm_status(ServerState::Starting, ServerOwner::App)),
            LlmShutdownPlan::Stop
        );
    }

    /// 这条是整个联动里最要紧的一条闸门:**不是自己拉起的,一个都不停**。
    ///
    /// 注意它比停止按钮严——`ExternalProject` 的 `can_stop` 是 true,用户点
    /// 「停止」能停掉它;但拨一下后处理开关就顺手杀掉他终端里的进程,是完全
    /// 不同的一件事。这里刻意不复用 `can_manage`。
    #[test]
    fn toggling_off_never_stops_a_server_we_did_not_start() {
        for owner in [ServerOwner::ExternalProject, ServerOwner::ExternalUnknown] {
            let status = llm_status(ServerState::Running, owner);
            assert_eq!(
                plan_llm_shutdown(&status),
                LlmShutdownPlan::KeepForeign(owner),
                "{:?} 的进程不能因为拨开关被停掉",
                owner
            );
            // 保留了就必须说清楚为什么,否则开关看起来像是没生效。
            assert!(plan_llm_shutdown(&status).keep_reason().is_some());
        }
        // 「外部(本项目)」明明是可以从界面停的,这里照样不停——两条规则的
        // 严格程度确实不同,这一行就是那个差别本身。
        assert!(ServerOwner::ExternalProject.can_manage());
    }

    /// 没在跑就没什么可停的,也不该报成「保留了别人的进程」。
    #[test]
    fn toggling_off_with_no_llm_running_is_a_no_op() {
        for state in [
            ServerState::Stopped,
            ServerState::Failed,
            ServerState::NotConfigured,
        ] {
            let mut st = llm_status(state, ServerOwner::App);
            // 进程已经退出:状态里没有 pid。
            st.pid = None;
            assert_eq!(
                plan_llm_shutdown(&st),
                LlmShutdownPlan::NothingToStop,
                "{:?}",
                state
            );
        }
    }

    /// R15:自己拉起的进程活着、但模型加载失败了——照样占着内存和端口,要停。
    /// 打开开关时加载失败,回收的就是它;以前 `Failed` 一律当成「没什么可停」。
    #[test]
    fn a_live_llm_server_whose_model_failed_is_still_stopped() {
        let st = llm_status(ServerState::Failed, ServerOwner::App);
        assert_eq!(plan_llm_shutdown(&st), LlmShutdownPlan::Stop);
        // 别人的进程照旧不碰。
        let st = llm_status(ServerState::Failed, ServerOwner::ExternalProject);
        assert_eq!(
            plan_llm_shutdown(&st),
            LlmShutdownPlan::KeepForeign(ServerOwner::ExternalProject)
        );
    }

    /// 缓存和权威对账:一致就什么都不做,不一致一律以权威为准。
    #[test]
    fn reconcile_always_follows_the_authoritative_flag() {
        assert_eq!(plan_llm_reconcile(true, true), LlmReconcile::InSync);
        assert_eq!(plan_llm_reconcile(false, false), LlmReconcile::InSync);
        // 缓存说关、服务端说开:补起 LLM,而不是把服务端改成关。
        assert_eq!(plan_llm_reconcile(false, true), LlmReconcile::StartLlm);
        // 缓存说开、服务端说关:把刚拉起的收回去。
        assert_eq!(plan_llm_reconcile(true, false), LlmReconcile::StopLlm);
    }

    #[test]
    fn a_non_utf8_line_does_not_stop_the_log_pump() {
        // GBK 编码的「加载」后面跟一行正常输出。以前第一行就让读取线程退出了。
        let mut s = LineSplitter::default();
        let mut lines = s.feed(b"\xbc\xd3\xd4\xd8\nnext line\n");
        assert_eq!(lines.len(), 2, "{:?}", lines);
        assert!(
            lines[0].contains('\u{FFFD}'),
            "坏字节应替换成 �: {:?}",
            lines[0]
        );
        assert_eq!(lines.pop().unwrap(), "next line");
    }

    #[test]
    fn carriage_returns_split_progress_bars_into_lines() {
        let mut s = LineSplitter::default();
        // 跨块到达的半行要拼起来;\r\n 不能多切出一个空行。
        assert!(s.feed(b" 10%|#").is_empty());
        assert_eq!(
            s.feed(b"   |\r 50%|#####|\r\n"),
            vec![" 10%|#   |", " 50%|#####|"]
        );
        assert_eq!(s.feed(b"tail without newline"), Vec::<String>::new());
        assert_eq!(s.finish().as_deref(), Some("tail without newline"));
    }

    #[test]
    fn a_line_that_never_ends_is_cut_instead_of_growing_forever() {
        let mut s = LineSplitter::default();
        let lines = s.feed(&vec![b'x'; MAX_LOG_LINE_BYTES * 2 + 10]);
        assert_eq!(lines.len(), 2);
        assert_eq!(s.finish().map(|l| l.len()), Some(10));
    }

    /// 真管道:一行坏字节之后照样能读到后面的内容,而且一直读到 EOF。
    #[test]
    fn pump_keeps_draining_after_invalid_utf8() {
        let dir = std::env::temp_dir().join(format!("vif-pump-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let log_path = dir.join("pump.log");
        File::create(&log_path).unwrap();
        let mut data = b"\xff\xfe broken\n".to_vec();
        for i in 0..2000 {
            data.extend_from_slice(format!("line {}\n", i).as_bytes());
        }
        let logs = Arc::new(Mutex::new(VecDeque::new()));
        pump(
            std::io::Cursor::new(data),
            logs.clone(),
            log_path.clone(),
            ServerKind::Stt,
            "test",
        );
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if logs.lock().unwrap().back().map(String::as_str) == Some("line 1999") {
                break;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(
            logs.lock().unwrap().back().map(String::as_str),
            Some("line 1999")
        );
        let text = std::fs::read_to_string(&log_path).unwrap();
        assert!(text.contains("line 1999"));
        std::fs::remove_dir_all(&dir).ok();
    }

    fn health(status: &str, error: Option<&str>) -> Health {
        Health {
            status: status.into(),
            current_model: Some("m".into()),
            error: error.map(Into::into),
            loading: None,
        }
    }

    #[test]
    fn health_answer_tells_loading_from_failed() {
        assert_eq!(health("ok", None).answer(), Answer::Ready);
        assert_eq!(health("loading", None).answer(), Answer::Loading);
        assert_eq!(
            health("error", Some("OOM")).answer(),
            Answer::Failed("OOM".into())
        );
        // 失败却没给原因,也不能报成空串。
        assert!(
            matches!(health("error", Some(" ")).answer(), Answer::Failed(r) if !r.trim().is_empty())
        );
    }

    /// R14:端口上有应答就不再拉起;加载失败时说清原因,而不是再起一个抢端口的。
    #[test]
    fn start_adopts_any_answering_port() {
        let ok = adopt_existing(ServerKind::Stt, 6544, &Answer::Ready, true, None).unwrap();
        assert!(ok.contains("本应用启动"), "{}", ok);

        let loading =
            adopt_existing(ServerKind::Stt, 6544, &Answer::Loading, false, Some(42)).unwrap();
        assert!(
            loading.contains("正在加载模型") && loading.contains("pid 42"),
            "{}",
            loading
        );
        assert!(loading.contains("未重复启动"), "{}", loading);

        let err = adopt_existing(
            ServerKind::Llm,
            6545,
            &Answer::Failed("No module named 'mlx_lm'".into()),
            false,
            None,
        )
        .unwrap_err();
        assert!(
            err.contains("mlx_lm") && err.contains("外部进程"),
            "{}",
            err
        );
    }

    /// R14:外部服务在加载 → 启动中;加载失败 → 失败并带原因。以前两者都报「未运行」。
    #[test]
    fn an_external_server_that_is_not_ready_is_not_reported_as_stopped() {
        let st = external_pending_status(
            ServerKind::Stt,
            6544,
            Answer::Loading,
            Some(7),
            None,
            "正在加载模型...",
        );
        assert_eq!(st.state, ServerState::Starting);
        assert_eq!(st.owner, ServerOwner::ExternalProject);
        assert!(st.can_stop);
        assert_eq!(st.pid, Some(7));

        let st = external_pending_status(
            ServerKind::Stt,
            6544,
            Answer::Failed("OOM".into()),
            None,
            None,
            "正在加载模型...",
        );
        assert_eq!(st.state, ServerState::Failed);
        assert_eq!(st.owner, ServerOwner::ExternalUnknown);
        assert!(!st.can_stop, "认不出身份的照样不能停");
        assert!(st.detail.unwrap().contains("OOM"));
    }

    /// R19:Windows 上通常只有 USERPROFILE。
    #[test]
    fn home_falls_back_to_userprofile() {
        assert_eq!(
            home_from(None, Some(r"C:\Users\me".into())),
            Some(PathBuf::from(r"C:\Users\me"))
        );
        assert_eq!(
            home_from(Some(String::new()), Some("/u".into())),
            Some(PathBuf::from("/u"))
        );
        assert_eq!(
            home_from(Some("/home/me".into()), Some("/u".into())),
            Some(PathBuf::from("/home/me"))
        );
        assert_eq!(home_from(None, None), None);
    }

    #[test]
    fn tasklist_pid_match_is_exact() {
        let out = "\"python.exe\",\"1234\",\"Console\",\"1\",\"12,345 K\"\r\n";
        assert!(tasklist_lists_pid(out, 1234));
        // 以前的子串匹配会让这几个都算「活着」。
        assert!(!tasklist_lists_pid(out, 12));
        assert!(!tasklist_lists_pid(out, 123));
        assert!(!tasklist_lists_pid(out, 345));
        assert!(!tasklist_lists_pid(
            "INFO: No tasks are running which match the specified criteria.\r\n",
            1
        ));
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
    /// 再在指定端口上应答 `/health`。`FAKE_HEALTH_STATUS` 可以让它一直报
    /// `loading` / `error`,模拟「模型还在加载」「模型加载失败」。
    fn fake_server_py(port_env: &str) -> String {
        format!(
            r#"
import http.server, json, os, sys, time
PORT = int(os.environ["{port_env}"])
STATUS = os.environ.get("FAKE_HEALTH_STATUS", "ok")
ERROR = "fake load failure" if STATUS == "error" else None
print("fake server booting on", PORT, flush=True)
time.sleep(1.5)
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({{"status": STATUS, "current_model": "fake-model", "error": ERROR}}).encode()
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
        /// `tag` 让同一个测试里能同时存在两个「checkout」——认错 checkout 的
        /// 负面用例需要一个和配置里不同的仓库目录。
        fn create(tag: &str) -> Self {
            let root = std::env::temp_dir().join(format!("vif-e2e-{}-{}", std::process::id(), tag));
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
        let repo = FakeRepo::create("adopt");
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
        assert_eq!(st.owner, ServerOwner::App);
        assert!(st.pid.is_some());

        // ── 2. 起来之后是 Running,且带模型名 ──
        assert!(wait_health(TEST_STT_PORT, true, 15).await, "服务没起来");
        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Running);
        assert_eq!(st.owner, ServerOwner::App, "自己拉起的必须标成本应用管理");
        assert!(st.can_stop);
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
        stop(&manager, &cfg, ServerKind::Stt).unwrap();
        assert!(wait_health(TEST_STT_PORT, false, 10).await, "没停干净");
        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Stopped);

        // ── 6. 用户自己在终端里起的本项目服务:采纳,而且**停得掉** ──
        //
        // 这一段就是这次改动要解决的场景本身:同一个仓库目录、同一个模块,
        // 只是不是本应用 spawn 的。以前它会被判成「外部,不可停」。
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
        assert_eq!(
            st.owner,
            ServerOwner::ExternalProject,
            "同仓库同模块的外部进程应该被认出来: {:?}",
            st
        );
        assert!(st.can_stop, "认出来了就该能停");
        assert_eq!(
            st.pid,
            Some(external.id()),
            "认出来的应该正是端口上监听的那个进程"
        );
        assert!(st.detail.unwrap().contains("可以从这里停止"));

        // 真的停掉它——这是以前做不到的那一步。
        let msg = stop(&manager, &cfg, ServerKind::Stt).unwrap();
        assert!(msg.contains("已停止"), "{}", msg);
        assert!(
            wait_health(TEST_STT_PORT, false, 10).await,
            "外部进程没停掉"
        );
        // 收尸,免得留下僵尸进程干扰后面的 pid 判活。
        external.wait().ok();

        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Stopped);
    }

    /// 轮询等待端口「有应答」(不管模型状态)变成期望值。
    async fn wait_answering(port: u16, want: bool, secs: u64) -> bool {
        let deadline = Instant::now() + Duration::from_secs(secs);
        while Instant::now() < deadline {
            if probe_raw(port).await.is_some() == want {
                return true;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        false
    }

    /// R14:用户在终端里起的服务还在加载 / 已经加载失败时点「启动」,不能再拉一个
    /// 进程去抢端口;状态也不能报成「未运行」。
    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7544 端口"]
    async fn an_external_server_still_loading_or_failed_is_adopted_not_duplicated() {
        let repo = FakeRepo::create("pending");
        let cfg = repo.config();
        let manager = Mutex::new(ServerManager::new(repo.data_dir()));
        assert!(probe_raw(TEST_STT_PORT).await.is_none(), "测试端口不干净");

        for (fake_status, want_state) in [
            ("loading", ServerState::Starting),
            ("error", ServerState::Failed),
        ] {
            let mut external = Command::new(system_python())
                .arg("-m")
                .arg("services.stt_server")
                .current_dir(&repo.root)
                .env("VIF_STT_PORT", TEST_STT_PORT.to_string())
                .env("FAKE_HEALTH_STATUS", fake_status)
                .env("PYTHONUNBUFFERED", "1")
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .unwrap();
            assert!(
                wait_answering(TEST_STT_PORT, true, 15).await,
                "外部假服务没起来"
            );

            let st = status(&manager, &cfg, ServerKind::Stt).await;
            assert_eq!(st.state, want_state, "{}: {:?}", fake_status, st);
            assert_eq!(st.owner, ServerOwner::ExternalProject, "{:?}", st);
            assert_eq!(st.pid, Some(external.id()));

            let started = start(&manager, &cfg, ServerKind::Stt).await;
            match fake_status {
                "loading" => {
                    let msg = started.unwrap();
                    assert!(msg.contains("正在加载模型"), "{}", msg);
                }
                _ => {
                    let err = started.unwrap_err();
                    assert!(err.contains("fake load failure"), "{}", err);
                    assert!(st.detail.unwrap().contains("fake load failure"));
                }
            }
            assert!(
                manager.lock().unwrap().snapshot(ServerKind::Stt).is_none(),
                "不该另拉起一个进程"
            );
            assert!(pid_alive(external.id()), "外部进程不该被碰");

            external.kill().ok();
            external.wait().ok();
            assert!(wait_answering(TEST_STT_PORT, false, 10).await);
        }
    }

    /// R15:模型加载失败时 `wait_ready` 立刻带着原因返回,不再白等到超时。
    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7545 端口"]
    async fn wait_ready_returns_the_load_failure_instead_of_timing_out() {
        let repo = FakeRepo::create("llmfail");
        let cfg = repo.config();
        assert!(probe_raw(TEST_LLM_PORT).await.is_none(), "测试端口不干净");

        let mut failing = Command::new(system_python())
            .arg("-m")
            .arg("services.llm_server")
            .current_dir(&repo.root)
            .env("VIF_LLM_PORT", TEST_LLM_PORT.to_string())
            .env("FAKE_HEALTH_STATUS", "error")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();

        let started = Instant::now();
        let r = wait_ready(&cfg, ServerKind::Llm, Duration::from_secs(30)).await;
        assert_eq!(r, Readiness::Failed("fake load failure".into()));
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "不该等到超时: {:?}",
            started.elapsed()
        );

        failing.kill().ok();
        failing.wait().ok();
        assert!(wait_answering(TEST_LLM_PORT, false, 10).await);
    }

    /// 负面用例:端口是健康的,但监听它的**不是**本项目的服务。
    ///
    /// 必须保持不可停。这是放宽「只停自己拉起的」之后,防止误杀无关进程的
    /// 那道闸门——一旦这条断言挂了,说明身份校验松到了危险的程度。
    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7544 端口"]
    async fn a_server_from_another_checkout_stays_untouchable() {
        // 配置指向 `repo`,但端口上跑的是 `other` 里的那份 checkout。
        let repo = FakeRepo::create("mine");
        let other = FakeRepo::create("theirs");
        let cfg = repo.config();
        let manager = Mutex::new(ServerManager::new(repo.data_dir()));

        assert!(probe(TEST_STT_PORT).await.is_none(), "测试端口不干净");

        // 同样的模块名、同样的端口,只是工作目录是另一个仓库。
        let mut stranger = Command::new(system_python())
            .arg("-m")
            .arg("services.stt_server")
            .current_dir(&other.root)
            .env("VIF_STT_PORT", TEST_STT_PORT.to_string())
            .env("PYTHONUNBUFFERED", "1")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        assert!(wait_health(TEST_STT_PORT, true, 15).await, "陌生服务没起来");

        // 先确认这个测试有意义:校验函数确实认得出它属于 `other`,
        // 只是不属于配置里的 `repo`。否则「认不出」可能只是因为整个机制没跑起来。
        let pid = stranger.id();
        assert!(
            pid_is_project_server(pid, ServerKind::Stt.module(), &other.root),
            "对着它自己的仓库应该认得出,否则这个负面用例什么都没证明"
        );
        assert!(
            !pid_is_project_server(pid, ServerKind::Stt.module(), &repo.root),
            "别的 checkout 绝不能被认成本仓库的服务"
        );

        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Running, "端口是通的,状态该是运行中");
        assert_eq!(
            st.owner,
            ServerOwner::ExternalUnknown,
            "别的 checkout 必须留在「未识别」这一档: {:?}",
            st
        );
        assert!(!st.can_stop, "认不出身份就绝不能给出停止能力");
        assert!(st.pid.is_none(), "认不出身份时不该把 pid 报出来");
        assert!(st.detail.unwrap().contains("不能从这里停止"));

        // 停止必须被拒绝,而且进程要毫发无伤。
        let err = stop(&manager, &cfg, ServerKind::Stt).unwrap_err();
        assert!(err.contains("认不出是本项目的服务"), "{}", err);
        assert!(pid_alive(pid), "被拒绝之后陌生进程必须还活着");
        assert!(probe(TEST_STT_PORT).await.is_some(), "它的端口也该还通着");

        // 重启同样不行:停不掉就不能假装重启。
        let err = restart(&manager, &cfg, ServerKind::Stt).await.unwrap_err();
        assert!(err.contains("认不出是本项目的服务"), "{}", err);
        assert!(pid_alive(pid), "重启被拒绝之后陌生进程也必须还活着");

        // 陌生进程由测试自己收拾(生产代码不许碰它,所以只能在这里 kill)。
        stranger.kill().ok();
        stranger.wait().ok();
        assert!(wait_health(TEST_STT_PORT, false, 10).await);
    }

    /// 改端口之后重新启动,不能把老进程丢成孤儿。
    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7544/7546 端口"]
    async fn changing_port_does_not_orphan_the_old_child() {
        const NEW_PORT: u16 = 7546;
        let repo = FakeRepo::create("port");
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

        // 端口对不上:绝不能报成「运行中」,否则用户会以为新端口上那个就是自己
        // 的服务。`owner` 这时说的是**本应用手里那个还在老端口上跑的进程**,
        // 而不是新端口上的任何东西——新端口上此刻根本没有东西,所以 pid 不报。
        let st = status(&manager, &cfg, ServerKind::Stt).await;
        assert_eq!(st.state, ServerState::Failed, "{:?}", st);
        assert!(
            st.pid.is_none(),
            "不能把老进程的 pid 当成新端口上的: {:?}",
            st
        );
        let detail = st.detail.clone().unwrap_or_default();
        assert!(
            detail.contains(&TEST_STT_PORT.to_string()) && detail.contains(&NEW_PORT.to_string()),
            "得说清楚是哪两个端口对不上: {}",
            detail
        );

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
        let repo = FakeRepo::create("shutdown");
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

    /// LLM 后处理开关联动服务生命周期时,只准停自己拉起的那个。
    ///
    /// 用真进程跑一遍,是因为「谁拉起的」这个判断本身就依赖 `lsof` / `ps` 的
    /// 真实结果——纯单测只能验证规则表,验证不了规则喂进去的那个 `owner` 是不是
    /// 算对了。顺带把 `wait_ready` 也压在真实的「起来要花几秒」上。
    #[tokio::test]
    #[ignore = "会真的拉起子进程并绑 7545 端口"]
    async fn the_toggle_only_stops_the_llm_server_it_started_itself() {
        let repo = FakeRepo::create("llmtoggle");
        let cfg = repo.config();
        let manager = Mutex::new(ServerManager::new(repo.data_dir()));

        assert!(probe(TEST_LLM_PORT).await.is_none(), "测试端口不干净");

        // ── 1. 自己拉起的:等它就绪,然后开关一关就该停掉 ──
        start(&manager, &cfg, ServerKind::Llm).await.unwrap();
        assert_eq!(
            wait_ready(&cfg, ServerKind::Llm, Duration::from_secs(15)).await,
            Readiness::Ready,
            "wait_ready 没等到 LLM 服务就绪"
        );

        let st = status(&manager, &cfg, ServerKind::Llm).await;
        assert_eq!(st.owner, ServerOwner::App);
        assert_eq!(plan_llm_shutdown(&st), LlmShutdownPlan::Stop);
        let own_pid = st.pid.unwrap();

        stop(&manager, &cfg, ServerKind::Llm).unwrap();
        assert!(wait_health(TEST_LLM_PORT, false, 10).await, "没停干净");
        assert!(!pid_alive(own_pid));

        // ── 2. 用户自己在终端里起的**同一个仓库**的服务:绝不能因为拨开关被停 ──
        //
        // 这一档(`ExternalProject`)的停止按钮是可以点的,所以它才是真正的
        // 分界线:能停 ≠ 该在拨开关时顺手停。
        let mut external = Command::new(system_python())
            .arg("-m")
            .arg("services.llm_server")
            .current_dir(&repo.root)
            .env("VIF_LLM_PORT", TEST_LLM_PORT.to_string())
            .env("PYTHONUNBUFFERED", "1")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        assert_eq!(
            wait_ready(&cfg, ServerKind::Llm, Duration::from_secs(15)).await,
            Readiness::Ready,
            "外部假服务没起来"
        );

        // 打开后处理时会先 `start`:端口上已经有健康服务,应该采纳而不是再起一个。
        let msg = start(&manager, &cfg, ServerKind::Llm).await.unwrap();
        assert!(msg.contains("外部进程"), "不该重复拉起: {}", msg);

        let st = status(&manager, &cfg, ServerKind::Llm).await;
        assert_eq!(st.owner, ServerOwner::ExternalProject);
        assert!(st.can_stop, "「停止」按钮对这一档是开放的");
        let plan = plan_llm_shutdown(&st);
        assert_eq!(
            plan,
            LlmShutdownPlan::KeepForeign(ServerOwner::ExternalProject),
            "拨开关不能停掉用户自己起的进程: {:?}",
            st
        );
        assert!(plan.keep_reason().is_some(), "保留了就得给出理由");

        // 照着 plan 走一遍(也就是什么都不做),进程必须毫发无伤。
        assert!(pid_alive(external.id()), "外部进程被误杀了");
        assert!(probe(TEST_LLM_PORT).await.is_some(), "它的端口也该还通着");

        // 外部进程由测试自己收拾。
        external.kill().ok();
        external.wait().ok();
        assert!(wait_health(TEST_LLM_PORT, false, 10).await);
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

/// 对着这台机器上**真正跑着**的服务验证一遍身份校验。全程只读:只做 `/health`
/// 探测和 `ps` / `lsof` 查询,绝不发任何信号。
///
/// 这是这次改动最有说服力的一条验证。假服务再像也是假的,而用户在终端里
/// `.venv/bin/python -m services.stt_server` 起的那个才是真正要认的东西——它的
/// 命令行里解释器是**相对路径**,正是这一点决定了身份校验只能锚在 cwd 上,
/// 拿命令行去比仓库路径在真实场景里根本比不出来。
///
/// `#[ignore]`,而且端口上没服务时直接跳过:它依赖这台机器此刻的状态,不该
/// 因为用户没开服务就把 `--ignored` 那一轮判红。
/// 手动执行:`cargo test --lib -- --ignored real_servers --nocapture`
#[cfg(test)]
mod real_servers {
    use super::*;
    use crate::config::{LocalServerConfig, ServerConfig, ServerMode};

    #[tokio::test]
    #[ignore = "依赖本机此刻在 6544/6545 上跑着的真实服务"]
    async fn identifies_the_real_running_servers() {
        let Some(repo) = detect_repo() else {
            eprintln!("跳过:没探测到仓库");
            return;
        };
        let cfg = ServerConfig {
            host: "127.0.0.1".into(),
            port: 6544,
            mode: ServerMode::Local,
            local: LocalServerConfig {
                repo_path: Some(repo.clone()),
                python_path: Some(format!("{}/.venv/bin/python", repo)),
                stt_port: 6544,
                llm_port: 6545,
                ..Default::default()
            },
        };
        let manager = Mutex::new(ServerManager::new(
            std::env::temp_dir().join("vif-real-check"),
        ));

        for kind in [ServerKind::Stt, ServerKind::Llm] {
            if probe(port_of(kind, &cfg.local)).await.is_none() {
                eprintln!(
                    "跳过 {:?}:{} 端口上没有服务",
                    kind,
                    port_of(kind, &cfg.local)
                );
                continue;
            }
            let st = status(&manager, &cfg, kind).await;
            println!(
                "{:?}: state={:?} owner={:?} can_stop={} pid={:?} model={:?}",
                kind, st.state, st.owner, st.can_stop, st.pid, st.current_model
            );
            assert_eq!(st.state, ServerState::Running, "{:?} 没在跑", kind);
            assert_eq!(
                st.owner,
                ServerOwner::ExternalProject,
                "用户自己起的本项目服务必须被认出来: {:?}",
                st
            );
            assert!(st.can_stop, "认出来了就该能停");
            assert!(st.pid.is_some());
        }

        // 换一个仓库路径,同样这两个进程就绝不能再被认走——这是严格性那一半。
        let mut wrong = cfg.clone();
        wrong.local.repo_path = Some("/tmp".into());
        for kind in [ServerKind::Stt, ServerKind::Llm] {
            if probe(port_of(kind, &wrong.local)).await.is_none() {
                continue;
            }
            let st = status(&manager, &wrong, kind).await;
            assert_eq!(
                st.owner,
                ServerOwner::ExternalUnknown,
                "仓库路径对不上就必须认不出: {:?}",
                st
            );
            assert!(!st.can_stop);
        }
    }
}

#[cfg(test)]
mod loading_text_tests {
    use super::*;

    #[test]
    fn download_progress_is_shown_in_mb() {
        let p = LoadingProgress {
            elapsed_s: 42.0,
            downloaded_bytes: 300 * 1024 * 1024,
        };
        assert_eq!(loading_text(Some(&p)), "正在下载模型… 已下载 300 MB(42 秒)");
    }

    #[test]
    fn loading_without_download_shows_elapsed_time() {
        let p = LoadingProgress {
            elapsed_s: 7.4,
            downloaded_bytes: 0,
        };
        assert_eq!(loading_text(Some(&p)), "正在加载模型…(7 秒)");
        // 老服务端没有 loading 字段:保持原来的说法
        assert_eq!(loading_text(None), "正在加载模型...");
    }
}
