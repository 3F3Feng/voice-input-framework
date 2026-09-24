//! 本地服务的版本对账与一键更新。
//!
//! 应用内更新只换桌面客户端;STT / LLM 服务跑的是用户本机仓库
//! (`server.local.repo_path`)里的代码。客户端升级了、仓库没拉新代码、环境没重建,
//! 新功能就对着老服务端悄无声息地失效——2.3.0 的词库、文件转写、中英文提示都是
//! 这样;依赖的下限也跟着涨(新默认 LLM 要 `mlx-lm>=0.31.2`),老环境里根本没有。
//! 以前只能靠 CHANGELOG 里一句「请同时 git pull 并重跑 setup-env」,没人会去看。
//!
//! 这里做两件事:
//!
//! 1. **对账**:两个服务在 `/health` 里报 `app_version`(和客户端同一个版本号,见
//!    `shared/app_version.py`),这里拿它和客户端自己的版本比。老服务端压根没有这个
//!    字段——那恰恰说明它早于这次改动,按「旧」处理。
//! 2. **一键更新**(只在本地模式):在仓库里 `git pull --ff-only`、重跑建环境脚本、
//!    重启本应用管理的服务。这一步动的是用户的仓库和进程,所以先把能想到的「不该
//!    动」的情况全部拦下来(见 [`Blocker`]),拦下时给出原因和手动命令。绝不
//!    `reset` / `stash` / `checkout`,绝不停认不出来源、或不是本应用启动的进程。
//!
//! 判断规则都是纯函数(`relation`、`check_git`、`plan_pull`、`plan_restart`、
//! `setup_args`),单测覆盖;git 那几条还对着测试里现建的临时仓库跑了一遍。

use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::config::{ServerConfig, ServerMode};
use crate::i18n::t;
use crate::server_manager::{
    self, ServerKind, ServerManager, ServerOwner, ServerState, ServerStatus,
};
use crate::tr;

/// 客户端自己的版本,唯一来源是 `gui/src-tauri/Cargo.toml`。
pub const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

/// `git fetch` / `git pull` 的时限。走的是用户的网络和凭据,卡住(比如在等一个
/// 永远不会出现的密码输入)时不能让按钮一直转下去。
const GIT_NET_TIMEOUT: Duration = Duration::from_secs(180);
/// 建环境的时限。首次装 torch / 现编译 llama-cpp-python 要十几分钟,给足。
const SETUP_TIMEOUT: Duration = Duration::from_secs(60 * 60);
/// 失败时附在报错里的输出行数。
const TAIL_LINES: usize = 12;

// ── 版本比较 ──

/// `2.3.10` / `v2.3.10` / `2.3.10-beta.1` / `2.3.10+build` → (数字段, 预发布标记)。
/// 认不出来就是 `None`。
fn parse_version(s: &str) -> Option<(Vec<u64>, Option<String>)> {
    let s = s.trim().trim_start_matches(['v', 'V']);
    // `+` 后面是构建元数据,不参与比较(semver 的规定)。
    let s = s.split('+').next()?;
    let (core, pre) = match s.split_once('-') {
        Some((c, p)) => (c, Some(p.to_string())),
        None => (s, None),
    };
    let parts = core
        .split('.')
        .map(|p| p.parse::<u64>().ok())
        .collect::<Option<Vec<_>>>()?;
    Some((parts, pre))
}

/// 按数字逐段比,不是按字符串比:`2.3.10 > 2.3.9`。段数不同时缺的补 0
/// (`2.3 == 2.3.0`)。数字相同时带预发布标记的更旧(`2.4.0-rc1 < 2.4.0`)。
pub fn compare_versions(a: &str, b: &str) -> Option<Ordering> {
    let (mut pa, pre_a) = parse_version(a)?;
    let (mut pb, pre_b) = parse_version(b)?;
    let n = pa.len().max(pb.len());
    pa.resize(n, 0);
    pb.resize(n, 0);
    Some(pa.cmp(&pb).then_with(|| match (pre_a, pre_b) {
        (None, None) => Ordering::Equal,
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (Some(x), Some(y)) => x.cmp(&y),
    }))
}

/// 服务相对客户端的版本关系。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VersionRelation {
    Same,
    /// 服务比客户端旧:该更新服务了。
    Older,
    /// 服务比客户端新:该更新应用了(不催,只提一句)。
    Newer,
    /// 服务没报版本号(早于报版本号的那一版),或者报的认不出来。按「旧」处理。
    Unknown,
}

impl VersionRelation {
    /// 要不要提示「服务比应用旧」。没报版本号的一律算:能走到这一步的服务
    /// 只可能来自报版本号之前的代码。
    pub fn needs_update(self) -> bool {
        matches!(self, VersionRelation::Older | VersionRelation::Unknown)
    }
}

pub fn relation(client: &str, service: Option<&str>) -> VersionRelation {
    let Some(v) = service.map(str::trim).filter(|s| !s.is_empty()) else {
        return VersionRelation::Unknown;
    };
    match compare_versions(v, client) {
        Some(Ordering::Less) => VersionRelation::Older,
        Some(Ordering::Equal) => VersionRelation::Same,
        Some(Ordering::Greater) => VersionRelation::Newer,
        None => VersionRelation::Unknown,
    }
}

// ── 版本报告 ──

#[derive(Debug, Clone, Serialize)]
pub struct ServiceVersion {
    pub kind: ServerKind,
    /// `/health` 答没答话。没答话的不参与判断(可能只是没启动、或正忙)。
    pub reachable: bool,
    /// 服务报的项目版本号;老服务端没有。
    pub app_version: Option<String>,
    /// 答话了才有。
    pub relation: Option<VersionRelation>,
}

