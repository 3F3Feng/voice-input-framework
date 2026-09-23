//! 本地管理模式的「环境体检」(F2)。
//!
//! 以前 GUI 只看「解释器文件在不在」(`server_manager::path_report`)。可环境坏掉的
//! 方式远不止这一种:uv 没装、Python 是 3.13、依赖没装全、Linux 上装了一个一 import
//! 就崩的 mlx、torch 压根没装……这些都要等点了「启动」、服务起不来、再去翻日志才
//! 看得出来。体检把它们一次查完,每一项都给出「哪里不对 + 怎么修」。
//!
//! 只起**一个** Python 进程:一段小脚本把版本、各包能否 import、加速后端一起打成
//! JSON。逐项各起一个进程的话,光是 import torch 就要重复好几秒。
//!
//! 结构上分两层:`check` 负责跑进程(有副作用),`build_report` / `parse_probe` /
//! `find_uv` 都是纯函数,单测覆盖。

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// 体检脚本的时限。import torch + transformers + mlx_audio 冷启动在慢盘上要十来秒,
/// 再往上基本就是卡住了(比如某个包 import 时去连网)。
const PROBE_TIMEOUT: Duration = Duration::from_secs(20);

/// 脚本输出里 JSON 那一行的前缀。有些库 import 时会往 stdout 打字,不能假设整个
/// stdout 就是一段 JSON。
const MARKER: &str = "VIF_ENV_JSON:";

/// 报告里依赖包的显示顺序(和脚本里检查的顺序一致)。
const PACKAGE_ORDER: &[&str] = &[
    "fastapi",
    "uvicorn",
    "numpy",
    "transformers",
    "torch",
    "mlx_audio",
    "mlx_whisper",
    "mlx",
];

/// 在目标解释器里跑的脚本。`sys.argv[1]` 是仓库路径(可能为空)。
///
/// 写法刻意保守(不用 f-string、每段各自 try):解释器版本不对恰恰是要查的问题之一,
/// 脚本自己要能在老版本上跑到把版本号报出来为止。
const PROBE_SCRIPT: &str = r#"
import sys, json, platform, importlib
out = {"version": platform.python_version(), "version_info": list(sys.version_info[:3]),
       "system": platform.system(), "machine": platform.machine(),
       "apple_silicon": sys.platform == "darwin" and platform.machine() == "arm64",
       "packages": {}, "backend": None, "backend_error": None, "nvidia_smi": False}
repo = sys.argv[1] if len(sys.argv) > 1 else ""

def dist_version(name):
    try:
        from importlib import metadata
        return metadata.version(name.replace("_", "-"))
    except BaseException:
        return ""

def probe(name):
    try:
        m = importlib.import_module(name)
        v = getattr(m, "__version__", "") or dist_version(name.split(".")[0])
        return {"ok": True, "version": str(v)}
    except BaseException as e:
        return {"ok": False, "error": ("%s: %s" % (type(e).__name__, e))[:300]}

try:
    import shutil
    out["nvidia_smi"] = shutil.which("nvidia-smi") is not None
except BaseException:
    pass

names = ["fastapi", "uvicorn", "numpy", "transformers", "torch"]
if out["apple_silicon"]:
    names += ["mlx_audio", "mlx_whisper"]
for n in names:
    out["packages"][n] = probe(n)

if not out["apple_silicon"]:
    try:
        import importlib.util
        if importlib.util.find_spec("mlx") is not None:
            out["packages"]["mlx"] = probe("mlx.core")
    except BaseException:
        pass

if out["packages"]["torch"]["ok"] and repo:
    try:
        sys.path.insert(0, repo)
        from services import device
        b = device.detect()
        out["backend"] = {"name": b.name, "detail": b.detail, "summary": None}
        try:
            out["backend"]["summary"] = device.describe()
        except BaseException:
            pass
    except BaseException as e:
        out["backend_error"] = ("%s: %s" % (type(e).__name__, e))[:300]

sys.stdout.write("\n" + "VIF_ENV_JSON:" + json.dumps(out) + "\n")
sys.stdout.flush()
"#;

