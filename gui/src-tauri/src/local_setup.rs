//! 新机器引导:把服务端代码取下来、把 Python 环境建好。
//!
//! `scripts/setup-env.*` 已经管了「探测硬件 → 装依赖」(连 uv 和 Python 都是它 / uv
//! 自己准备的),可再往前一步——**拿到代码**——一直要用户照着 README 手敲
//! `git clone`,建环境也只是把命令给出来让人复制到终端里跑。对没碰过命令行的人,
//! 「本地管理」在这一步就走不下去了。
//!
//! 这里把这两步接起来,在应用里一键做完:
//!
//! 1. 仓库:配置里已经有可用的仓库就用它;没有就 `git clone` 到默认目录
//!    (`~/voice-input-framework`,自动探测第一个找的位置)。**只往不存在或空的目录里
//!    克隆**,目录里有别的东西一律不碰。
//! 2. 环境:跑 `setup-env.sh` / `setup-env.ps1`。已有环境时沿用当初的参数
//!    (`service_update::setup_args`),只按需补上 LLM 后处理的依赖。
//!
//! 唯一真正的前置条件是 git(uv 由脚本装,Python 由 uv 装)。没有 git 时不硬来,给出
//! 这个平台上怎么装。
//!
//! 和「更新服务」共用一把锁(`service_update::RUNNING`):两边都在动同一个仓库和
//! 同一个 `.venv`。判断规则都是纯函数,单测覆盖;克隆对着测试里现建的本地仓库跑。

use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::Ordering as AtomicOrdering;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::config::ServerConfig;
use crate::i18n::t;
use crate::server_manager::{self, ServerManager};
use crate::service_update;
use crate::tr;

/// 服务端代码的出处。`VIF_REPO_URL` 可以换成镜像或自己的 fork。
pub const REPO_URL: &str = "https://github.com/3F3Feng/voice-input-framework.git";

/// 克隆的时限。仓库不大(几十 MB),但境内连 github.com 可能很慢。
const CLONE_TIMEOUT: Duration = Duration::from_secs(20 * 60);

fn repo_url() -> String {
    std::env::var("VIF_REPO_URL")
        .ok()
        .map(|u| u.trim().to_string())
        .filter(|u| !u.is_empty())
        .unwrap_or_else(|| REPO_URL.to_string())
}

/// 要克隆到的那个目录现在是什么情况。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetState {
    /// 不存在:可以克隆。
    Missing,
    /// 存在但是空的:可以克隆。
    Empty,
    /// 已经是本项目的仓库:直接用,不克隆。
    Repo,
    /// 有别的东西(包括克隆到一半留下的):不碰。
    Occupied,
}

pub fn classify_target(path: &Path) -> TargetState {
    if server_manager::is_repo_root(path) {
        return TargetState::Repo;
    }
    match std::fs::read_dir(path) {
        Err(_) if !path.exists() => TargetState::Missing,
        // 读不了(没权限、是个文件):当成有东西,不碰。
        Err(_) => TargetState::Occupied,
        Ok(mut entries) => {
            if entries.next().is_none() {
                TargetState::Empty
            } else {
                TargetState::Occupied
            }
        }
    }
}

/// 默认克隆到哪。和 `server_manager::detect_repo` 找的第一个位置一致,以后「自动探测」
/// 也找得到它。
pub fn default_target(home: &Path) -> PathBuf {
    home.join("voice-input-framework")
}

/// 这一次该干什么。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Plan {
    /// 仓库已经在了,只建环境。
    UseRepo(PathBuf),
    /// 先克隆到这里,再建环境。
    Clone(PathBuf),
}

/// `configured`:配置里那个**确认可用**的仓库(不可用的不要传进来)。
pub fn plan(configured: Option<&Path>, target: &Path, state: TargetState) -> Result<Plan, String> {
    if let Some(repo) = configured {
        return Ok(Plan::UseRepo(repo.to_path_buf()));
    }
    match state {
        TargetState::Repo => Ok(Plan::UseRepo(target.to_path_buf())),
        TargetState::Missing | TargetState::Empty => Ok(Plan::Clone(target.to_path_buf())),
        TargetState::Occupied => Err(tr!(
            "{} 里已经有别的文件,不会往里面下载。请换一个不存在或空的文件夹;如果那是上次下载到一半留下的,删掉它再试。",
            "{} already contains other files, so nothing will be downloaded into it. Pick a folder that doesn't exist or is empty; if it's left over from an interrupted download, delete it and try again.",
            target.display()
        )),
    }
}