impl ServiceVersion {
    fn from_probe(kind: ServerKind, probe: Result<serde_json::Value, String>) -> Self {
        match probe {
            Ok(v) => {
                let app_version = v["app_version"]
                    .as_str()
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string);
                Self {
                    kind,
                    reachable: true,
                    relation: Some(relation(CLIENT_VERSION, app_version.as_deref())),
                    app_version,
                }
            }
            Err(_) => Self {
                kind,
                reachable: false,
                app_version: None,
                relation: None,
            },
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct VersionReport {
    pub client_version: String,
    pub mode: ServerMode,
    pub services: Vec<ServiceVersion>,
    /// 至少一个答话的服务比客户端旧(或没报版本号)。
    pub services_older: bool,
    /// 没有旧的,但有比客户端新的。
    pub services_newer: bool,
    /// 能不能一键更新(本地模式才有仓库可更新)。按钮点下去还会再做一遍全部检查。
    pub can_update: bool,
    /// 更新正在进行中(比如设置窗口重开了,按钮得接着显示「更新中」)。
    pub update_running: bool,
}

fn summarize(mode: ServerMode, services: Vec<ServiceVersion>) -> VersionReport {
    let older = services
        .iter()
        .any(|s| s.relation.is_some_and(VersionRelation::needs_update));
    let newer = !older
        && services
            .iter()
            .any(|s| s.relation == Some(VersionRelation::Newer));
    VersionReport {
        client_version: CLIENT_VERSION.to_string(),
        mode,
        services,
        services_older: older,
        services_newer: newer,
        can_update: mode == ServerMode::Local,
        update_running: RUNNING.load(AtomicOrdering::SeqCst),
    }
}

/// 问一遍服务的版本。本地模式问两个端口;远程模式只问 STT——LLM 在对端,
/// 客户端连不到它,而两个服务来自同一个 checkout,STT 的版本就够说明问题。
pub async fn version_report(cfg: &ServerConfig) -> VersionReport {
    let stt_url = cfg.effective_stt_url();
    let stt = crate::stt::SttClient::new(&stt_url).get_health().await;
    let mut services = vec![ServiceVersion::from_probe(ServerKind::Stt, stt)];
    if cfg.mode == ServerMode::Local {
        // `SttClient::get_health` 只是 GET `{url}/health`,对 LLM 服务一样适用。
        let llm_url = format!("http://127.0.0.1:{}", cfg.local.llm_port);
        let llm = crate::stt::SttClient::new(&llm_url).get_health().await;
        services.push(ServiceVersion::from_probe(ServerKind::Llm, llm));
    }
    summarize(cfg.mode, services)
}

// ── 更新前的检查 ──

/// 一键更新被拦下的原因。每一条都对应一种「自动去做可能会弄坏用户东西」的情况。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Blocker {
    /// 远程模式:服务在别的机器上,这边没有仓库可更新。
    NotLocal,
    /// 没配仓库路径,或者路径下不是本项目。
    NoRepo,
    /// 仓库目录不是 git 仓库(下载的压缩包),或者它只是某个更大仓库里的子目录。
    NotGitRepo,
    GitMissing,
    /// 建环境脚本要 uv;找不到时脚本会自己从网上装,那一步不该在后台悄悄发生。
    UvMissing,
    /// 解释器不是仓库里的 `.venv`:建环境脚本建的是 `.venv`,服务却用别的解释器跑,
    /// 重建了也等于没重建。
    PythonNotInRepoVenv(String),
    /// 已跟踪的文件有改动(`git status --porcelain` 非空)。前几行原样带上。
    Dirty(Vec<String>),
    /// 合并 / 变基 / 拣选做到一半。
    InProgress(&'static str),
    DetachedHead,
    /// 当前分支没有上游,`git pull` 不知道从哪儿拉。
    NoUpstream(String),
    /// 本地有上游没有的提交,上游也有本地没有的:不是快进。
    Diverged {
        ahead: u32,
        behind: u32,
    },
    FetchFailed(String),
    /// 有在跑的服务不是本应用启动的。
    ForeignService(ServerKind, ServerOwner),
    AlreadyRunning,
}

impl Blocker {
    pub fn message(&self) -> String {
        match self {
            Blocker::NotLocal => t(
                "远程模式下服务跑在别的机器上,这里没法更新。请在那台机器的仓库里拉取新代码、重跑建环境脚本,再重启服务。",
                "In remote mode the services run on another machine, so they can't be updated from here. On that machine, pull the new code in the repository, rerun the setup script, then restart the services.",
            )
            .to_string(),
            Blocker::NoRepo => t(
                "没有配置可用的仓库路径(「服务 → 服务器」里),不知道该更新哪个目录。",
                "No usable repository path is configured (under Service → Server), so there's nothing to update.",
            )
            .to_string(),
            Blocker::NotGitRepo => t(
                "仓库路径不是一个 git 仓库的根目录(可能是下载的压缩包),没法自动拉取新代码。请下载新版代码替换它,或改用 git clone 的仓库。",
                "The repository path isn't the root of a git repository (maybe it was downloaded as an archive), so new code can't be pulled automatically. Replace it with the new release, or use a git clone.",
            )
            .to_string(),
            Blocker::GitMissing => t(
                "没找到可用的 git。请先安装 git(macOS 可运行 xcode-select --install),或在终端里手动更新。",
                "Couldn't find a working git. Install git first (on macOS: xcode-select --install), or update manually in a terminal.",
            )
            .to_string(),
            Blocker::UvMissing => t(
                "没找到 uv(建环境脚本要用它)。脚本会自动安装 uv,但这一步应该由你在终端里看着做——请手动运行下面的命令。",
                "Couldn't find uv, which the setup script needs. The script can install uv itself, but that should happen in a terminal where you can see it, so please run the commands below manually.",
            )
            .to_string(),
            Blocker::PythonNotInRepoVenv(py) => tr!(
                "服务用的解释器 {} 不在仓库的 .venv 里。建环境脚本只会更新仓库里的 .venv,自动更新对你用的这个环境不起作用——请自己更新它的依赖。",
                "The services use the interpreter {}, which isn't the repository's .venv. The setup script only updates the repository's .venv, so an automatic update wouldn't touch your environment; update its dependencies yourself.",
                py
            ),
            Blocker::Dirty(lines) => tr!(
                "仓库里有未提交的改动,自动更新不会碰它们:\n{}\n请先提交或自行处理这些改动,再更新。",
                "The repository has uncommitted changes, and an automatic update won't touch them:\n{}\nCommit or deal with them yourself first, then update.",
                lines.join("\n")
            ),
            Blocker::InProgress(what) => tr!(
                "仓库里有一个做到一半的 {}。请先在终端里把它完成或放弃,再更新。",
                "The repository has an unfinished {}. Finish or abort it in a terminal first, then update.",
                what
            ),
            Blocker::DetachedHead => t(
                "仓库当前不在任何分支上(detached HEAD),不知道该拉哪个分支。请先切回你的分支。",
                "The repository isn't on a branch (detached HEAD), so it's unclear what to pull. Switch back to your branch first.",
            )
            .to_string(),
            Blocker::NoUpstream(branch) => tr!(
                "当前分支 {} 没有设置上游分支,git pull 不知道从哪儿拉。",
                "The current branch {} has no upstream, so git pull doesn't know where to pull from.",
                branch
            ),
            Blocker::Diverged { ahead, behind } => tr!(
                "本地分支和上游分叉了(本地多 {} 个提交,上游多 {} 个),不能快进。自动更新不会合并或变基——请在终端里自己处理。",
                "The local branch and its upstream have diverged ({} local commits, {} upstream), so it can't fast-forward. The automatic update won't merge or rebase; sort it out in a terminal.",
                ahead,
                behind
            ),
            Blocker::FetchFailed(detail) => tr!(
                "从远端拉取失败(没联网、或者需要输入凭据):{}",
                "Fetching from the remote failed (offline, or credentials needed): {}",
                detail
            ),
            Blocker::ForeignService(kind, owner) => match owner {
                ServerOwner::ExternalProject => tr!(
                    "{} 服务是你自己在终端里启动的,不是本应用启动的。更新要重启服务,本应用不会替你停它——请先用「停止」或在终端里停掉它,再更新。",
                    "The {} service was started in your own terminal, not by this app. Updating restarts the services, and this app won't stop yours for you; stop it first (with Stop, or in its terminal), then update.",
                    kind.label()
                ),
                _ => tr!(
                    "{} 端口上是一个认不出来源的进程,本应用不会停它。请先在终端里停掉它,再更新。",
                    "The {} port is held by a process of unknown origin, and this app won't stop it. Stop it in a terminal first, then update.",
                    kind.label()
                ),
            },
            Blocker::AlreadyRunning => t("更新已经在进行中。", "An update is already running.").to_string(),
        }
    }
}

/// 更新前从仓库里查到的 git 状态(`git fetch` 之前就能查的那些)。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct GitFacts {
    /// `git rev-parse --show-toplevel` 就是配置的仓库路径。
    pub is_repo_root: bool,
    /// 当前分支;detached HEAD 时为空。
    pub branch: Option<String>,
    /// 上游分支(`@{u}`),没有时为空。
    pub upstream: Option<String>,
    /// `git status --porcelain --untracked-files=no` 的输出行。
    pub dirty: Vec<String>,
    pub in_progress: Option<&'static str>,
}

/// 未跟踪的文件不拦:`git pull` 碰到会被覆盖的未跟踪文件时自己会拒绝、什么都不改,
/// 其它未跟踪文件(草稿、自己下的模型、编辑器的目录)和拉取毫不相干——拿它们拦住
/// 更新,几乎每个用户都会被拦。已跟踪文件的改动才可能被拉取撞上或搞混,必须拦。
pub fn check_git(facts: &GitFacts) -> Result<(), Blocker> {
    if !facts.is_repo_root {
        return Err(Blocker::NotGitRepo);
    }
    if let Some(what) = facts.in_progress {
        return Err(Blocker::InProgress(what));
    }
    if !facts.dirty.is_empty() {
        const SHOWN: usize = 8;
        let mut lines: Vec<String> = facts.dirty.iter().take(SHOWN).cloned().collect();
        if facts.dirty.len() > SHOWN {
            lines.push(tr!(
                "……还有 {} 个",
                "… and {} more",
                facts.dirty.len() - SHOWN
            ));
        }
        return Err(Blocker::Dirty(lines));
    }
    let Some(branch) = facts.branch.clone() else {
        return Err(Blocker::DetachedHead);
    };
    if facts.upstream.is_none() {
        return Err(Blocker::NoUpstream(branch));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PullPlan {
    /// 上游没有新提交(本地可能领先,那是开发者自己的提交,不碰)。
    UpToDate,
    /// 可以快进,上游多这么多个提交。
    FastForward(u32),
}

/// `git rev-list --left-right --count HEAD...@{u}` 的结果(本地领先, 本地落后)。
pub fn plan_pull(ahead: u32, behind: u32) -> Result<PullPlan, Blocker> {
    match (ahead, behind) {
        (_, 0) => Ok(PullPlan::UpToDate),
        (0, n) => Ok(PullPlan::FastForward(n)),
        (a, b) => Err(Blocker::Diverged {
            ahead: a,
            behind: b,
        }),
    }
}

/// 更新完要重启哪些服务(顺序:LLM 在前,和自动启动一致,STT 的反代才有东西可连)。
///
/// 规则比「停止」按钮更严,和拨 LLM 开关时一样(见 `plan_llm_shutdown`):只有本应用
/// 启动的才重启。「外部(本项目)」虽然能从界面上手动停,可那是用户瞄着那一个进程按下
/// 去的;点「更新服务」顺手把他终端里跑着的服务杀掉,是另一回事。认不出来源的更不用说。
/// 有这样的服务在跑就整个拒绝,而不是跳过它——跳过的话更新完它还跑着旧代码,
/// 提示消不掉,用户也不知道为什么。
pub fn plan_restart(statuses: &[&ServerStatus]) -> Result<Vec<ServerKind>, Blocker> {
    let mut kinds = Vec::new();
    for kind in [ServerKind::Llm, ServerKind::Stt] {
        let Some(st) = statuses.iter().find(|s| s.kind == kind) else {
            continue;
        };
        let has_process = match st.state {
            ServerState::Running | ServerState::Starting => true,
            ServerState::Failed => st.pid.is_some(),
            ServerState::Stopped | ServerState::NotConfigured => false,
        };
        if !has_process {
            continue;
        }
        if st.owner != ServerOwner::App {
            return Err(Blocker::ForeignService(kind, st.owner));
        }
        kinds.push(kind);
    }
    Ok(kinds)
}

// ── 建环境脚本的参数 ──

/// 现有环境里装了什么。建环境脚本最后是 `uv sync`,它会**删掉**这次没要的包:
/// 当初加了 `--llm` 装的 llama.cpp、`--dev` 装的 pytest,这次不带同样的参数就没了。
/// 所以要先看一眼现有环境,把当初的参数还原出来。
#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(default)]
pub struct EnvFacts {
    pub apple_silicon: bool,
    /// torch 的版本号,带本地版本标记(`2.5.1+cu124`)。
    pub torch: Option<String>,
    pub llama_cpp: bool,
    pub dev: bool,
}

/// 只查包的元数据,不 import:import torch 要好几秒,这里只要版本号。
const ENV_PROBE_SCRIPT: &str = r#"
import json, platform, sys
from importlib import metadata
def v(name):
    try:
        return metadata.version(name)
    except Exception:
        return None
out = {"apple_silicon": sys.platform == "darwin" and platform.machine() == "arm64",
       "torch": v("torch"), "llama_cpp": v("llama-cpp-python") is not None,
       "dev": v("pytest") is not None}
sys.stdout.write("\nVIF_SETUP_JSON:" + json.dumps(out) + "\n")
"#;

/// torch 本地版本标记 → 建环境脚本的后端名。没有标记(PyPI 上的默认包)时
/// 返回 `None`,让脚本自己探测——当初多半也是探测出来的。
fn torch_backend(version: &str) -> Option<&'static str> {
    let local = version.split_once('+')?.1.to_ascii_lowercase();
    if local.starts_with("cu") {
        Some("cuda")
    } else if local.starts_with("rocm") {
        Some("rocm")
    } else if local.starts_with("xpu") {
        Some("xpu")
    } else if local.starts_with("cpu") {
        Some("cpu")
    } else {
        None
    }
}

/// 建环境脚本的参数。`windows` 时用 PowerShell 版的写法(`-Backend` / `-Llm` / `-Dev`)。
pub fn setup_args(env: &EnvFacts, windows: bool) -> Vec<String> {
    let mut args = Vec::new();
    // Apple Silicon 走 MLX,不挑 torch 索引,也不需要 llama.cpp(脚本在 mlx 上忽略 --llm)。
    let backend = if env.apple_silicon {
        None
    } else {
        env.torch.as_deref().and_then(torch_backend)
    };
    if windows {
        // PowerShell 版只认 cpu / cuda / xpu(ROCm 的 PyTorch 只有 Linux 版)。
        if let Some(b) = backend.filter(|b| matches!(*b, "cpu" | "cuda" | "xpu")) {
            args.push("-Backend".into());
            args.push(b.into());
        }
        if env.llama_cpp && !env.apple_silicon {
            args.push("-Llm".into());
        }
        if env.dev {
            args.push("-Dev".into());
        }
    } else {
        if let Some(b) = backend {
            args.push("--backend".into());
            args.push(b.into());
        }
        if env.llama_cpp && !env.apple_silicon {
            args.push("--llm".into());
        }
        if env.dev {
            args.push("--dev".into());
        }
    }
    args
}

/// 拦下时附上的手动命令。
pub fn manual_commands(repo: &str, args: &[String], windows: bool) -> String {
    let extra = if args.is_empty() {
        String::new()
    } else {
        format!(" {}", args.join(" "))
    };
    let setup = if windows {
        format!("powershell -ExecutionPolicy Bypass -File scripts\\setup-env.ps1{extra}")
    } else {
        format!("scripts/setup-env.sh{extra}")
    };
    format!("cd \"{repo}\"\ngit pull --ff-only\n{setup}")
}

fn with_manual(reason: String, manual: &str) -> String {
    tr!(
        "{}\n\n手动更新:在终端里运行\n{}\n然后回到这里重启 STT / LLM 服务。",
        "{}\n\nTo update manually, run this in a terminal:\n{}\nthen restart the STT / LLM services here.",
        reason,
        manual
    )
}

// ── 找工具 ──

/// 找 git。和找 uv 一样(见 `env_check::find_uv`):从 Finder 打开的应用 PATH 只有
/// `/usr/bin:/bin:/usr/sbin:/sbin`,Homebrew 装的 git 不在里面。
pub fn find_git(
    path_var: Option<&OsStr>,
    windows: bool,
    exists: impl Fn(&Path) -> bool,
) -> Option<PathBuf> {
    let exe = if windows { "git.exe" } else { "git" };
    let mut dirs: Vec<PathBuf> = path_var
        .map(|p| std::env::split_paths(p).collect())
        .unwrap_or_default();
    if windows {
        dirs.push(r"C:\Program Files\Git\cmd".into());
    } else {
        dirs.push("/opt/homebrew/bin".into());
        dirs.push("/usr/local/bin".into());
        dirs.push("/usr/bin".into());
    }
    dirs.into_iter().map(|d| d.join(exe)).find(|p| exists(p))
}

/// 找到并确认 git 能用。
///
/// macOS 的 `/usr/bin/git` 是个转发壳:没装命令行工具时一运行就弹「安装开发者工具」
/// 的系统对话框。先用 `xcode-select -p` 问一句装没装,没装就当没有 git,不去碰那个壳。
fn locate_git() -> Option<PathBuf> {
    let git = find_git(std::env::var_os("PATH").as_deref(), cfg!(windows), |p| {
        p.is_file()
    })?;
    if cfg!(target_os = "macos") && git == Path::new("/usr/bin/git") {
        let clt = std::process::Command::new("xcode-select")
            .arg("-p")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|s| s.success());
        if !clt {
            return None;
        }
    }
    let mut cmd = std::process::Command::new(&git);
    cmd.arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    server_manager::no_console(&mut cmd);
    cmd.status().is_ok_and(|s| s.success()).then_some(git)
}

/// 子进程的 PATH:uv / git 所在目录放最前,再接上原来的,最后补上系统目录。
fn augmented_path(front: &[&Path]) -> Option<std::ffi::OsString> {
    let mut dirs: Vec<PathBuf> = front.iter().map(|p| p.to_path_buf()).collect();
    if let Some(p) = std::env::var_os("PATH") {
        dirs.extend(std::env::split_paths(&p));
    }
    if !cfg!(windows) {
        for d in ["/usr/bin", "/bin", "/usr/sbin", "/sbin"] {
            dirs.push(d.into());
        }
    }
    std::env::join_paths(dirs).ok()
}

// ── git ──

/// 一条 git 命令。不许它问任何问题:后台没有终端可以回答,问了就是一直卡着。
fn git_std(git: &Path, repo: &Path) -> std::process::Command {
    let mut cmd = std::process::Command::new(git);
    cmd.arg("-C")
        .arg(repo)
        .stdin(Stdio::null())
        .env("GIT_TERMINAL_PROMPT", "0")
        // Windows 的 Git Credential Manager 默认会弹登录窗口。
        .env("GCM_INTERACTIVE", "never");
    if std::env::var_os("GIT_SSH_COMMAND").is_none() {
        // ssh 远端:要密码 / 口令时直接失败,而不是等一个永远没人敲的输入。
        cmd.env("GIT_SSH_COMMAND", "ssh -o BatchMode=yes");
    }
    server_manager::no_console(&mut cmd);
    cmd
}

/// 跑一条很快的 git 查询,成功时返回 stdout(去掉首尾空白)。
fn git_query(git: &Path, repo: &Path, args: &[&str]) -> Option<String> {
    let out = git_std(git, repo).args(args).output().ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// 查仓库状态。只读:这里的命令一条都不改仓库。
pub fn gather_git_facts(git: &Path, repo: &Path) -> GitFacts {
    let is_repo_root = git_query(git, repo, &["rev-parse", "--show-toplevel"])
        .is_some_and(|top| server_manager::same_dir(Path::new(&top), repo));
    if !is_repo_root {
        return GitFacts::default();
    }
    let in_progress = [
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
    ]
    .into_iter()
    .find(|(name, _)| {
        git_query(git, repo, &["rev-parse", "--git-path", name])
            .is_some_and(|p| repo.join(p).exists())
    })
    .map(|(_, what)| what);
    let dirty = git_query(
        git,
        repo,
        &["status", "--porcelain", "--untracked-files=no"],
    )
    .map(|s| {
        s.lines()
            .filter(|l| !l.trim().is_empty())
            .map(str::to_string)
            .collect()
    })
    // 连状态都查不出来就别当成干净的。
    .unwrap_or_else(|| vec![t("(git status 失败)", "(git status failed)").to_string()]);
    GitFacts {
        is_repo_root,
        branch: git_query(git, repo, &["symbolic-ref", "--short", "-q", "HEAD"])
            .filter(|b| !b.is_empty()),
        upstream: git_query(
            git,
            repo,
            &["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        )
        .filter(|u| !u.is_empty()),
        dirty,
        in_progress,
    }
}

/// (本地领先, 本地落后) 上游多少个提交。要在 `git fetch` 之后问才准。
pub fn ahead_behind(git: &Path, repo: &Path) -> Option<(u32, u32)> {
    let out = git_query(
        git,
        repo,
        &["rev-list", "--left-right", "--count", "HEAD...@{u}"],
    )?;
    let mut it = out.split_whitespace().map(|n| n.parse::<u32>().ok());
    Some((it.next()??, it.next()??))
}

// ── 检查服务代码有没有更新 ──

/// 本机服务的代码和上游比,落后多少。
///
/// 版本号只在发版时才变,可两次发版之间 main 上照样会合进服务端的修复(比如建环境脚本、
/// 默认提示词)。只比版本号的话,版本一样时界面上连「更新服务」都看不到,用户没法主动
/// 检查。这里直接问 git:fetch 一下,看本地分支落后上游几个提交。
#[derive(Debug, Clone, Default, Serialize)]
pub struct CodeStatus {
    /// fetch 成功、比较出了结果。
    pub checked: bool,
    pub branch: Option<String>,
    pub upstream: Option<String>,
    /// 本地当前提交(短哈希)。
    pub head: Option<String>,
    pub ahead: u32,
    pub behind: u32,
    /// 上游比本地多出来的提交(最多 5 条,「短哈希 标题」)。
    pub new_commits: Vec<String>,
    /// 没法检查的原因(不是本地模式、没有仓库、没有 git、fetch 失败……)。
    pub error: Option<String>,
}

/// 只读:`git fetch` 只更新远端跟踪分支,不动工作区和本地分支。
pub async fn check_code(cfg: &ServerConfig) -> CodeStatus {
    let fail = |e: String| CodeStatus {
        error: Some(e),
        ..Default::default()
    };
    if cfg.mode != ServerMode::Local {
        return fail(Blocker::NotLocal.message());
    }
    let Some(repo) = cfg
        .local
        .repo_path
        .as_deref()
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
    else {
        return fail(Blocker::NoRepo.message());
    };
    if !server_manager::is_repo_root(&repo) {
        return fail(Blocker::NoRepo.message());
    }
    let Some(git) = locate_git() else {
        return fail(Blocker::GitMissing.message());
    };
    let facts = {
        let (git, repo) = (git.clone(), repo.clone());
        tokio::task::spawn_blocking(move || gather_git_facts(&git, &repo))
            .await
            .unwrap_or_default()
    };
    let mut status = CodeStatus {
        branch: facts.branch.clone(),
        upstream: facts.upstream.clone(),
        ..Default::default()
    };
    if facts.upstream.is_none() {
        status.error = Some(if facts.branch.is_none() {
            Blocker::DetachedHead.message()
        } else {
            Blocker::NoUpstream(facts.branch.clone().unwrap_or_default()).message()
        });
        return status;
    }
    let mut fetch = git_std(&git, &repo);
    fetch.arg("fetch");
    if let Err(e) = run_streamed(fetch, GIT_NET_TIMEOUT, &mut |_| {}).await {
        status.error = Some(Blocker::FetchFailed(e).message());
        return status;
    }
    let (git2, repo2) = (git.clone(), repo.clone());
    let (head, counts, log) = tokio::task::spawn_blocking(move || {
        (
            git_query(&git2, &repo2, &["rev-parse", "--short", "HEAD"]),
            ahead_behind(&git2, &repo2),
            git_query(
                &git2,
                &repo2,
                &[
                    "log",
                    "--no-merges",
                    "--format=%h %s",
                    "-n",
                    "5",
                    "HEAD..@{u}",
                ],
            ),
        )
    })
    .await
    .unwrap_or((None, None, None));
    status.head = head;
    match counts {
        Some((ahead, behind)) => {
            status.checked = true;
            status.ahead = ahead;
            status.behind = behind;
            status.new_commits = log
                .unwrap_or_default()
                .lines()
                .filter(|l| !l.trim().is_empty())
                .map(str::to_string)
                .collect();
        }
        None => {
            status.error =
                Some(t("比较本地和上游失败", "Couldn't compare local and upstream").into())
        }
    }
    status
}

// ── 带实时输出地跑一条命令 ──

/// 去掉 ANSI 颜色码:建环境脚本给 `==>` 上了色,原样进日志框就是一串 `[1;36m`。
fn strip_ansi(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' {
            if chars.peek() == Some(&'[') {
                chars.next();
                // CSI:参数字节之后以 0x40–0x7E 结尾。
                for c in chars.by_ref() {
                    if ('@'..='~').contains(&c) {
                        break;
                    }
                }
            }
            continue;
        }
        out.push(c);
    }
    out
}

fn pump_lines<R>(mut stream: R, tx: tokio::sync::mpsc::UnboundedSender<String>)
where
    R: tokio::io::AsyncRead + Unpin + Send + 'static,
{
    use tokio::io::AsyncReadExt;
    tokio::spawn(async move {
        let mut buf = [0u8; 4096];
        let mut splitter = server_manager::LineSplitter::default();
        loop {
            match stream.read(&mut buf).await {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    for line in splitter.feed(&buf[..n]) {
                        let _ = tx.send(line);
                    }
                }
            }
        }
        if let Some(line) = splitter.finish() {
            let _ = tx.send(line);
        }
    });
}

/// 跑一条命令,每一行输出交给 `on_line`。失败时的 `Err` 里带退出码和最后几行输出。
///
/// stdout / stderr 都要读:uv、git 的进度和报错都在 stderr 上;不读的话管道写满,
/// 子进程就卡在 write 上(和服务日志那边是同一个坑,见 `server_manager::pump`)。
async fn run_streamed(
    std_cmd: std::process::Command,
    timeout: Duration,
    on_line: &mut (dyn FnMut(&str) + Send),
) -> Result<(), String> {
    let mut cmd = tokio::process::Command::from(std_cmd);
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // 超时后 future 被丢掉,子进程跟着被杀。
        .kill_on_drop(true);
    let mut child = cmd
        .spawn()
        .map_err(|e| tr!("启动失败:{}", "Failed to start: {}", e))?;
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    if let Some(out) = child.stdout.take() {
        pump_lines(out, tx.clone());
    }
    if let Some(err) = child.stderr.take() {
        pump_lines(err, tx.clone());
    }
    drop(tx);

    let mut tail: std::collections::VecDeque<String> = std::collections::VecDeque::new();
    let work = async {
        while let Some(line) = rx.recv().await {
            let line = strip_ansi(&line);
            if line.trim().is_empty() {
                continue;
            }
            on_line(&line);
            if tail.len() == TAIL_LINES {
                tail.pop_front();
            }
            tail.push_back(line);
        }
        child.wait().await
    };
    let outcome = tokio::time::timeout(timeout, work).await;
    let tail_text = tail.iter().cloned().collect::<Vec<_>>().join("\n");
    match outcome {
        Err(_) => {
            let _ = child.start_kill();
            Err(tr!(
                "超过 {} 秒没有完成,已中止。\n{}",
                "Didn't finish within {} seconds and was stopped.\n{}",
                timeout.as_secs(),
                tail_text
            ))
        }
        Ok(Err(e)) => Err(e.to_string()),
        Ok(Ok(status)) if status.success() => Ok(()),
        Ok(Ok(status)) => Err(tr!(
            "退出码 {}\n{}",
            "Exit code {}\n{}",
            status
                .code()
                .map(|c| c.to_string())
                .unwrap_or_else(|| "?".into()),
            tail_text
        )),
    }
}

// ── 整个流程 ──

/// 同一时间只允许一次更新:两次 `git pull` / `uv sync` 并发跑,结果谁也说不清。
static RUNNING: AtomicBool = AtomicBool::new(false);

struct RunningGuard;
impl Drop for RunningGuard {
    fn drop(&mut self) {
        RUNNING.store(false, AtomicOrdering::SeqCst);
    }
}

/// 推给前端的进度(`service-update` 事件)。
#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    /// checking / fetching / pulling / setup / restarting / done / failed
    pub stage: &'static str,
    /// 这一阶段最新的一行输出,或者给用户看的一句话。
    pub line: Option<String>,
}