// ── 报告 ──

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CheckStatus {
    Ok,
    Warn,
    Fail,
}

#[derive(Debug, Clone, Serialize)]
pub struct CheckItem {
    pub id: String,
    pub label: String,
    pub status: CheckStatus,
    /// 一行中文说明,直接显示。
    pub detail: String,
    /// 怎么修。`None` 表示没什么要做的。
    pub fix: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct EnvReport {
    pub items: Vec<CheckItem>,
    /// 没有任何一项 `Fail`。
    pub ok: bool,
    /// 一键重建环境的命令,体检不通过时给用户复制。
    pub setup_command: String,
}

// ── 脚本输出 ──

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct Probe {
    pub version: String,
    pub version_info: Vec<u32>,
    pub system: String,
    pub machine: String,
    pub apple_silicon: bool,
    pub packages: BTreeMap<String, PkgProbe>,
    pub backend: Option<BackendProbe>,
    pub backend_error: Option<String>,
    pub nvidia_smi: bool,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct PkgProbe {
    pub ok: bool,
    pub version: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct BackendProbe {
    pub name: String,
    pub detail: String,
    pub summary: Option<String>,
}

/// 解释器那一步的结果。
#[derive(Debug, Clone)]
pub enum ProbeOutcome {
    /// 配置里没填解释器。
    NotConfigured,
    /// 填了,但文件不存在。
    Missing(String),
    /// 进程起不来(没有执行权限、不是可执行文件……)。
    SpawnFailed(String),
    TimedOut(u64),
    /// 跑完了但没吐出 JSON:通常是解释器本身坏了,或者版本老到脚本都跑不了。
    BadOutput {
        code: Option<i32>,
        stderr_tail: String,
    },
    Ran(Probe),
}

/// 从 stdout 里找出最后一个 JSON 行。
pub fn parse_probe(stdout: &str) -> Result<Probe, String> {
    let line = stdout
        .lines()
        .rev()
        .find_map(|l| l.trim().strip_prefix(MARKER))
        .ok_or("体检脚本没有输出结果")?;
    serde_json::from_str(line).map_err(|e| format!("体检结果解析失败:{e}"))
}

/// 最后几行 stderr,用来说明解释器为什么没跑起来。整段贴出来太长,Python 的
/// traceback 关键信息总在最后。
fn tail(text: &str, lines: usize) -> String {
    let all: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    all[all.len().saturating_sub(lines)..].join(" / ")
}

/// 3.11 / 3.12,与 `shared/version_check.py` 和 pyproject 的 `requires-python` 一致。
pub fn version_supported(v: &[u32]) -> bool {
    matches!(v, [3, 11 | 12, ..])
}

/// 一键重建环境的命令。仓库路径已知时带上 `cd`,用户复制到任意终端都能直接跑。
pub fn setup_command(repo: Option<&str>, windows: bool) -> String {
    match (repo, windows) {
        (Some(r), true) => format!(
            "powershell -ExecutionPolicy Bypass -File \"{}\"",
            Path::new(r).join("scripts").join("setup-env.ps1").display()
        ),
        (None, true) => "powershell -ExecutionPolicy Bypass -File scripts\\setup-env.ps1".into(),
        (Some(r), false) => format!("cd \"{r}\" && scripts/setup-env.sh"),
        (None, false) => "scripts/setup-env.sh".into(),
    }
}

/// 找 uv。
///
/// 只查 PATH 不够:macOS 上从 Finder / 启动台打开的应用,PATH 只有
/// `/usr/bin:/bin:/usr/sbin:/sbin`,用户终端里的 `~/.local/bin`(uv 安装脚本的默认
/// 位置)根本不在里面,只查 PATH 会把装好了的 uv 报成没装。
pub fn find_uv(
    path_var: Option<&std::ffi::OsStr>,
    home: Option<&Path>,
    windows: bool,
    exists: impl Fn(&Path) -> bool,
) -> Option<PathBuf> {
    let exe = if windows { "uv.exe" } else { "uv" };
    let mut dirs: Vec<PathBuf> = path_var
        .map(|p| std::env::split_paths(p).collect())
        .unwrap_or_default();
    if let Some(h) = home {
        dirs.push(h.join(".local").join("bin"));
        dirs.push(h.join(".cargo").join("bin"));
    }
    if !windows {
        dirs.push("/opt/homebrew/bin".into());
        dirs.push("/usr/local/bin".into());
    }
    dirs.into_iter().map(|d| d.join(exe)).find(|p| exists(p))
}

/// 体检的全部输入。副作用(跑进程、查文件)都在 `check` 里做完,这里只剩判断。
pub struct ReportInput {
    pub repo_path: Option<String>,
    pub repo_ok: bool,
    pub probe: ProbeOutcome,
    pub uv: Option<PathBuf>,
    pub windows: bool,
    /// 宿主机是不是 Apple Silicon。用来认出「经 Rosetta 跑的 x86 Python」。
    pub host_apple_silicon: bool,
}

fn item(
    id: &str,
    label: &str,
    status: CheckStatus,
    detail: String,
    fix: Option<String>,
) -> CheckItem {
    CheckItem {
        id: id.into(),
        label: label.into(),
        status,
        detail,
        fix,
    }
}

const REBUILD: &str = "运行下方的建环境命令重建环境";

pub fn build_report(input: ReportInput) -> EnvReport {
    use CheckStatus::*;
    let mut items = Vec::new();
    let repo = input
        .repo_path
        .as_deref()
        .map(str::trim)
        .filter(|r| !r.is_empty());

    // ── 仓库 ──
    items.push(match (repo, input.repo_ok) {
        (None, _) => item(
            "repo",
            "仓库路径",
            Fail,
            "没有设置仓库路径".into(),
            Some("点「自动探测」,或手动填写 voice-input-framework 仓库的位置".into()),
        ),
        (Some(r), false) => item(
            "repo",
            "仓库路径",
            Fail,
            format!("{r} 下找不到 services/stt_server.py"),
            Some("确认填的是仓库根目录(里面有 services、scripts 两个文件夹)".into()),
        ),
        (Some(r), true) => item("repo", "仓库路径", Ok, r.to_string(), None),
    });

    // ── 解释器 ──
    let venv_hint = if input.windows {
        ".venv\\Scripts\\python.exe"
    } else {
        ".venv/bin/python"
    };
    let rebuild_then_detect = format!("{REBUILD},建好后点「自动探测」选中仓库里的 {venv_hint}");
    let probe = match &input.probe {
        ProbeOutcome::Ran(p) => Some(p),
        _ => None,
    };
    items.push(match &input.probe {
        ProbeOutcome::NotConfigured => item(
            "python",
            "Python 解释器",
            Fail,
            "没有设置 Python 解释器路径".into(),
            Some(rebuild_then_detect.clone()),
        ),
        ProbeOutcome::Missing(p) => item(
            "python",
            "Python 解释器",
            Fail,
            format!("解释器不存在:{p}"),
            Some(rebuild_then_detect.clone()),
        ),
        ProbeOutcome::SpawnFailed(e) => item(
            "python",
            "Python 解释器",
            Fail,
            format!("解释器运行不起来:{e}"),
            Some(rebuild_then_detect.clone()),
        ),
        ProbeOutcome::TimedOut(s) => item(
            "python",
            "Python 解释器",
            Fail,
            format!("体检脚本 {s} 秒内没有跑完(导入依赖卡住了)"),
            Some("先确认磁盘和网络正常再点一次;反复超时就重建环境".into()),
        ),
        ProbeOutcome::BadOutput { code, stderr_tail } => {
            let code = code.map_or("被信号终止".to_string(), |c| format!("退出码 {c}"));
            let why = if stderr_tail.is_empty() {
                String::new()
            } else {
                format!(":{stderr_tail}")
            };
            item(
                "python",
                "Python 解释器",
                Fail,
                format!("解释器能启动,但体检脚本没跑完({code}){why}"),
                Some(rebuild_then_detect.clone()),
            )
        }
        ProbeOutcome::Ran(p) if !version_supported(&p.version_info) => item(
            "python",
            "Python 解释器",
            Fail,
            format!("Python {},需要 3.11 或 3.12", p.version),
            Some(format!(
                "依赖(numpy 1.x / mlx / torch)还没有这个版本的预编译包。{REBUILD}(uv 会自动准备 3.12)"
            )),
        ),
        // 在 Apple Silicon 上跑的是 x86_64 的 Python:经 Rosetta 转译,能跑,但
        // pyproject 里的 MLX 依赖按平台 marker 不会装,MLX 模型全都用不了。
        ProbeOutcome::Ran(p) if input.host_apple_silicon && p.machine == "x86_64" => item(
            "python",
            "Python 解释器",
            Warn,
            format!("Python {}(x86_64,经 Rosetta 转译运行)", p.version),
            Some(format!("MLX 模型用不了,推理也慢。{REBUILD}(会装 arm64 的 Python)")),
        ),
        ProbeOutcome::Ran(p) => item(
            "python",
            "Python 解释器",
            Ok,
            format!("Python {}({})", p.version, p.machine),
            None,
        ),
    });

    // ── 依赖包 ──
    // 解释器没跑起来时一行带过,不必把每个包都列成「不知道」。
    match probe {
        None => items.push(item(
            "packages",
            "依赖包",
            Warn,
            "解释器没跑起来,没法检查".into(),
            None,
        )),
        Some(p) => {
            // JSON 对象进来就按字母序了;按「基础 → 推理 → MLX」排回去,读起来顺。
            let rank = |n: &str| {
                PACKAGE_ORDER
                    .iter()
                    .position(|o| *o == n)
                    .unwrap_or(usize::MAX)
            };
            let mut pkgs: Vec<_> = p.packages.iter().collect();
            pkgs.sort_by_key(|(n, _)| rank(n));
            for (name, pkg) in pkgs {
                items.push(package_item(name, pkg, p));
            }
        }
    }

    // ── 加速后端 ──
    items.push(backend_item(probe, repo.is_some() && input.repo_ok));

    // ── uv ──
    items.push(match &input.uv {
        Some(p) => item("uv", "uv", Ok, p.display().to_string(), None),
        // 只是「warn」:服务跑起来并不需要 uv,只有建 / 重建环境时要用,而建环境
        // 脚本自己会装它。
        None => item(
            "uv",
            "uv",
            Warn,
            "没找到 uv(建环境要用,服务运行本身不需要)".into(),
            Some("建环境脚本发现没有 uv 会自动安装".into()),
        ),
    });

    let ok = !items.iter().any(|i| i.status == Fail);
    EnvReport {
        items,
        ok,
        setup_command: setup_command(repo, input.windows),
    }
}

fn package_item(name: &str, pkg: &PkgProbe, p: &Probe) -> CheckItem {
    use CheckStatus::*;
    let id = format!("pkg:{name}");
    let version = pkg.version.clone().unwrap_or_default();
    let error = pkg.error.clone().unwrap_or_default();

    // 非 Apple 平台上装了 mlx:pyproject 用平台 marker 把它排除掉了,出现在这里
    // 多半是照着旧的 requirements-stt.txt 装的。Linux x86_64 的 mlx wheel 是残的,
    // 一 import 就报 libmlx.so 找不到。它不妨碍非 MLX 模型,所以只算 warn。
    if name == "mlx" {
        return if pkg.ok {
            item(&id, "mlx", Ok, format!("{version}(这个平台上用不到)"), None)
        } else {
            item(
                &id,
                "mlx",
                Warn,
                format!("装了一个用不了的 mlx:{error}"),
                Some(format!(
                    "多半是照着旧的 requirements-stt.txt 装的;不影响非 MLX 模型,但建议{REBUILD}"
                )),
            )
        };
    }

    if !pkg.ok {
        let fix = match name {
            "torch" if !p.apple_silicon => {
                "torch 要按显卡选版本,建环境脚本会自动探测(也可以加 --backend cpu / cuda 指定)"
                    .to_string()
            }
            "mlx_audio" | "mlx_whisper" => format!("Apple Silicon 上的 MLX 模型要用它。{REBUILD}"),
            _ => format!("依赖没装全。{REBUILD}"),
        };
        return item(&id, name, Fail, format!("无法导入:{error}"), Some(fix));
    }

    // numpy 2.x 能 import,但和为 1.x 编译的二进制包冲突(`_ARRAY_API not found`),
    // 到加载模型时才炸。pyproject 锁的是 <2.0。
    if name == "numpy"
        && version
            .split('.')
            .next()
            .and_then(|m| m.parse::<u32>().ok())
            >= Some(2)
    {
        return item(
            &id,
            name,
            Warn,
            format!("{version}:需要 1.x,2.x 会和部分二进制包冲突"),
            Some(format!("{REBUILD}(会装回 numpy 1.26)")),
        );
    }
    item(&id, name, Ok, version, None)
}

fn backend_item(probe: Option<&Probe>, repo_ok: bool) -> CheckItem {
    use CheckStatus::*;
    let skip = |why: &str| item("backend", "加速后端", Warn, why.to_string(), None);
    let Some(p) = probe else {
        return skip("解释器没跑起来,没法判断");
    };
    if !p.packages.get("torch").is_some_and(|t| t.ok) {
        return skip("没有 torch,没法判断");
    }
    if !repo_ok {
        return skip("仓库路径无效,读不到 services/device.py");
    }
    let Some(b) = &p.backend else {
        return item(
            "backend",
            "加速后端",
            Warn,
            format!(
                "判断失败:{}",
                p.backend_error.as_deref().unwrap_or("没有结果")
            ),
            None,
        );
    };
    let detail = b.summary.clone().unwrap_or_else(|| b.detail.clone());
    if b.name == "cpu" && p.nvidia_smi {
        return item(
            "backend",
            "加速后端",
            Warn,
            format!("有 NVIDIA 显卡,但装的是 CPU 版 torch:{detail}"),
            Some(format!(
                "{REBUILD},并加上 --backend cuda(Windows 上是 -Backend cuda)"
            )),
        );
    }
    if b.name == "cpu" && p.apple_silicon {
        return item(
            "backend",
            "加速后端",
            Warn,
            format!("Apple Silicon 上 MPS 不可用,只能用 CPU:{detail}"),
            Some(format!("torch 可能太旧。{REBUILD}")),
        );
    }
    item("backend", "加速后端", Ok, detail, None)
}

// ── 跑起来 ──

/// 跑体检脚本。
async fn run_probe(python: &str, repo: Option<&str>) -> ProbeOutcome {
    let mut std_cmd = std::process::Command::new(python);
    std_cmd
        .arg("-c")
        .arg(PROBE_SCRIPT)
        .arg(repo.unwrap_or(""))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    if let Some(r) = repo {
        std_cmd.current_dir(r);
    }
    // Windows 上不弹黑色控制台窗口(和拉起服务时一样)。
    crate::server_manager::no_console(&mut std_cmd);
    let mut cmd = tokio::process::Command::from(std_cmd);
    // 超时后 future 被丢弃,子进程跟着被杀,不留一个卡在 import 里的 Python。
    cmd.kill_on_drop(true);

    match tokio::time::timeout(PROBE_TIMEOUT, cmd.output()).await {
        Err(_) => ProbeOutcome::TimedOut(PROBE_TIMEOUT.as_secs()),
        Ok(Err(e)) => ProbeOutcome::SpawnFailed(e.to_string()),
        Ok(Ok(out)) => {
            let stdout = String::from_utf8_lossy(&out.stdout);
            match parse_probe(&stdout) {
                Ok(p) => ProbeOutcome::Ran(p),
                Err(_) => ProbeOutcome::BadOutput {
                    code: out.status.code(),
                    stderr_tail: tail(&String::from_utf8_lossy(&out.stderr), 3),
                },
            }
        }
    }
}

/// 体检入口:按配置里的仓库 / 解释器查一遍。
pub async fn check(local: &crate::config::LocalServerConfig) -> EnvReport {
    let repo_path = local.repo_path.clone();
    let repo_ok = repo_path
        .as_deref()
        .is_some_and(|p| crate::server_manager::is_repo_root(Path::new(p)));
    let python = local
        .python_path
        .as_deref()
        .map(str::trim)
        .filter(|p| !p.is_empty());

    let probe = match python {
        None => ProbeOutcome::NotConfigured,
        Some(p) if !Path::new(p).exists() => ProbeOutcome::Missing(p.to_string()),
        Some(p) => run_probe(p, repo_path.as_deref().filter(|_| repo_ok)).await,
    };

    let home = crate::server_manager::home_dir();
    let uv = find_uv(
        std::env::var_os("PATH").as_deref(),
        home.as_deref(),
        cfg!(windows),
        |p| p.is_file(),
    );

    build_report(ReportInput {
        repo_path,
        repo_ok,
        probe,
        uv,
        windows: cfg!(windows),
        host_apple_silicon: cfg!(all(target_os = "macos", target_arch = "aarch64")),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pkg_ok(v: &str) -> PkgProbe {
        PkgProbe {
            ok: true,
            version: Some(v.into()),
            error: None,
        }
    }

    fn pkg_err(e: &str) -> PkgProbe {
        PkgProbe {
            ok: false,
            version: None,
            error: Some(e.into()),
        }
    }

    /// 一个健康的 Linux + CUDA 环境。
    fn healthy_probe() -> Probe {
        let mut packages = BTreeMap::new();
        for (n, v) in [
            ("fastapi", "0.110.0"),
            ("uvicorn", "0.29.0"),
            ("numpy", "1.26.4"),
            ("transformers", "4.40.0"),
            ("torch", "2.3.0"),
        ] {
            packages.insert(n.to_string(), pkg_ok(v));
        }
        Probe {
            version: "3.12.4".into(),
            version_info: vec![3, 12, 4],
            system: "Linux".into(),
            machine: "x86_64".into(),
            apple_silicon: false,
            packages,
            backend: Some(BackendProbe {
                name: "cuda".into(),
                detail: "NVIDIA GPU (CUDA 12.4)".into(),
                summary: None,
            }),
            backend_error: None,
            nvidia_smi: true,
        }
    }

    fn input(probe: ProbeOutcome) -> ReportInput {
        ReportInput {
            repo_path: Some("/home/u/voice-input-framework".into()),
            repo_ok: true,
            probe,
            uv: Some("/home/u/.local/bin/uv".into()),
            windows: false,
            host_apple_silicon: false,
        }
    }

    fn get<'a>(r: &'a EnvReport, id: &str) -> &'a CheckItem {
        r.items
            .iter()
            .find(|i| i.id == id)
            .unwrap_or_else(|| panic!("缺少 {id}"))
    }

    #[test]
    fn a_healthy_environment_passes() {
        let r = build_report(input(ProbeOutcome::Ran(healthy_probe())));
        assert!(r.ok);
        assert!(
            r.items.iter().all(|i| i.status == CheckStatus::Ok),
            "{r:#?}"
        );
        assert_eq!(get(&r, "backend").detail, "NVIDIA GPU (CUDA 12.4)");
        // 依赖按「基础 → 推理」排,而不是 JSON 进来的字母序。
        let pkgs: Vec<&str> = r
            .items
            .iter()
            .filter_map(|i| i.id.strip_prefix("pkg:"))
            .collect();
        assert_eq!(
            pkgs,
            ["fastapi", "uvicorn", "numpy", "transformers", "torch"]
        );
    }

    #[test]
    fn python_313_is_a_failure_with_a_fix() {
        let mut p = healthy_probe();
        p.version = "3.13.1".into();
        p.version_info = vec![3, 13, 1];
        let r = build_report(input(ProbeOutcome::Ran(p)));
        assert!(!r.ok);
        let py = get(&r, "python");
        assert_eq!(py.status, CheckStatus::Fail);
        assert!(py.detail.contains("3.13.1"));
        assert!(py.fix.is_some());
    }

    #[test]
    fn supported_versions_follow_version_check_py() {
        assert!(version_supported(&[3, 11, 0]));
        assert!(version_supported(&[3, 12, 9]));
        assert!(!version_supported(&[3, 10, 14]));
        assert!(!version_supported(&[3, 13, 0]));
        assert!(!version_supported(&[]));
    }

    #[test]
    fn a_missing_package_fails_and_torch_gets_a_backend_hint() {
        let mut p = healthy_probe();
        p.packages.insert(
            "torch".into(),
            pkg_err("ModuleNotFoundError: No module named 'torch'"),
        );
        p.backend = None;
        let r = build_report(input(ProbeOutcome::Ran(p)));
        assert!(!r.ok);
        let t = get(&r, "pkg:torch");
        assert_eq!(t.status, CheckStatus::Fail);
        assert!(t.detail.contains("No module named"));
        assert!(t.fix.as_deref().unwrap().contains("--backend"));
        // 没有 torch 时后端判断不了,但不重复报成失败。
        assert_eq!(get(&r, "backend").status, CheckStatus::Warn);
    }

    #[test]
    fn a_broken_mlx_on_linux_is_a_warning_not_a_failure() {
        let mut p = healthy_probe();
        p.packages.insert(
            "mlx".into(),
            pkg_err("ImportError: libmlx.so: cannot open shared object file"),
        );
        let r = build_report(input(ProbeOutcome::Ran(p)));
        assert!(r.ok);
        let m = get(&r, "pkg:mlx");
        assert_eq!(m.status, CheckStatus::Warn);
        assert!(m.detail.contains("libmlx.so"));
    }

    #[test]
    fn numpy_2_is_flagged() {
        let mut p = healthy_probe();
        p.packages.insert("numpy".into(), pkg_ok("2.1.0"));
        let r = build_report(input(ProbeOutcome::Ran(p)));
        assert_eq!(get(&r, "pkg:numpy").status, CheckStatus::Warn);
    }

    #[test]
    fn cpu_torch_on_an_nvidia_machine_is_a_warning() {
        let mut p = healthy_probe();
        p.backend = Some(BackendProbe {
            name: "cpu".into(),
            detail: "CPU（8 线程）".into(),
            summary: None,
        });
        let r = build_report(input(ProbeOutcome::Ran(p.clone())));
        let b = get(&r, "backend");
        assert_eq!(b.status, CheckStatus::Warn);
        assert!(b.fix.as_deref().unwrap().contains("cuda"));
        // 没有 N 卡的机器用 CPU 是正常的。
        p.nvidia_smi = false;
        let r = build_report(input(ProbeOutcome::Ran(p)));
        assert_eq!(get(&r, "backend").status, CheckStatus::Ok);
    }

    #[test]
    fn rosetta_python_on_apple_silicon_is_a_warning() {
        let mut p = healthy_probe();
        p.system = "Darwin".into();
        p.nvidia_smi = false;
        let mut i = input(ProbeOutcome::Ran(p));
        i.host_apple_silicon = true;
        let r = build_report(i);
        assert_eq!(get(&r, "python").status, CheckStatus::Warn);
    }

    #[test]
    fn a_missing_interpreter_collapses_the_dependent_checks() {
        let r = build_report(input(ProbeOutcome::Missing("/nope/python".into())));
        assert!(!r.ok);
        assert!(get(&r, "python").detail.contains("/nope/python"));
        assert_eq!(get(&r, "packages").status, CheckStatus::Warn);
        assert_eq!(get(&r, "backend").status, CheckStatus::Warn);
        assert!(!r.items.iter().any(|i| i.id.starts_with("pkg:")));
    }

    #[test]
    fn timeout_and_bad_output_are_explained() {
        let r = build_report(input(ProbeOutcome::TimedOut(20)));
        assert!(get(&r, "python").detail.contains("20 秒"));
        let r = build_report(input(ProbeOutcome::BadOutput {
            code: Some(1),
            stderr_tail: "SyntaxError: invalid syntax".into(),
        }));
        let py = get(&r, "python");
        assert!(py.detail.contains("退出码 1"));
        assert!(py.detail.contains("SyntaxError"));
    }

    #[test]
    fn missing_repo_fails_and_uv_missing_only_warns() {
        let mut i = input(ProbeOutcome::NotConfigured);
        i.repo_path = None;
        i.repo_ok = false;
        i.uv = None;
        let r = build_report(i);
        assert_eq!(get(&r, "repo").status, CheckStatus::Fail);
        assert_eq!(get(&r, "uv").status, CheckStatus::Warn);
        assert_eq!(r.setup_command, "scripts/setup-env.sh");
    }

    #[test]
    fn setup_command_matches_the_platform() {
        assert_eq!(
            setup_command(Some("/home/u/vif"), false),
            "cd \"/home/u/vif\" && scripts/setup-env.sh"
        );
        let win = setup_command(Some("C:\\vif"), true);
        assert!(win.starts_with("powershell -ExecutionPolicy Bypass -File"));
        assert!(win.contains("setup-env.ps1"));
    }

    #[test]
    fn parse_skips_noise_and_takes_the_marker_line() {
        let stdout = "some library banner\nVIF_ENV_JSON:{\"version\":\"3.12.1\",\"version_info\":[3,12,1],\"packages\":{\"torch\":{\"ok\":false,\"error\":\"x\"}}}\n";
        let p = parse_probe(stdout).unwrap();
        assert_eq!(p.version_info, vec![3, 12, 1]);
        assert!(!p.packages["torch"].ok);
        assert!(parse_probe("Traceback ...").is_err());
    }

    #[test]
    fn uv_is_found_outside_the_gui_path() {
        let home = Path::new("/Users/u");
        let path = std::env::join_paths(["/usr/bin", "/bin"]).unwrap();
        let found = find_uv(Some(path.as_os_str()), Some(home), false, |p| {
            p == Path::new("/Users/u/.local/bin/uv")
        });
        assert_eq!(found, Some(PathBuf::from("/Users/u/.local/bin/uv")));
        assert_eq!(find_uv(None, None, false, |_| false), None);
    }

    #[test]
    fn the_script_prints_the_marker_the_parser_looks_for() {
        assert!(PROBE_SCRIPT.contains(MARKER));
    }

    #[test]
    fn stderr_tail_keeps_the_last_lines() {
        assert_eq!(tail("a\n\nb\nc\nd\n", 2), "c / d");
        assert_eq!(tail("", 3), "");
    }

    /// 脚本本身要能在真解释器上跑通(不然纯函数测得再全也没用)。只要求输出能解析、
    /// 版本号对得上;装没装依赖因机器而异,不作断言。
    #[tokio::test]
    async fn probe_script_runs_on_a_real_interpreter() {
        let Some(py) = [
            "/usr/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
        ]
        .into_iter()
        .find(|p| Path::new(p).exists()) else {
            eprintln!("没有系统 python3,跳过");
            return;
        };
        match run_probe(py, None).await {
            ProbeOutcome::Ran(p) => {
                assert_eq!(p.version_info.first(), Some(&3));
                assert!(p.packages.contains_key("torch"));
            }
            other => panic!("{py} 上体检脚本没跑通:{other:?}"),
        }
    }

    #[tokio::test]
    async fn a_nonexistent_interpreter_is_reported_as_missing() {
        let local = crate::config::LocalServerConfig {
            python_path: Some("/nonexistent/bin/python".into()),
            ..Default::default()
        };
        let r = check(&local).await;
        assert!(!r.ok);
        assert!(get(&r, "python").detail.contains("/nonexistent/bin/python"));
    }

    /// 手动跑:对指定的仓库 / 解释器打印完整报告。
    /// `VIF_CHECK_REPO=... VIF_CHECK_PYTHON=... cargo test print_report -- --ignored --nocapture`
    #[tokio::test]
    #[ignore]
    async fn print_report() {
        let local = crate::config::LocalServerConfig {
            repo_path: std::env::var("VIF_CHECK_REPO").ok(),
            python_path: std::env::var("VIF_CHECK_PYTHON").ok(),
            ..Default::default()
        };
        let t = std::time::Instant::now();
        let r = check(&local).await;
        println!("{}", serde_json::to_string_pretty(&r).unwrap());
        println!("耗时 {:?}", t.elapsed());
    }
}