/// 没有 git 时怎么装。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct GitHint {
    /// 一句说明。
    pub text: String,
    /// 可以复制去终端里跑的命令。
    pub command: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Os {
    Windows,
    Mac,
    Linux,
}

impl Os {
    pub fn current() -> Self {
        if cfg!(windows) {
            Os::Windows
        } else if cfg!(target_os = "macos") {
            Os::Mac
        } else {
            Os::Linux
        }
    }
}

pub fn git_hint(os: Os) -> GitHint {
    match os {
        Os::Windows => GitHint {
            text: t(
                "这台电脑上没有 Git。在 PowerShell 里运行下面这条命令安装(或者从 https://git-scm.com/download/win 下载安装包),装完点「重新检测」。",
                "Git isn't installed on this computer. Run the command below in PowerShell to install it (or download the installer from https://git-scm.com/download/win), then click Check again.",
            )
            .into(),
            command: Some("winget install --id Git.Git -e --source winget".into()),
        },
        Os::Mac => GitHint {
            text: t(
                "这台 Mac 上没有 Git(它跟着 Apple 的命令行工具一起装)。在「终端」里运行下面这条命令,按弹出的窗口装完后点「重新检测」。",
                "Git isn't installed on this Mac (it comes with Apple's command line tools). Run the command below in Terminal, finish the installer that pops up, then click Check again.",
            )
            .into(),
            command: Some("xcode-select --install".into()),
        },
        Os::Linux => GitHint {
            text: t(
                "这台电脑上没有 Git。用发行版的包管理器安装(Debian / Ubuntu 是下面这条,Fedora 用 dnf,Arch 用 pacman),装完点「重新检测」。",
                "Git isn't installed on this computer. Install it with your distribution's package manager (the command below is for Debian / Ubuntu; use dnf on Fedora, pacman on Arch), then click Check again.",
            )
            .into(),
            command: Some("sudo apt install git".into()),
        },
    }
}

/// 建环境脚本的参数里有没有 LLM 那一项。
fn has_llm_flag(args: &[String]) -> bool {
    args.iter().any(|a| a == "--llm" || a == "-Llm")
}

/// 在现有参数上按需补 LLM 后处理的依赖。Apple Silicon 用 MLX,不需要(脚本在那边
/// 也会忽略这个参数,但别把没用的东西写进命令里)。
pub fn with_llm(
    mut args: Vec<String>,
    want_llm: bool,
    apple_silicon: bool,
    windows: bool,
) -> Vec<String> {
    if want_llm && !apple_silicon && !has_llm_flag(&args) {
        args.push(if windows { "-Llm" } else { "--llm" }.into());
    }
    args
}

/// 给前端的现状:要不要克隆、能不能克隆、缺什么。
#[derive(Debug, Clone, Serialize)]
pub struct SetupStatus {
    /// 配置里(或默认目录下)已经可用的仓库。有它就只需要建环境。
    pub repo_path: Option<String>,
    /// 那个仓库里已经有的解释器(`.venv`)。
    pub python_path: Option<String>,
    /// 没有仓库时会克隆到哪。
    pub target_dir: String,
    pub target_state: TargetState,
    /// git 在哪;没找到时为空,`git_hint` 说怎么装。
    pub git: Option<String>,
    pub git_hint: GitHint,
    /// 这台机器上 LLM 后处理要不要额外装依赖(Apple Silicon 不用)。
    pub llm_optional: bool,
    /// 有一次安装 / 更新正在跑。
    pub running: bool,
    pub repo_url: String,
}

fn configured_repo(cfg: &ServerConfig) -> Option<PathBuf> {
    cfg.local
        .repo_path
        .as_deref()
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .map(PathBuf::from)
        .filter(|p| server_manager::is_repo_root(p))
}