/// 报进度的回调。流程本身不碰 tauri,测试里换成收集到 Vec 里。
type Emit<'a> = &'a (dyn Fn(&'static str, Option<String>) + Send + Sync);

/// 解释器是不是 `<repo>/.venv` 里的那个。只比较所在目录:`.venv/bin/python`
/// 本身是指向 uv 管理的解释器的软链,规范化文件本身会跑到仓库外面去。
fn python_in_repo_venv(python: &Path, repo: &Path) -> bool {
    let (Some(dir), Ok(venv)) = (python.parent(), std::fs::canonicalize(repo.join(".venv"))) else {
        return false;
    };
    std::fs::canonicalize(dir).is_ok_and(|d| d.starts_with(&venv))
}

async fn probe_env(python: &str, repo: &Path) -> Option<EnvFacts> {
    let mut cmd = std::process::Command::new(python);
    cmd.arg("-c")
        .arg(ENV_PROBE_SCRIPT)
        .current_dir(repo)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    server_manager::no_console(&mut cmd);
    let mut cmd = tokio::process::Command::from(cmd);
    cmd.kill_on_drop(true);
    let out = tokio::time::timeout(Duration::from_secs(20), cmd.output())
        .await
        .ok()?
        .ok()?;
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .rev()
        .find_map(|l| l.trim().strip_prefix("VIF_SETUP_JSON:"))
        .and_then(|j| serde_json::from_str(j).ok())
}

fn setup_command(
    repo: &Path,
    args: &[String],
    path: Option<&std::ffi::OsString>,
) -> std::process::Command {
    let mut cmd = if cfg!(windows) {
        let mut c = std::process::Command::new("powershell");
        c.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
            .arg(repo.join("scripts").join("setup-env.ps1"));
        c
    } else {
        // 用 bash 显式跑:从压缩包解出来、或者在 Windows 上 checkout 过的仓库,
        // 脚本可能没有可执行位。
        let mut c = std::process::Command::new("bash");
        c.arg(repo.join("scripts").join("setup-env.sh"));
        c
    };
    cmd.args(args)
        .current_dir(repo)
        // 应用若是从激活了别的虚拟环境的终端里启动的,uv 会对着它发警告。
        .env_remove("VIRTUAL_ENV")
        // 进度条在日志框里就是几百行重复的 `\r`,关掉。
        .env("UV_NO_PROGRESS", "1")
        .env("NO_COLOR", "1")
        .env("PYTHONIOENCODING", "utf-8");
    if let Some(p) = path {
        cmd.env("PATH", p);
    }
    server_manager::no_console(&mut cmd);
    cmd
}

/// 一键更新本地服务。成功时返回给用户看的总结;失败 / 被拦下时返回原因(带手动命令)。
pub async fn update_services(
    app: tauri::AppHandle,
    servers: Arc<Mutex<ServerManager>>,
    cfg: ServerConfig,
) -> Result<String, String> {
    if RUNNING.swap(true, AtomicOrdering::SeqCst) {
        return Err(Blocker::AlreadyRunning.message());
    }
    let _guard = RunningGuard;
    let emit = move |stage: &'static str, line: Option<String>| {
        use tauri::Emitter;
        let _ = app.emit("service-update", Progress { stage, line });
    };
    let result = run_update(&emit, &servers, &cfg).await;
    match &result {
        Ok(msg) => {
            crate::log_info!("[服务更新] 完成:{}", msg);
            emit("done", Some(msg.clone()));
        }
        Err(e) => {
            crate::log_error!("[服务更新] 没有完成:{}", e);
            emit("failed", Some(e.clone()));
        }
    }
    result
}

async fn run_update(
    emit: Emit<'_>,
    servers: &Arc<Mutex<ServerManager>>,
    cfg: &ServerConfig,
) -> Result<String, String> {
    let windows = cfg!(windows);
    emit("checking", None);
    if cfg.mode != ServerMode::Local {
        return Err(Blocker::NotLocal.message());
    }
    let repo_str = cfg
        .local
        .repo_path
        .as_deref()
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .ok_or_else(|| Blocker::NoRepo.message())?
        .to_string();
    let repo = PathBuf::from(&repo_str);
    if !server_manager::is_repo_root(&repo) {
        return Err(Blocker::NoRepo.message());
    }
    let python = cfg
        .local
        .python_path
        .as_deref()
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .unwrap_or_default()
        .to_string();

    // 先看一眼现有环境,把当初建环境用的参数还原出来——被拦下时给的手动命令也要带上。
    let env = probe_env(&python, &repo).await;
    if env.is_none() {
        crate::log_warn!("[服务更新] 读不出现有环境装了哪些包,建环境脚本按自动探测跑");
    }
    let args = setup_args(&env.unwrap_or_default(), windows);
    let manual = manual_commands(&repo_str, &args, windows);
    let refuse = |b: Blocker| with_manual(b.message(), &manual);

    if !python_in_repo_venv(Path::new(&python), &repo) {
        return Err(refuse(Blocker::PythonNotInRepoVenv(python.clone())));
    }
    let git = locate_git().ok_or_else(|| refuse(Blocker::GitMissing))?;
    let home = server_manager::home_dir();
    let uv = crate::env_check::find_uv(
        std::env::var_os("PATH").as_deref(),
        home.as_deref(),
        windows,
        |p| p.is_file(),
    )
    .ok_or_else(|| refuse(Blocker::UvMissing))?;

    let facts = gather_git_facts(&git, &repo);
    check_git(&facts).map_err(refuse)?;

    // 服务归属在动仓库之前就查:要是有别人的服务在跑,一个文件都不该改。
    let report = server_manager::report(servers, cfg).await;
    let to_restart = plan_restart(&[&report.stt, &report.llm]).map_err(refuse)?;

    let branch = facts.branch.clone().unwrap_or_default();
    let upstream = facts.upstream.clone().unwrap_or_default();
    let mut log_line = |line: &str| {
        crate::log_info!("[服务更新] {}", line);
        emit("progress", Some(line.to_string()));
    };

    emit(
        "fetching",
        Some(tr!(
            "git fetch({} ← {})",
            "git fetch ({} ← {})",
            branch,
            upstream
        )),
    );
    crate::log_info!(
        "[服务更新] 仓库 {},分支 {} ← {}",
        repo_str,
        branch,
        upstream
    );
    // fetch 只更新远端跟踪分支,不动工作区和本地分支。
    let mut fetch = git_std(&git, &repo);
    fetch.arg("fetch");
    run_streamed(fetch, GIT_NET_TIMEOUT, &mut log_line)
        .await
        .map_err(|e| refuse(Blocker::FetchFailed(e)))?;

    let (ahead, behind) = ahead_behind(&git, &repo).ok_or_else(|| {
        refuse(Blocker::FetchFailed(
            t("比较本地和上游失败", "Couldn't compare local and upstream").into(),
        ))
    })?;
    let before = git_query(&git, &repo, &["rev-parse", "--short", "HEAD"]).unwrap_or_default();
    let code_note = match plan_pull(ahead, behind).map_err(refuse)? {
        PullPlan::UpToDate => {
            crate::log_info!("[服务更新] 代码已是最新({}),只重建环境", before);
            tr!("代码已是最新({})", "Code already up to date ({})", before)
        }
        PullPlan::FastForward(n) => {
            emit(
                "pulling",
                Some(tr!("拉取 {} 个新提交", "Pulling {} new commits", n)),
            );
            let mut pull = git_std(&git, &repo);
            // `--no-rebase`:用户的 git 配置里可能设了 pull.rebase=true,
            // 这里只要快进,任何形式的改写历史都不做。
            pull.args(["pull", "--ff-only", "--no-rebase"]);
            if let Err(e) = run_streamed(pull, GIT_NET_TIMEOUT, &mut log_line).await {
                return Err(with_manual(
                    tr!(
                        "git pull 没有成功,服务没有动过。git 的输出:\n{}",
                        "git pull didn't succeed; the services weren't touched. git said:\n{}",
                        e
                    ),
                    &manual,
                ));
            }
            let after =
                git_query(&git, &repo, &["rev-parse", "--short", "HEAD"]).unwrap_or_default();
            tr!(
                "代码 {} → {}({} 个新提交)",
                "Code {} → {} ({} new commits)",
                before,
                after,
                n
            )
        }
    };

    // Windows 上正在运行的 Python 进程锁着它加载的 .pyd / .dll,uv 换不掉这些文件,
    // 只能先停服务再建环境;其它平台换文件不受影响,建环境期间服务照常可用。
    let stop_first = windows;
    if stop_first {
        for kind in &to_restart {
            if let Err(e) = server_manager::stop(servers, cfg, *kind) {
                crate::log_error!("[服务更新] 停止 {} 失败:{}", kind.label(), e);
            }
        }
    }

    let setup_cmd_text = if windows {
        format!("setup-env.ps1 {}", args.join(" "))
    } else {
        format!("setup-env.sh {}", args.join(" "))
    };
    emit("setup", Some(setup_cmd_text.trim().to_string()));
    crate::log_info!("[服务更新] 重建环境:{}", setup_cmd_text.trim());
    let git_dir = git.parent().map(Path::to_path_buf);
    let uv_dir = uv.parent().map(Path::to_path_buf);
    let front: Vec<&Path> = [uv_dir.as_deref(), git_dir.as_deref()]
        .into_iter()
        .flatten()
        .collect();
    let path = augmented_path(&front);
    let setup = run_streamed(
        setup_command(&repo, &args, path.as_ref()),
        SETUP_TIMEOUT,
        &mut log_line,
    )
    .await;

    if let Err(e) = setup {
        // 失败了也要把服务恢复成原来的样子:Windows 上刚才为了建环境停掉的,
        // 在这里拉起来;其它平台根本没停过。
        let restored = if stop_first {
            restart_all(servers, cfg, &to_restart, false).await
        } else {
            Vec::new()
        };
        let mut msg = tr!(
            "{}。建环境脚本没有成功,依赖可能只装了一部分:\n{}",
            "{}. The setup script didn't succeed, so dependencies may be only partly updated:\n{}",
            code_note,
            e
        );
        if !stop_first && !to_restart.is_empty() {
            msg.push_str(t(
                "\n服务没有重启,还在跑更新前的代码。",
                "\nThe services weren't restarted and are still running the old code.",
            ));
        }
        for r in restored {
            msg.push('\n');
            msg.push_str(&r);
        }
        return Err(with_manual(msg, &manual));
    }

    if to_restart.is_empty() {
        return Ok(tr!(
            "{},环境已重建。服务没有在运行,需要时点「启动」即可。",
            "{}; environment rebuilt. No service was running; start them when you need them.",
            code_note
        ));
    }
    emit("restarting", None);
    let restart_msgs = restart_all(servers, cfg, &to_restart, !stop_first).await;
    let failed = restart_msgs.iter().any(|m| m.starts_with('✗'));
    let summary = tr!(
        "{},环境已重建。\n{}",
        "{}; environment rebuilt.\n{}",
        code_note,
        restart_msgs.join("\n")
    );
    if failed {
        Err(tr!(
            "{}\n重启失败的服务的原因见「日志」里对应的 STT / LLM 输出。",
            "{}\nFor the service that failed to restart, see its STT / LLM output under Logs.",
            summary
        ))
    } else {
        Ok(summary)
    }
}

/// 逐个重启(`restart = true`)或拉起服务,返回每个的结果(失败的以 ✗ 开头)。
async fn restart_all(
    servers: &Arc<Mutex<ServerManager>>,
    cfg: &ServerConfig,
    kinds: &[ServerKind],
    restart: bool,
) -> Vec<String> {
    let mut out = Vec::new();
    for kind in kinds {
        let r = if restart {
            server_manager::restart(servers, cfg, *kind).await
        } else {
            server_manager::start(servers, cfg, *kind).await
        };
        match r {
            Ok(m) => {
                crate::log_info!("[服务更新] {}", m);
                out.push(format!("✓ {m}"));
            }
            Err(e) => {
                crate::log_error!("[服务更新] {} 没能重新启动:{}", kind.label(), e);
                out.push(format!("✗ {}: {e}", kind.label()));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── 版本比较 ──

    #[test]
    fn versions_compare_numerically_not_as_strings() {
        assert_eq!(compare_versions("2.3.10", "2.3.9"), Some(Ordering::Greater));
        assert_eq!(compare_versions("2.3.9", "2.3.10"), Some(Ordering::Less));
        assert_eq!(compare_versions("2.10.0", "2.9.9"), Some(Ordering::Greater));
        assert_eq!(
            compare_versions("10.0.0", "9.99.99"),
            Some(Ordering::Greater)
        );
        assert_eq!(compare_versions("2.3.2", "2.3.2"), Some(Ordering::Equal));
    }

    #[test]
    fn versions_tolerate_prefix_padding_and_suffixes() {
        assert_eq!(compare_versions("v2.3.2", "2.3.2"), Some(Ordering::Equal));
        assert_eq!(compare_versions("2.3", "2.3.0"), Some(Ordering::Equal));
        assert_eq!(compare_versions(" 2.3.2 ", "2.3.2"), Some(Ordering::Equal));
        // 构建元数据不参与比较;预发布版比正式版旧。
        assert_eq!(
            compare_versions("2.3.2+abc", "2.3.2"),
            Some(Ordering::Equal)
        );
        assert_eq!(compare_versions("2.4.0-rc1", "2.4.0"), Some(Ordering::Less));
        assert_eq!(
            compare_versions("2.4.0-rc1", "2.3.9"),
            Some(Ordering::Greater)
        );
    }

    #[test]
    fn garbage_versions_are_not_comparable() {
        assert_eq!(compare_versions("", "2.3.2"), None);
        assert_eq!(compare_versions("dev", "2.3.2"), None);
        assert_eq!(compare_versions("2..3", "2.3.2"), None);
    }

    #[test]
    fn a_missing_or_unreadable_service_version_counts_as_old() {
        assert_eq!(relation("2.3.3", None), VersionRelation::Unknown);
        assert_eq!(relation("2.3.3", Some("  ")), VersionRelation::Unknown);
        assert_eq!(relation("2.3.3", Some("dev")), VersionRelation::Unknown);
        assert!(VersionRelation::Unknown.needs_update());
        assert!(VersionRelation::Older.needs_update());
        assert!(!VersionRelation::Same.needs_update());
        assert!(!VersionRelation::Newer.needs_update());
        assert_eq!(relation("2.3.10", Some("2.3.9")), VersionRelation::Older);
        assert_eq!(relation("2.3.9", Some("2.3.10")), VersionRelation::Newer);
        assert_eq!(relation("2.3.3", Some("2.3.3")), VersionRelation::Same);
    }

    fn probe_ok(v: serde_json::Value) -> Result<serde_json::Value, String> {
        Ok(v)
    }

    #[test]
    fn report_flags_old_services_and_ignores_unreachable_ones() {
        let old = ServiceVersion::from_probe(
            ServerKind::Stt,
            probe_ok(serde_json::json!({"status": "ok", "version": "1.1.0"})),
        );
        // 老服务端:有 `version`(接口版本)却没有 `app_version`,照样算旧。
        assert_eq!(old.relation, Some(VersionRelation::Unknown));
        assert!(old.app_version.is_none());
        let down = ServiceVersion::from_probe(ServerKind::Llm, Err("连不上".into()));
        assert!(!down.reachable);
        assert_eq!(down.relation, None);

        let r = summarize(ServerMode::Local, vec![old, down.clone()]);
        assert!(r.services_older);
        assert!(!r.services_newer);
        assert!(r.can_update);

        let same = ServiceVersion::from_probe(
            ServerKind::Stt,
            probe_ok(serde_json::json!({"app_version": CLIENT_VERSION})),
        );
        let r = summarize(ServerMode::Remote, vec![same, down.clone()]);
        assert!(!r.services_older && !r.services_newer);
        assert!(!r.can_update, "远程模式没有仓库可更新");

        // 连不上的服务不产生提示。
        let r = summarize(ServerMode::Local, vec![down]);
        assert!(!r.services_older && !r.services_newer);
    }

    #[test]
    fn newer_services_only_count_when_nothing_is_older() {
        let newer = ServiceVersion::from_probe(
            ServerKind::Stt,
            probe_ok(serde_json::json!({"app_version": "999.0.0"})),
        );
        let old = ServiceVersion::from_probe(
            ServerKind::Llm,
            probe_ok(serde_json::json!({"app_version": "0.0.1"})),
        );
        let r = summarize(ServerMode::Local, vec![newer.clone()]);
        assert!(r.services_newer && !r.services_older);
        let r = summarize(ServerMode::Local, vec![newer, old]);
        assert!(r.services_older && !r.services_newer);
    }

    // ── 纯判断 ──

    fn clean_facts() -> GitFacts {
        GitFacts {
            is_repo_root: true,
            branch: Some("main".into()),
            upstream: Some("origin/main".into()),
            dirty: vec![],
            in_progress: None,
        }
    }

    #[test]
    fn git_checks_refuse_every_unsafe_state() {
        assert_eq!(check_git(&clean_facts()), Ok(()));
        assert_eq!(check_git(&GitFacts::default()), Err(Blocker::NotGitRepo));
        let mut f = clean_facts();
        f.dirty = vec![" M services/stt_server.py".into()];
        assert!(matches!(check_git(&f), Err(Blocker::Dirty(_))));
        let mut f = clean_facts();
        f.branch = None;
        assert_eq!(check_git(&f), Err(Blocker::DetachedHead));
        let mut f = clean_facts();
        f.upstream = None;
        assert_eq!(check_git(&f), Err(Blocker::NoUpstream("main".into())));
        let mut f = clean_facts();
        f.in_progress = Some("rebase");
        assert_eq!(check_git(&f), Err(Blocker::InProgress("rebase")));
    }

    #[test]
    fn long_dirty_lists_are_truncated() {
        let mut f = clean_facts();
        f.dirty = (0..20).map(|i| format!(" M f{i}")).collect();
        let Err(Blocker::Dirty(lines)) = check_git(&f) else {
            panic!("应当因为改动被拦下");
        };
        assert_eq!(lines.len(), 9);
        assert!(lines[8].contains("12"));
    }

    #[test]
    fn only_fast_forwards_are_pulled() {
        assert_eq!(plan_pull(0, 0), Ok(PullPlan::UpToDate));
        assert_eq!(plan_pull(0, 3), Ok(PullPlan::FastForward(3)));
        // 只是本地领先:没什么可拉的,开发者自己的提交不碰。
        assert_eq!(plan_pull(2, 0), Ok(PullPlan::UpToDate));
        assert_eq!(
            plan_pull(1, 2),
            Err(Blocker::Diverged {
                ahead: 1,
                behind: 2
            })
        );
    }

    fn status(kind: ServerKind, state: ServerState, owner: ServerOwner) -> ServerStatus {
        ServerStatus {
            kind,
            state,
            port: 7644,
            owner,
            can_stop: owner.can_manage(),
            pid: Some(4242),
            current_model: None,
            detail: None,
            log_path: None,
            recent_logs: Vec::new(),
        }
    }

    #[test]
    fn restart_plan_only_covers_our_own_services_llm_first() {
        let stt = status(ServerKind::Stt, ServerState::Running, ServerOwner::App);
        let llm = status(ServerKind::Llm, ServerState::Starting, ServerOwner::App);
        assert_eq!(
            plan_restart(&[&stt, &llm]),
            Ok(vec![ServerKind::Llm, ServerKind::Stt])
        );
        // 没在跑的不重启(也不拦)。
        let mut llm_off = status(
            ServerKind::Llm,
            ServerState::Stopped,
            ServerOwner::ExternalUnknown,
        );
        llm_off.pid = None;
        assert_eq!(plan_restart(&[&stt, &llm_off]), Ok(vec![ServerKind::Stt]));
        let mut dead = status(ServerKind::Llm, ServerState::Failed, ServerOwner::App);
        dead.pid = None;
        assert_eq!(plan_restart(&[&stt, &dead]), Ok(vec![ServerKind::Stt]));
    }

    /// 最要紧的一条:有不是本应用启动的服务在跑,整个更新都不做——
    /// 连「外部(本项目)」这种停止按钮能停的也一样。
    #[test]
    fn restart_plan_refuses_when_a_foreign_service_is_running() {
        let stt = status(ServerKind::Stt, ServerState::Running, ServerOwner::App);
        for owner in [ServerOwner::ExternalProject, ServerOwner::ExternalUnknown] {
            let llm = status(ServerKind::Llm, ServerState::Running, owner);
            assert_eq!(
                plan_restart(&[&stt, &llm]),
                Err(Blocker::ForeignService(ServerKind::Llm, owner))
            );
            let st2 = status(ServerKind::Stt, ServerState::Starting, owner);
            assert_eq!(
                plan_restart(&[&st2]),
                Err(Blocker::ForeignService(ServerKind::Stt, owner))
            );
            assert!(!Blocker::ForeignService(ServerKind::Stt, owner)
                .message()
                .is_empty());
        }
    }

    #[test]
    fn setup_args_restore_the_original_install() {
        let mac = EnvFacts {
            apple_silicon: true,
            torch: Some("2.5.1".into()),
            llama_cpp: false,
            dev: false,
        };
        assert!(setup_args(&mac, false).is_empty());
        let mac_dev = EnvFacts {
            dev: true,
            ..mac.clone()
        };
        assert_eq!(setup_args(&mac_dev, false), vec!["--dev"]);

        let linux = EnvFacts {
            apple_silicon: false,
            torch: Some("2.5.1+cu124".into()),
            llama_cpp: true,
            dev: false,
        };
        assert_eq!(
            setup_args(&linux, false),
            vec!["--backend", "cuda", "--llm"]
        );
        assert_eq!(setup_args(&linux, true), vec!["-Backend", "cuda", "-Llm"]);

        let rocm = EnvFacts {
            torch: Some("2.5.1+rocm6.2".into()),
            ..Default::default()
        };
        assert_eq!(setup_args(&rocm, false), vec!["--backend", "rocm"]);
        // PowerShell 版不认 rocm,交给它自己探测。
        assert!(setup_args(&rocm, true).is_empty());

        let cpu = EnvFacts {
            torch: Some("2.5.1+cpu".into()),
            dev: true,
            ..Default::default()
        };
        assert_eq!(setup_args(&cpu, true), vec!["-Backend", "cpu", "-Dev"]);
        // 没有本地版本标记 / 没装 torch:让脚本自己探测。
        assert!(setup_args(&EnvFacts::default(), false).is_empty());
    }

    #[test]
    fn manual_commands_match_the_platform() {
        let unix = manual_commands("/home/u/vif", &["--llm".into()], false);
        assert_eq!(
            unix,
            "cd \"/home/u/vif\"\ngit pull --ff-only\nscripts/setup-env.sh --llm"
        );
        let win = manual_commands("C:\\vif", &[], true);
        assert!(win.ends_with("powershell -ExecutionPolicy Bypass -File scripts\\setup-env.ps1"));
    }

    #[test]
    fn ansi_colors_are_stripped() {
        assert_eq!(strip_ansi("\u{1b}[1;36m==>\u{1b}[0m uv 0.5"), "==> uv 0.5");
        assert_eq!(strip_ansi("纯文本"), "纯文本");
    }

    #[test]
    fn git_is_found_outside_the_gui_path() {
        let found = find_git(Some(OsStr::new("/usr/bin:/bin")), false, |p| {
            p == Path::new("/opt/homebrew/bin/git")
        });
        assert_eq!(found, Some(PathBuf::from("/opt/homebrew/bin/git")));
        assert_eq!(find_git(None, false, |_| false), None);
    }

    #[test]
    fn only_the_repo_venv_counts() {
        let dir = std::env::temp_dir().join(format!("vif-venv-{}", std::process::id()));
        let bin = dir.join(".venv").join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let other = dir.join("elsewhere").join("bin");
        std::fs::create_dir_all(&other).unwrap();
        assert!(python_in_repo_venv(&bin.join("python"), &dir));
        assert!(!python_in_repo_venv(&other.join("python"), &dir));
        assert!(!python_in_repo_venv(Path::new("/usr/bin/python3"), &dir));
        std::fs::remove_dir_all(&dir).ok();
    }

    // ── 对着真的 git 仓库跑 ──
    //
    // 全部在临时目录里现建:一个裸仓库当远端,一个克隆当「用户的仓库」,另一个克隆
    // 用来往远端推新提交。绝不碰开发机上的真实仓库。

    struct Sandbox {
        root: PathBuf,
        git: PathBuf,
    }

    impl Sandbox {
        fn new(tag: &str) -> Option<Self> {
            let git = locate_git()?;
            let root =
                std::env::temp_dir().join(format!("vif-svc-update-{}-{}", tag, std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(&root).unwrap();
            let sb = Self { root, git };
            sb.run(
                &sb.root,
                &["init", "--bare", "-q", "-b", "main", "origin.git"],
            );
            sb.run(&sb.root, &["clone", "-q", "origin.git", "work"]);
            std::fs::write(sb.work().join("a.txt"), "one\n").unwrap();
            sb.commit(&sb.work(), "first");
            sb.run(&sb.work(), &["push", "-q", "-u", "origin", "HEAD:main"]);
            sb.run(&sb.root, &["clone", "-q", "origin.git", "other"]);
            Some(sb)
        }

        fn work(&self) -> PathBuf {
            self.root.join("work")
        }

        fn other(&self) -> PathBuf {
            self.root.join("other")
        }

        fn run(&self, dir: &Path, args: &[&str]) -> String {
            let out = std::process::Command::new(&self.git)
                .args([
                    "-c",
                    "user.name=vif-test",
                    "-c",
                    "user.email=vif-test@example.com",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "init.defaultBranch=main",
                ])
                .args(args)
                .current_dir(dir)
                .env("GIT_TERMINAL_PROMPT", "0")
                .output()
                .unwrap();
            assert!(
                out.status.success(),
                "git {:?} 失败: {}",
                args,
                String::from_utf8_lossy(&out.stderr)
            );
            String::from_utf8_lossy(&out.stdout).trim().to_string()
        }

        fn commit(&self, dir: &Path, msg: &str) {
            self.run(dir, &["add", "-A"]);
            self.run(dir, &["commit", "-q", "-m", msg]);
        }

        /// 从另一个克隆往远端推一个新提交。
        fn push_upstream_change(&self, name: &str) {
            std::fs::write(self.other().join(name), "new\n").unwrap();
            self.commit(&self.other(), name);
            self.run(&self.other(), &["push", "-q", "origin", "HEAD:main"]);
        }

        async fn fetch(&self) {
            let mut cmd = git_std(&self.git, &self.work());
            cmd.arg("fetch");
            run_streamed(cmd, Duration::from_secs(60), &mut |_| {})
                .await
                .expect("fetch 失败");
        }
    }

    impl Drop for Sandbox {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[tokio::test]
    async fn a_clean_repo_behind_upstream_fast_forwards() {
        let Some(sb) = Sandbox::new("ff") else {
            eprintln!("没有 git,跳过");
            return;
        };
        let facts = gather_git_facts(&sb.git, &sb.work());
        assert_eq!(facts.branch.as_deref(), Some("main"));
        assert_eq!(facts.upstream.as_deref(), Some("origin/main"));
        assert_eq!(check_git(&facts), Ok(()));
        sb.fetch().await;
        assert_eq!(ahead_behind(&sb.git, &sb.work()), Some((0, 0)));
        assert_eq!(plan_pull(0, 0), Ok(PullPlan::UpToDate));

        sb.push_upstream_change("b.txt");
        sb.fetch().await;
        let (a, b) = ahead_behind(&sb.git, &sb.work()).unwrap();
        assert_eq!(plan_pull(a, b), Ok(PullPlan::FastForward(1)));

        // 真跑一次和流程里一模一样的 pull。
        let mut pull = git_std(&sb.git, &sb.work());
        pull.args(["pull", "--ff-only", "--no-rebase"]);
        let mut lines = Vec::new();
        run_streamed(pull, Duration::from_secs(60), &mut |l| {
            lines.push(l.to_string())
        })
        .await
        .expect("快进应当成功");
        assert!(sb.work().join("b.txt").exists());
        assert_eq!(ahead_behind(&sb.git, &sb.work()), Some((0, 0)));
    }

    #[tokio::test]
    async fn uncommitted_changes_block_but_untracked_files_do_not() {
        let Some(sb) = Sandbox::new("dirty") else {
            return;
        };
        std::fs::write(sb.work().join("notes.txt"), "草稿\n").unwrap();
        assert_eq!(
            check_git(&gather_git_facts(&sb.git, &sb.work())),
            Ok(()),
            "未跟踪的文件不该拦住更新"
        );
        std::fs::write(sb.work().join("a.txt"), "changed\n").unwrap();
        let facts = gather_git_facts(&sb.git, &sb.work());
        let Err(Blocker::Dirty(lines)) = check_git(&facts) else {
            panic!("改了已跟踪的文件应当被拦下: {:?}", facts);
        };
        assert!(lines.iter().any(|l| l.contains("a.txt")));
        // 暂存了也一样。
        sb.run(&sb.work(), &["add", "a.txt"]);
        assert!(matches!(
            check_git(&gather_git_facts(&sb.git, &sb.work())),
            Err(Blocker::Dirty(_))
        ));
    }

    fn local_cfg(repo: &Path) -> ServerConfig {
        ServerConfig {
            host: "localhost".into(),
            port: 6544,
            mode: ServerMode::Local,
            local: crate::config::LocalServerConfig {
                repo_path: Some(repo.display().to_string()),
                ..Default::default()
            },
            token: None,
        }
    }

    /// 「检查服务更新」:版本号一样时也能看出上游有新提交,且只读 —— 工作区和本地分支不动。
    #[tokio::test]
    async fn check_code_reports_new_upstream_commits_without_touching_the_repo() {
        let Some(sb) = Sandbox::new("check-code") else {
            return;
        };
        std::fs::create_dir_all(sb.work().join("services")).unwrap();
        std::fs::write(sb.work().join("services/stt_server.py"), "# stub\n").unwrap();
        std::fs::write(sb.work().join("services/llm_server.py"), "# stub\n").unwrap();
        sb.commit(&sb.work(), "services");
        sb.run(&sb.work(), &["push", "-q", "origin", "HEAD:main"]);
        sb.run(&sb.other(), &["pull", "-q", "--ff-only"]);
        let cfg = local_cfg(&sb.work());

        let st = check_code(&cfg).await;
        assert!(st.checked, "{:?}", st.error);
        assert_eq!((st.ahead, st.behind), (0, 0));
        assert!(st.new_commits.is_empty());

        sb.push_upstream_change("fix-setup-script.txt");
        let head_before = sb.run(&sb.work(), &["rev-parse", "HEAD"]);
        let st = check_code(&cfg).await;
        assert!(st.checked, "{:?}", st.error);
        assert_eq!(st.behind, 1);
        assert_eq!(st.new_commits.len(), 1);
        assert!(
            st.new_commits[0].ends_with("fix-setup-script.txt"),
            "{:?}",
            st.new_commits
        );
        assert_eq!(st.branch.as_deref(), Some("main"));
        // 只 fetch,不 pull
        assert_eq!(sb.run(&sb.work(), &["rev-parse", "HEAD"]), head_before);
        assert!(!sb.work().join("fix-setup-script.txt").exists());
    }

    #[tokio::test]
    async fn check_code_explains_why_it_cannot_check() {
        let mut cfg = local_cfg(Path::new("/definitely/not/a/repo"));
        let st = check_code(&cfg).await;
        assert!(!st.checked && st.error.is_some());
        cfg.mode = ServerMode::Remote;
        let st = check_code(&cfg).await;
        assert!(!st.checked && st.error.is_some());
    }

    #[tokio::test]
    async fn a_branch_without_upstream_or_a_detached_head_is_refused() {
        let Some(sb) = Sandbox::new("upstream") else {
            return;
        };
        sb.run(&sb.work(), &["switch", "-q", "-c", "feature"]);
        assert_eq!(
            check_git(&gather_git_facts(&sb.git, &sb.work())),
            Err(Blocker::NoUpstream("feature".into()))
        );
        sb.run(&sb.work(), &["switch", "-q", "--detach", "main"]);
        assert_eq!(
            check_git(&gather_git_facts(&sb.git, &sb.work())),
            Err(Blocker::DetachedHead)
        );
    }

    #[tokio::test]
    async fn diverged_history_is_refused() {
        let Some(sb) = Sandbox::new("diverged") else {
            return;
        };
        std::fs::write(sb.work().join("local.txt"), "mine\n").unwrap();
        sb.commit(&sb.work(), "local");
        sb.push_upstream_change("remote.txt");
        sb.fetch().await;
        let (a, b) = ahead_behind(&sb.git, &sb.work()).unwrap();
        assert_eq!((a, b), (1, 1));
        assert_eq!(
            plan_pull(a, b),
            Err(Blocker::Diverged {
                ahead: 1,
                behind: 1
            })
        );
    }

    #[tokio::test]
    async fn not_a_repo_or_a_subdirectory_is_refused() {
        let Some(sb) = Sandbox::new("norepo") else {
            return;
        };
        let plain = sb.root.join("plain");
        std::fs::create_dir_all(&plain).unwrap();
        assert_eq!(
            check_git(&gather_git_facts(&sb.git, &plain)),
            Err(Blocker::NotGitRepo)
        );
        let sub = sb.work().join("sub");
        std::fs::create_dir_all(&sub).unwrap();
        assert_eq!(
            check_git(&gather_git_facts(&sb.git, &sub)),
            Err(Blocker::NotGitRepo),
            "仓库路径必须是仓库根,不能是某个仓库里的子目录"
        );
    }

    #[tokio::test]
    async fn a_merge_in_progress_is_refused() {
        let Some(sb) = Sandbox::new("merge") else {
            return;
        };
        std::fs::write(sb.work().join("a.txt"), "local\n").unwrap();
        sb.commit(&sb.work(), "local");
        std::fs::write(sb.other().join("a.txt"), "remote\n").unwrap();
        sb.commit(&sb.other(), "remote");
        sb.run(&sb.other(), &["push", "-q", "origin", "HEAD:main"]);
        sb.fetch().await;
        // 故意制造一个冲突的合并,停在半路。
        let _ = std::process::Command::new(&sb.git)
            .args([
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "merge",
                "origin/main",
            ])
            .current_dir(sb.work())
            .output();
        let facts = gather_git_facts(&sb.git, &sb.work());
        assert_eq!(check_git(&facts), Err(Blocker::InProgress("merge")));
    }

    #[tokio::test]
    async fn streamed_commands_report_exit_codes_and_output() {
        let mut lines = Vec::new();
        let mut cmd = if cfg!(windows) {
            let mut c = std::process::Command::new("cmd");
            c.args(["/C", "echo hello & echo oops 1>&2 & exit 3"]);
            c
        } else {
            let mut c = std::process::Command::new("sh");
            c.args([
                "-c",
                "echo hello; printf '\\033[1;36m==>\\033[0m colored\\n'; echo oops >&2; exit 3",
            ]);
            c
        };
        cmd.stdin(Stdio::null());
        let err = run_streamed(cmd, Duration::from_secs(20), &mut |l| {
            lines.push(l.to_string())
        })
        .await
        .unwrap_err();
        assert!(err.contains('3'), "{err}");
        assert!(err.contains("oops"), "{err}");
        assert!(lines.iter().any(|l| l.trim() == "hello"));
        if !cfg!(windows) {
            assert!(lines.iter().any(|l| l == "==> colored"), "{lines:?}");
        }
    }

    /// 把整个流程在临时仓库里走一遍:建环境脚本换成一个只记下参数的假脚本,
    /// 端口用没人监听的,所以不会碰到任何真服务。
    #[cfg(unix)]
    struct ProjectSandbox {
        sb: Sandbox,
        cfg: ServerConfig,
        servers: Arc<Mutex<ServerManager>>,
    }

    #[cfg(unix)]
    impl ProjectSandbox {
        fn new(tag: &str, script: &str) -> Option<Self> {
            let home = server_manager::home_dir();
            crate::env_check::find_uv(
                std::env::var_os("PATH").as_deref(),
                home.as_deref(),
                false,
                |p| p.is_file(),
            )?;
            let python = std::env::var_os("PATH").and_then(|p| {
                std::env::split_paths(&p)
                    .map(|d| d.join("python3"))
                    .find(|p| p.is_file())
            })?;
            let sb = Sandbox::new(tag)?;
            let work = sb.work();
            std::fs::create_dir_all(work.join("services")).unwrap();
            std::fs::create_dir_all(work.join("scripts")).unwrap();
            std::fs::write(work.join("services/stt_server.py"), "").unwrap();
            std::fs::write(work.join("services/llm_server.py"), "").unwrap();
            std::fs::write(work.join("scripts/setup-env.sh"), script).unwrap();
            std::fs::write(work.join(".gitignore"), ".venv/\nsetup-ran.txt\n").unwrap();
            sb.commit(&work, "project");
            sb.run(&work, &["push", "-q", "origin", "HEAD:main"]);
            sb.run(&sb.other(), &["pull", "-q", "--ff-only"]);
            std::fs::create_dir_all(work.join(".venv/bin")).unwrap();
            std::os::unix::fs::symlink(&python, work.join(".venv/bin/python")).unwrap();
            let cfg = ServerConfig {
                host: "localhost".into(),
                port: 6544,
                mode: ServerMode::Local,
                local: crate::config::LocalServerConfig {
                    repo_path: Some(work.display().to_string()),
                    python_path: Some(work.join(".venv/bin/python").display().to_string()),
                    // 没人监听的端口:报告里两个服务都是「未运行」。
                    stt_port: 59_644,
                    llm_port: 59_645,
                    ..Default::default()
                },
                token: None,
            };
            let servers = Arc::new(Mutex::new(ServerManager::new(sb.root.join("data"))));
            Some(Self { sb, cfg, servers })
        }

        async fn run(&self) -> (Result<String, String>, Vec<(&'static str, Option<String>)>) {
            let events = Mutex::new(Vec::new());
            let emit = |stage: &'static str, line: Option<String>| {
                events.lock().unwrap().push((stage, line));
            };
            let r = run_update(&emit, &self.servers, &self.cfg).await;
            (r, events.into_inner().unwrap())
        }
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn the_whole_flow_pulls_runs_setup_and_leaves_stopped_services_alone() {
        let script = "#!/usr/bin/env bash\nset -e\necho \"==> fake setup $*\"\ncommand -v uv >/dev/null && echo 'uv on PATH'\necho \"$*\" > \"$(dirname \"$0\")/../setup-ran.txt\"\n";
        let Some(p) = ProjectSandbox::new("flow", script) else {
            eprintln!("没有 git / uv / python3,跳过");
            return;
        };
        p.sb.push_upstream_change("feature.txt");

        let (r, events) = p.run().await;
        let msg = r.expect("干净、可快进的仓库应当更新成功");
        assert!(p.sb.work().join("feature.txt").exists(), "应当拉到了新提交");
        assert!(
            p.sb.work().join("setup-ran.txt").exists(),
            "应当跑了建环境脚本"
        );
        assert!(msg.contains('1'), "{msg}");
        let stages: Vec<_> = events.iter().map(|(s, _)| *s).collect();
        for want in ["checking", "fetching", "pulling", "setup"] {
            assert!(stages.contains(&want), "缺少阶段 {want}: {stages:?}");
        }
        assert!(
            !stages.contains(&"restarting"),
            "没有在跑的服务,不该重启任何东西"
        );
        let lines: Vec<_> = events.iter().filter_map(|(_, l)| l.clone()).collect();
        assert!(lines.iter().any(|l| l.contains("fake setup")), "{lines:?}");
        assert!(
            lines.iter().any(|l| l.contains("uv on PATH")),
            "建环境脚本的 PATH 里应当有 uv: {lines:?}"
        );

        // 再来一次:代码已是最新,照样重建环境(用户可能拉了代码却没重跑脚本)。
        std::fs::remove_file(p.sb.work().join("setup-ran.txt")).unwrap();
        let (r, events) = p.run().await;
        r.expect("已是最新时也应当成功");
        assert!(p.sb.work().join("setup-ran.txt").exists());
        assert!(!events.iter().any(|(s, _)| *s == "pulling"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn the_whole_flow_stops_before_touching_a_dirty_repo() {
        let script = "#!/usr/bin/env bash\ntouch \"$(dirname \"$0\")/../setup-ran.txt\"\n";
        let Some(p) = ProjectSandbox::new("flowdirty", script) else {
            return;
        };
        p.sb.push_upstream_change("feature.txt");
        std::fs::write(p.sb.work().join("a.txt"), "local edit\n").unwrap();
        let (r, _) = p.run().await;
        let err = r.unwrap_err();
        assert!(err.contains("a.txt"), "{err}");
        assert!(
            err.contains("git pull --ff-only"),
            "拦下时要给出手动命令: {err}"
        );
        assert!(
            !p.sb.work().join("feature.txt").exists(),
            "被拦下时不能拉取"
        );
        assert!(
            !p.sb.work().join("setup-ran.txt").exists(),
            "被拦下时不能跑脚本"
        );
        assert_eq!(
            std::fs::read_to_string(p.sb.work().join("a.txt")).unwrap(),
            "local edit\n",
            "用户的改动必须原样保留"
        );
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn a_failing_setup_script_is_reported_with_its_output() {
        let script = "#!/usr/bin/env bash\necho 'resolution failed: mlx-lm>=0.31.2' >&2\nexit 7\n";
        let Some(p) = ProjectSandbox::new("flowfail", script) else {
            return;
        };
        let (r, events) = p.run().await;
        let err = r.unwrap_err();
        assert!(err.contains('7'), "{err}");
        assert!(err.contains("mlx-lm"), "{err}");
        assert!(!events.iter().any(|(s, _)| *s == "restarting"));
    }
}