fn target_dir(dir: Option<&str>) -> Result<PathBuf, String> {
    match dir.map(str::trim).filter(|d| !d.is_empty()) {
        Some(d) => Ok(PathBuf::from(d)),
        None => server_manager::home_dir()
            .map(|h| default_target(&h))
            .ok_or_else(|| {
                t(
                    "找不到用户主目录,请手动填一个文件夹",
                    "Couldn't find the home folder; enter a folder manually",
                )
                .to_string()
            }),
    }
}

const APPLE_SILICON: bool = cfg!(all(target_os = "macos", target_arch = "aarch64"));

pub fn status(cfg: &ServerConfig, dir: Option<&str>) -> Result<SetupStatus, String> {
    let target = target_dir(dir)?;
    let target_state = classify_target(&target);
    let repo = configured_repo(cfg)
        .or_else(|| (target_state == TargetState::Repo).then(|| target.clone()));
    let python_path = repo.as_deref().and_then(server_manager::find_python);
    Ok(SetupStatus {
        repo_path: repo.map(|p| p.display().to_string()),
        python_path,
        target_dir: target.display().to_string(),
        target_state,
        git: service_update::locate_git().map(|p| p.display().to_string()),
        git_hint: git_hint(Os::current()),
        llm_optional: !APPLE_SILICON,
        running: service_update::RUNNING.load(AtomicOrdering::SeqCst),
        repo_url: repo_url(),
    })
}

/// 推给前端的进度(`local-setup` 事件)。
#[derive(Debug, Clone, Serialize)]
pub struct Progress {
    /// checking / cloning / setup / restarting / done / failed / progress(某一阶段的一行输出)
    pub stage: &'static str,
    pub line: Option<String>,
}

type Emit<'a> = &'a (dyn Fn(&'static str, Option<String>) + Send + Sync);

/// 装好之后的结果。路径由调用方写进配置。
#[derive(Debug, Clone, Serialize)]
pub struct Outcome {
    pub repo_path: String,
    pub python_path: String,
    pub message: String,
}

/// `git clone`。不许它问任何问题(后台没有终端可以回答)。
fn clone_command(git: &Path, url: &str, target: &Path) -> std::process::Command {
    let mut cmd = std::process::Command::new(git);
    cmd.arg("clone")
        // 只要默认分支:别的分支用不上,少下一点是一点。
        .arg("--single-branch")
        .arg(url)
        .arg(target)
        .stdin(Stdio::null())
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GCM_INTERACTIVE", "never");
    server_manager::no_console(&mut cmd);
    cmd
}

/// 克隆并确认拿到的确实是本项目。
async fn clone_repo(
    git: &Path,
    url: &str,
    target: &Path,
    on_line: &mut (dyn FnMut(&str) + Send),
) -> Result<(), String> {
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            tr!(
                "创建文件夹 {} 失败:{}",
                "Couldn't create the folder {}: {}",
                parent.display(),
                e
            )
        })?;
    }
    service_update::run_streamed(clone_command(git, url, target), CLONE_TIMEOUT, on_line)
        .await
        .map_err(|e| {
            tr!(
                "下载代码没有成功(git clone):\n{}\n\n检查一下能不能打开 github.com;网络不通时可以稍后再试。",
                "Downloading the code didn't succeed (git clone):\n{}\n\nCheck that github.com is reachable; if the network is down, try again later.",
                e
            )
        })?;
    if !server_manager::is_repo_root(target) {
        return Err(tr!(
            "下载完成,但 {} 里没有 services/stt_server.py——拿到的不是本项目的代码({})。",
            "The download finished, but {} has no services/stt_server.py — that isn't this project's code ({}).",
            target.display(),
            url
        ));
    }
    Ok(())
}

/// 手动跑的命令(失败时附上)。
fn manual_command(repo: &Path, args: &[String], windows: bool) -> String {
    let base = crate::env_check::setup_command(Some(&repo.display().to_string()), windows);
    if args.is_empty() {
        base
    } else {
        format!("{} {}", base, args.join(" "))
    }
}

/// 一键:取代码 + 建环境。成功时返回路径,由调用方写进配置。
pub async fn run(
    app: tauri::AppHandle,
    servers: Arc<Mutex<ServerManager>>,
    cfg: ServerConfig,
    dir: Option<String>,
    llm: bool,
) -> Result<Outcome, String> {
    if service_update::RUNNING.swap(true, AtomicOrdering::SeqCst) {
        return Err(t(
            "已经有一次安装或更新在进行中。",
            "An install or update is already running.",
        )
        .to_string());
    }
    let _guard = service_update::RunningGuard;
    let emit = move |stage: &'static str, line: Option<String>| {
        use tauri::Emitter;
        let _ = app.emit("local-setup", Progress { stage, line });
    };
    let result = run_inner(&emit, &servers, &cfg, dir.as_deref(), llm).await;
    match &result {
        Ok(o) => {
            crate::log_info!("[本机安装] 完成:{}", o.message);
            emit("done", Some(o.message.clone()));
        }
        Err(e) => {
            crate::log_error!("[本机安装] 没有完成:{}", e);
            emit("failed", Some(e.clone()));
        }
    }
    result
}

async fn run_inner(
    emit: Emit<'_>,
    servers: &Arc<Mutex<ServerManager>>,
    cfg: &ServerConfig,
    dir: Option<&str>,
    llm: bool,
) -> Result<Outcome, String> {
    let windows = cfg!(windows);
    emit("checking", None);
    let target = target_dir(dir)?;
    let todo = plan(
        configured_repo(cfg).as_deref(),
        &target,
        classify_target(&target),
    )?;
    let git = service_update::locate_git();
    let mut log_line = |line: &str| {
        crate::log_info!("[本机安装] {}", line);
        emit("progress", Some(line.to_string()));
    };

    let (repo, cloned) = match todo {
        Plan::UseRepo(repo) => (repo, false),
        Plan::Clone(target) => {
            let hint = git_hint(Os::current());
            let git = git.as_deref().ok_or_else(|| match &hint.command {
                Some(c) => format!("{}\n{}", hint.text, c),
                None => hint.text.clone(),
            })?;
            let url = repo_url();
            emit(
                "cloning",
                Some(tr!("{} → {}", "{} → {}", url, target.display())),
            );
            crate::log_info!("[本机安装] git clone {} → {}", url, target.display());
            clone_repo(git, &url, &target, &mut log_line).await?;
            (target, true)
        }
    };

    // 已有环境时沿用当初的参数(后端、dev),只按需补上 LLM;没有环境就让脚本自己探测硬件。
    let existing_python = server_manager::find_python(&repo);
    let env = match &existing_python {
        Some(py) => service_update::probe_env(py, &repo).await,
        None => None,
    };
    let args = with_llm(
        service_update::setup_args(&env.unwrap_or_default(), windows),
        llm,
        APPLE_SILICON,
        windows,
    );
    let manual = manual_command(&repo, &args, windows);

    // Windows 上正在运行的 Python 进程锁着它加载的 .pyd / .dll,uv 换不掉;已有环境
    // 而且有服务在跑时,和「更新服务」一样先停、建完再拉起来。有不是本应用启动的
    // 服务在跑就不动。
    let mut stopped = Vec::new();
    if windows && existing_python.is_some() {
        let report = server_manager::report(servers, cfg).await;
        stopped =
            service_update::plan_restart(&[&report.stt, &report.llm]).map_err(|b| b.message())?;
        for kind in &stopped {
            if let Err(e) = server_manager::stop(servers, cfg, *kind) {
                crate::log_error!("[本机安装] 停止 {} 失败:{}", kind.label(), e);
            }
        }
    }

    let script = if windows {
        "setup-env.ps1"
    } else {
        "setup-env.sh"
    };
    let cmd_text = format!("{} {}", script, args.join(" "));
    emit("setup", Some(cmd_text.trim().to_string()));
    crate::log_info!("[本机安装] 建环境:{}", cmd_text.trim());
    let uv = crate::env_check::find_uv(
        std::env::var_os("PATH").as_deref(),
        server_manager::home_dir().as_deref(),
        windows,
        |p| p.is_file(),
    );
    let uv_dir = uv.as_deref().and_then(Path::parent);
    let git_dir = git.as_deref().and_then(Path::parent);
    let front: Vec<&Path> = [uv_dir, git_dir].into_iter().flatten().collect();
    let path = service_update::augmented_path(&front);
    let setup = service_update::run_streamed(
        service_update::setup_command(&repo, &args, path.as_ref()),
        service_update::SETUP_TIMEOUT,
        &mut log_line,
    )
    .await;

    let python = server_manager::find_python(&repo);
    let failure = match (&setup, &python) {
        (Err(e), _) => Some(tr!(
            "建环境脚本没有成功:\n{}",
            "The setup script didn't succeed:\n{}",
            e
        )),
        (Ok(()), None) => Some(
            t(
                "建环境脚本跑完了,但仓库里没有出现 .venv。",
                "The setup script finished, but no .venv appeared in the repository.",
            )
            .to_string(),
        ),
        (Ok(()), Some(_)) => None,
    };
    if let Some(why) = failure {
        // 为了建环境停掉的服务,失败了也要拉回来。
        let restored = service_update::restart_all(servers, cfg, &stopped, false).await;
        let mut msg = String::new();
        if cloned {
            msg.push_str(&tr!(
                "代码已经下载到 {}。",
                "The code was downloaded to {}. ",
                repo.display()
            ));
        }
        msg.push_str(&why);
        for r in restored {
            msg.push('\n');
            msg.push_str(&r);
        }
        msg.push_str(&tr!(
            "\n\n可以再点一次重试(已经下好的部分不会重下),或者在终端里手动运行:\n{}",
            "\n\nYou can click again to retry (what's already downloaded is kept), or run this in a terminal:\n{}",
            manual
        ));
        return Err(msg);
    }
    let python = python.unwrap_or_default();

    let mut message = if cloned {
        tr!(
            "代码已下载到 {},环境已建好。",
            "Code downloaded to {}; environment set up.",
            repo.display()
        )
    } else {
        tr!(
            "环境已建好({})。",
            "Environment set up ({}).",
            repo.display()
        )
    };
    if !stopped.is_empty() {
        emit("restarting", None);
        for r in service_update::restart_all(servers, cfg, &stopped, false).await {
            message.push('\n');
            message.push_str(&r);
        }
    }
    Ok(Outcome {
        repo_path: repo.display().to_string(),
        python_path: python,
        message,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(tag: &str) -> PathBuf {
        let p =
            std::env::temp_dir().join(format!("vif-local-setup-{}-{}", tag, std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    fn make_repo(dir: &Path) {
        std::fs::create_dir_all(dir.join("services")).unwrap();
        std::fs::write(dir.join("services/stt_server.py"), "").unwrap();
        std::fs::write(dir.join("services/llm_server.py"), "").unwrap();
    }

    #[test]
    fn target_states() {
        let root = tmp("states");
        assert_eq!(classify_target(&root.join("nope")), TargetState::Missing);
        let empty = root.join("empty");
        std::fs::create_dir_all(&empty).unwrap();
        assert_eq!(classify_target(&empty), TargetState::Empty);
        let other = root.join("other");
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(other.join("notes.txt"), "mine").unwrap();
        assert_eq!(classify_target(&other), TargetState::Occupied);
        // 是个文件而不是文件夹:也不碰。
        assert_eq!(
            classify_target(&other.join("notes.txt")),
            TargetState::Occupied
        );
        let repo = root.join("repo");
        make_repo(&repo);
        assert_eq!(classify_target(&repo), TargetState::Repo);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn plan_prefers_the_configured_repo_and_never_touches_other_folders() {
        let target = Path::new("/home/u/voice-input-framework");
        let configured = Path::new("/srv/vif");
        // 配置里有可用的仓库:哪怕目标目录被占着也没关系,根本不会去克隆。
        assert_eq!(
            plan(Some(configured), target, TargetState::Occupied),
            Ok(Plan::UseRepo(configured.to_path_buf()))
        );
        assert_eq!(
            plan(None, target, TargetState::Repo),
            Ok(Plan::UseRepo(target.to_path_buf()))
        );
        for state in [TargetState::Missing, TargetState::Empty] {
            assert_eq!(
                plan(None, target, state),
                Ok(Plan::Clone(target.to_path_buf()))
            );
        }
        let err = plan(None, target, TargetState::Occupied).unwrap_err();
        assert!(err.contains("/home/u/voice-input-framework"), "{err}");
    }

    #[test]
    fn default_target_is_where_auto_detect_looks_first() {
        assert_eq!(
            default_target(Path::new("/home/u")),
            PathBuf::from("/home/u/voice-input-framework")
        );
    }

    #[test]
    fn llm_flag_is_added_once_and_only_where_it_matters() {
        let none: Vec<String> = Vec::new();
        assert_eq!(with_llm(none.clone(), true, false, false), vec!["--llm"]);
        assert_eq!(with_llm(none.clone(), true, false, true), vec!["-Llm"]);
        // Apple Silicon 用 MLX。
        assert!(with_llm(none.clone(), true, true, false).is_empty());
        assert!(with_llm(none.clone(), false, false, false).is_empty());
        // 现有环境已经装了 LLM 依赖:不重复加;没要 LLM 也不把已有的去掉。
        let has = vec!["--backend".to_string(), "cuda".into(), "--llm".into()];
        assert_eq!(with_llm(has.clone(), true, false, false), has);
        assert_eq!(with_llm(has.clone(), false, false, false), has);
        let win = vec!["-Backend".to_string(), "cpu".into()];
        assert_eq!(
            with_llm(win, true, false, true),
            vec!["-Backend", "cpu", "-Llm"]
        );
    }

    #[test]
    fn git_hints_give_a_command_per_platform() {
        assert!(git_hint(Os::Windows).command.unwrap().contains("winget"));
        assert_eq!(
            git_hint(Os::Mac).command.as_deref(),
            Some("xcode-select --install")
        );
        assert!(git_hint(Os::Linux).command.unwrap().contains("git"));
    }

    #[test]
    fn manual_command_carries_the_args() {
        let repo = Path::new("/home/u/vif");
        assert_eq!(
            manual_command(repo, &["--llm".into()], false),
            "cd \"/home/u/vif\" && scripts/setup-env.sh --llm"
        );
        assert_eq!(
            manual_command(repo, &[], false),
            "cd \"/home/u/vif\" && scripts/setup-env.sh"
        );
    }

    // ── 真的克隆一次:远端是测试里现建的本地仓库,绝不碰网络 ──

    fn git_run(git: &Path, dir: &Path, args: &[&str]) {
        let out = std::process::Command::new(git)
            .args([
                "-c",
                "user.name=vif-test",
                "-c",
                "user.email=vif-test@example.com",
                "-c",
                "commit.gpgsign=false",
            ])
            .args(args)
            .current_dir(dir)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "git {:?}: {}",
            args,
            String::from_utf8_lossy(&out.stderr)
        );
    }

    #[tokio::test]
    async fn clone_fetches_the_project_and_rejects_anything_else() {
        let Some(git) = service_update::locate_git() else {
            eprintln!("no git on this machine; skipping");
            return;
        };
        let root = tmp("clone");
        // 一个长得像本项目的「远端」。
        let origin = root.join("origin");
        make_repo(&origin);
        git_run(&git, &origin, &["init", "-q", "-b", "main"]);
        git_run(&git, &origin, &["add", "."]);
        git_run(&git, &origin, &["commit", "-q", "-m", "first"]);

        let mut lines = Vec::new();
        let target = root.join("deep").join("vif");
        clone_repo(
            &git,
            &origin.display().to_string(),
            &target,
            &mut |l: &str| lines.push(l.to_string()),
        )
        .await
        .expect("clone");
        assert!(server_manager::is_repo_root(&target));
        assert_eq!(classify_target(&target), TargetState::Repo);

        // 不是本项目的仓库:克隆成功也要报错。
        let stranger = root.join("stranger");
        std::fs::create_dir_all(&stranger).unwrap();
        std::fs::write(stranger.join("README.md"), "hi").unwrap();
        git_run(&git, &stranger, &["init", "-q", "-b", "main"]);
        git_run(&git, &stranger, &["add", "."]);
        git_run(&git, &stranger, &["commit", "-q", "-m", "first"]);
        let err = clone_repo(
            &git,
            &stranger.display().to_string(),
            &root.join("wrong"),
            &mut |_: &str| {},
        )
        .await
        .unwrap_err();
        assert!(err.contains("services/stt_server.py"), "{err}");

        // 远端不存在:报错里带着 git 自己的话。
        let err = clone_repo(
            &git,
            &root.join("no-such-remote").display().to_string(),
            &root.join("never"),
            &mut |_: &str| {},
        )
        .await
        .unwrap_err();
        assert!(err.contains("git clone"), "{err}");
        assert!(!root.join("never").join("services").exists());
        let _ = std::fs::remove_dir_all(&root);
    }

    /// 真的走一遍「克隆 + 建环境」:会跑真正的 `setup-env`(uv sync,要联网、要几分钟、
    /// 占几个 GB),所以默认不跑。
    ///
    /// `VIF_E2E_SETUP_FROM=<本项目的一个 checkout>` 时才跑:从那个 checkout 克隆到
    /// 临时目录,在里面建环境,最后确认解释器起得来。只动临时目录。
    ///
    ///     VIF_E2E_SETUP_FROM=$PWD/../.. cargo test local_setup::tests::e2e -- --ignored --nocapture
    #[tokio::test]
    #[ignore = "会真的 git clone + uv sync,要联网和几分钟"]
    async fn e2e_clone_then_setup_env() {
        let Ok(from) = std::env::var("VIF_E2E_SETUP_FROM") else {
            eprintln!("set VIF_E2E_SETUP_FROM to a checkout of this project to run this");
            return;
        };
        let from = std::fs::canonicalize(from).unwrap();
        std::env::set_var("VIF_REPO_URL", from.display().to_string());
        let root = tmp("e2e");
        let target = root.join("voice-input-framework");
        let servers = Arc::new(Mutex::new(ServerManager::new(root.join("data"))));
        let cfg = ServerConfig {
            host: "localhost".into(),
            port: 6544,
            mode: crate::config::ServerMode::Local,
            local: crate::config::LocalServerConfig::default(),
            token: None,
        };
        assert!(configured_repo(&cfg).is_none());

        let stages = Mutex::new(Vec::<String>::new());
        let emit = |stage: &'static str, line: Option<String>| {
            if stage != "progress" {
                stages.lock().unwrap().push(stage.to_string());
            }
            eprintln!("[{stage}] {}", line.unwrap_or_default());
        };
        let want_llm = std::env::var("VIF_E2E_SETUP_LLM").is_ok();
        let outcome = run_inner(
            &emit,
            &servers,
            &cfg,
            Some(&target.display().to_string()),
            want_llm,
        )
        .await
        .expect("setup");
        assert_eq!(
            *stages.lock().unwrap(),
            vec!["checking", "cloning", "setup"]
        );
        assert_eq!(PathBuf::from(&outcome.repo_path), target);
        assert!(server_manager::is_repo_root(&target));
        let version = std::process::Command::new(&outcome.python_path)
            .args(["-c", "import fastapi, uvicorn, numpy; print('deps ok')"])
            .output()
            .expect("python runs");
        assert!(
            version.status.success(),
            "{}",
            String::from_utf8_lossy(&version.stderr)
        );

        // 再来一次:仓库和环境都在了,不克隆,只重跑建环境脚本。
        stages.lock().unwrap().clear();
        let again = run_inner(
            &emit,
            &servers,
            &cfg,
            Some(&target.display().to_string()),
            want_llm,
        )
        .await
        .expect("second run");
        assert_eq!(*stages.lock().unwrap(), vec!["checking", "setup"]);
        assert_eq!(again.python_path, outcome.python_path);
        let _ = std::fs::remove_dir_all(&root);
    }
}
