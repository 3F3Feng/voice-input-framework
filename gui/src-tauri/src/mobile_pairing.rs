//! 手机配对:生成一个二维码,手机上的「语音输入」扫一下就把服务地址填好。
//!
//! 手机要连回这台电脑上的 STT 服务,需要一个它够得着的 HTTPS 地址。用 Tailscale 的话,
//! 那就是 `tailscale serve` 把本机的 STT 端口转成 `https://<本机名>.<tailnet>.ts.net[:端口]`
//! (只在 tailnet 内可见)。这里读出那个地址,拼成 `voiceinput://setup?url=…` 链接,画成二维码;
//! 还没转发时,由用户点按钮才执行 `tailscale serve`——绝不在用户不知道的时候改 Tailscale 的配置。
//!
//! 只在「本地管理模式」下有意义:远程模式的服务不在这台电脑上。
//! 本地模式的服务是本应用起的,不设 `VIF_API_TOKEN`,所以链接里不带令牌。
//! 手机端一定会先弹窗让用户确认地址,链接本身不会直接改任何设置。

use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use std::time::Duration;

use crate::tr;

/// 依次尝试的 HTTPS 端口。443 常被别的转发占着(比如别的服务),所以从 8443 起。
const CANDIDATE_PORTS: [u16; 4] = [8443, 8444, 8445, 8446];
/// 单条 tailscale 命令最多等多久。`serve` 在没开 HTTPS 时可能一直等着人去网页上开。
const COMMAND_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PairingStatus {
    /// 地址、链接、二维码都有了。
    Ready,
    /// Tailscale 在线,但还没把 STT 服务转成 HTTPS。`command` 是点按钮会执行的命令。
    NeedsServe,
    /// 没找到 tailscale 命令行。
    NoTailscale,
    /// Tailscale 没登录 / 没连上。
    NotRunning,
    /// 不是本地管理模式。
    NotLocal,
}

/// 给前端的整包结果。文案已按界面语言写好。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct PairingInfo {
    pub status: PairingStatus,
    pub address: Option<String>,
    pub link: Option<String>,
    /// 二维码,SVG 文本。
    pub qr_svg: Option<String>,
    /// 不是 `Ready` 时的原因,或出错的详情。
    pub message: Option<String>,
    /// `NeedsServe` 时点按钮会执行的完整命令,展示给用户看。
    pub command: Option<String>,
}

impl PairingInfo {
    fn bare(status: PairingStatus, message: String) -> Self {
        PairingInfo {
            status,
            address: None,
            link: None,
            qr_svg: None,
            message: Some(message),
            command: None,
        }
    }
}

// ── 纯逻辑(不碰进程,好测)──

/// 从 `tailscale status --json` 取本机的 MagicDNS 名字。没登录 / 没连上时返回 `Err(原因)`。
fn self_dns_name(status: &Value) -> Result<String, String> {
    let state = status
        .get("BackendState")
        .and_then(Value::as_str)
        .unwrap_or("");
    if state != "Running" {
        return Err(state.to_string());
    }
    let name = status
        .pointer("/Self/DNSName")
        .and_then(Value::as_str)
        .map(|s| s.trim_end_matches('.').to_string())
        .filter(|s| !s.is_empty());
    name.ok_or_else(|| "no DNSName".to_string())
}

/// `serve status` 里的 Proxy 是不是指向本机的 `port`。只认回环地址:转发到别的机器的不算。
fn proxies_to_local_port(proxy: &str, port: u16) -> bool {
    let rest = proxy.split_once("://").map(|(_, r)| r).unwrap_or(proxy);
    let authority = rest.split('/').next().unwrap_or("");
    let Some((host, p)) = authority.rsplit_once(':') else {
        return false;
    };
    matches!(host, "127.0.0.1" | "localhost" | "[::1]") && p.parse::<u16>().ok() == Some(port)
}

/// 找 `tailscale serve` 里已经把本机 `stt_port` 转成 HTTPS 的那条,返回它的地址。
///
/// 必须是 HTTPS(`TCP.<端口>.HTTPS == true`):手机端只认 `https://`,明文的转发不算。
fn find_serve_address(serve: &Value, stt_port: u16) -> Option<String> {
    let web = serve.get("Web")?.as_object()?;
    let mut keys: Vec<&String> = web.keys().collect();
    keys.sort(); // 键形如 "host:8443";排序让结果稳定
    for key in keys {
        let (host, port) = key.rsplit_once(':')?;
        let Ok(port_num) = port.parse::<u16>() else {
            continue;
        };
        let https = serve
            .pointer(&format!("/TCP/{port_num}/HTTPS"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
        if !https {
            continue;
        }
        let Some(handlers) = web[key].get("Handlers").and_then(Value::as_object) else {
            continue;
        };
        let hit = handlers.values().any(|h| {
            h.get("Proxy")
                .and_then(Value::as_str)
                .is_some_and(|p| proxies_to_local_port(p, stt_port))
        });
        if hit {
            // 443 是 https 的默认端口,不写。
            return Some(if port_num == 443 {
                format!("https://{host}")
            } else {
                format!("https://{host}:{port_num}")
            });
        }
    }
    None
}

/// `serve` 已经占用的端口(不管是 HTTPS 还是 TCP 转发)。
fn used_ports(serve: &Value) -> Vec<u16> {
    serve
        .get("TCP")
        .and_then(Value::as_object)
        .map(|m| m.keys().filter_map(|k| k.parse().ok()).collect())
        .unwrap_or_default()
}

fn pick_free_port(used: &[u16]) -> Option<u16> {
    CANDIDATE_PORTS.into_iter().find(|p| !used.contains(p))
}

/// 百分号编码:只放行 RFC 3986 的 unreserved 字符。
fn percent_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 3);
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn build_link(address: &str) -> String {
    format!("voiceinput://setup?url={}", percent_encode(address))
}

fn qr_svg(link: &str) -> Result<String, String> {
    use qrcode::render::svg;
    let code = qrcode::QrCode::with_error_correction_level(link.as_bytes(), qrcode::EcLevel::M)
        .map_err(|e| e.to_string())?;
    let svg = code
        .render::<svg::Color>()
        .min_dimensions(220, 220)
        .quiet_zone(true)
        .dark_color(svg::Color("#000000"))
        .light_color(svg::Color("#ffffff"))
        .build();
    // 去掉开头的 `<?xml …?>`:这段 SVG 会经 innerHTML 放进网页,HTML 里它只会变成一条注释。
    Ok(svg
        .find("<svg")
        .map_or(svg.clone(), |i| svg[i..].to_string()))
}

fn ready(address: String) -> PairingInfo {
    let link = build_link(&address);
    match qr_svg(&link) {
        Ok(svg) => PairingInfo {
            status: PairingStatus::Ready,
            address: Some(address),
            link: Some(link),
            qr_svg: Some(svg),
            message: None,
            command: None,
        },
        // 二维码画不出来(链接不会长到那个程度)时,链接本身还能复制过去用。
        Err(e) => PairingInfo {
            status: PairingStatus::Ready,
            address: Some(address),
            link: Some(link),
            qr_svg: None,
            message: Some(tr!(
                "二维码生成失败:{}",
                "Couldn't generate the QR code: {}",
                e
            )),
            command: None,
        },
    }
}

// ── 调 tailscale ──

fn find_tailscale() -> Option<PathBuf> {
    let fixed: &[&str] = if cfg!(target_os = "macos") {
        &[
            "/usr/local/bin/tailscale",
            "/opt/homebrew/bin/tailscale",
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        ]
    } else if cfg!(windows) {
        &[
            "C:\\Program Files\\Tailscale\\tailscale.exe",
            "C:\\Program Files (x86)\\Tailscale\\tailscale.exe",
        ]
    } else {
        &[
            "/usr/bin/tailscale",
            "/usr/local/bin/tailscale",
            "/usr/sbin/tailscale",
        ]
    };
    if let Some(p) = fixed.iter().map(PathBuf::from).find(|p| p.is_file()) {
        return Some(p);
    }
    // 从图形界面启动的应用 PATH 很短,所以上面的固定位置要先试;都没有再看 PATH。
    let name = if cfg!(windows) {
        "tailscale.exe"
    } else {
        "tailscale"
    };
    std::env::var_os("PATH")
        .into_iter()
        .flat_map(|paths| std::env::split_paths(&paths).collect::<Vec<_>>())
        .map(|dir| dir.join(name))
        .find(|p| p.is_file())
}

async fn run_tailscale(bin: &PathBuf, args: &[&str]) -> Result<String, String> {
    let mut std_cmd = std::process::Command::new(bin);
    std_cmd.args(args).stdin(std::process::Stdio::null());
    crate::server_manager::no_console(&mut std_cmd);
    let mut cmd = tokio::process::Command::from(std_cmd);
    cmd.kill_on_drop(true);

    let output = tokio::time::timeout(COMMAND_TIMEOUT, cmd.output())
        .await
        .map_err(|_| {
            tr!(
                "tailscale {} 超过 {} 秒没有返回",
                "tailscale {} didn't return within {}s",
                args.join(" "),
                COMMAND_TIMEOUT.as_secs()
            )
        })?
        .map_err(|e| e.to_string())?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    } else {
        let err = String::from_utf8_lossy(&output.stderr);
        let out = String::from_utf8_lossy(&output.stdout);
        // 出错信息有时在 stdout(比如让你去网页上开 HTTPS 的那一段),两个都带上。
        Err(format!(
            "{}{}",
            err.trim(),
            if out.trim().is_empty() {
                String::new()
            } else {
                format!("\n{}", out.trim())
            }
        ))
    }
}

async fn json_of(bin: &PathBuf, args: &[&str]) -> Result<Value, String> {
    let text = run_tailscale(bin, args).await?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

/// 读出当前状态:能配对就给二维码,不能就说清楚缺什么。只读,不改任何配置。
pub async fn gather(local_mode: bool, stt_port: u16) -> PairingInfo {
    if !local_mode {
        return PairingInfo::bare(
            PairingStatus::NotLocal,
            tr!(
                "手机配对只在「本地管理」模式下可用:服务要跑在这台电脑上。远程模式请在服务所在的电脑上运行 swift mobile/tools/pair.swift。",
                "Phone pairing is only available in local-managed mode, where the service runs on this computer. In remote mode, run swift mobile/tools/pair.swift on the computer that hosts the service."
            ),
        );
    }
    let Some(bin) = find_tailscale() else {
        return PairingInfo::bare(
            PairingStatus::NoTailscale,
            tr!(
                "没找到 Tailscale。先在这台电脑和手机上都装好 Tailscale 并登录同一个账号。",
                "Tailscale wasn't found. Install Tailscale on this computer and your phone and sign in to the same account."
            ),
        );
    };

    let status = match json_of(&bin, &["status", "--json"]).await {
        Ok(v) => v,
        Err(e) => return not_running(&e),
    };
    let host = match self_dns_name(&status) {
        Ok(h) => h,
        Err(state) => return not_running(&state),
    };
    let serve = json_of(&bin, &["serve", "status", "--json"])
        .await
        .unwrap_or(Value::Null);

    if let Some(address) = find_serve_address(&serve, stt_port) {
        return ready(address);
    }
    let port = pick_free_port(&used_ports(&serve));
    let command =
        port.map(|p| format!("tailscale serve --bg --https={p} http://127.0.0.1:{stt_port}"));
    let mut info = PairingInfo::bare(
        PairingStatus::NeedsServe,
        match port {
            Some(_) => tr!(
                "还没有把 STT 服务转成 HTTPS。开启后,只有你 tailnet 里的设备(比如你的手机)能访问,不会对公网开放。本机的 Tailscale 名字是 {}。",
                "The STT service isn't exposed over HTTPS yet. Once enabled, only devices in your tailnet (like your phone) can reach it; it's not opened to the internet. This computer's Tailscale name is {}.",
                host
            ),
            None => tr!(
                "8443–8446 这几个 HTTPS 端口都被 tailscale serve 占用了,没法自动开启。先用 tailscale serve status 看看,腾出一个再来。",
                "HTTPS ports 8443–8446 are all in use by tailscale serve, so it can't be enabled automatically. Check tailscale serve status and free one up first."
            ),
        },
    );
    info.command = command;
    info
}

fn not_running(detail: &str) -> PairingInfo {
    PairingInfo::bare(
        PairingStatus::NotRunning,
        if detail.is_empty() {
            tr!(
                "Tailscale 没有在运行。打开 Tailscale 并登录。",
                "Tailscale isn't running. Open Tailscale and sign in."
            )
        } else {
            tr!(
                "Tailscale 没有连上(状态:{})。打开 Tailscale 并登录。",
                "Tailscale isn't connected (state: {}). Open Tailscale and sign in.",
                detail
            )
        },
    )
}

/// 用户点了「开启手机访问」:执行 `tailscale serve`,然后重新读一遍状态。
pub async fn enable(local_mode: bool, stt_port: u16) -> PairingInfo {
    let current = gather(local_mode, stt_port).await;
    if current.status != PairingStatus::NeedsServe {
        return current; // 已经开着,或者根本做不了
    }
    let Some(bin) = find_tailscale() else {
        return current;
    };
    // 命令里的端口是 gather 挑好的那个;这里不重新挑,免得和用户看到的不一致。
    let Some(port) = current
        .command
        .as_deref()
        .and_then(|c| c.split("--https=").nth(1))
        .and_then(|rest| rest.split_whitespace().next())
        .and_then(|p| p.parse::<u16>().ok())
    else {
        return current;
    };
    let target = format!("http://127.0.0.1:{stt_port}");
    let https = format!("--https={port}");
    if let Err(e) = run_tailscale(&bin, &["serve", "--bg", &https, &target]).await {
        let mut failed = current;
        failed.message = Some(tr!("开启失败:{}", "Couldn't enable it: {}", e));
        return failed;
    }
    gather(local_mode, stt_port).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// 真实机器上 `tailscale serve status --json` 的输出(端口 443 转给别的服务,8443 转给 STT)。
    fn real_serve() -> Value {
        json!({
            "TCP": { "443": { "HTTPS": true }, "8443": { "HTTPS": true } },
            "Web": {
                "mac.tailnet.ts.net:443": { "Handlers": { "/": { "Proxy": "http://127.0.0.1:18789" } } },
                "mac.tailnet.ts.net:8443": { "Handlers": { "/": { "Proxy": "http://127.0.0.1:6544" } } }
            }
        })
    }

    #[test]
    fn finds_the_https_forward_to_stt_and_ignores_others() {
        assert_eq!(
            find_serve_address(&real_serve(), 6544).as_deref(),
            Some("https://mac.tailnet.ts.net:8443")
        );
        // 换个端口就没有:18789 那条是别的服务,不能拿来配对。
        assert_eq!(find_serve_address(&real_serve(), 7544), None);
    }

    #[test]
    fn port_443_is_written_without_a_port() {
        let serve = json!({
            "TCP": { "443": { "HTTPS": true } },
            "Web": { "mac.tailnet.ts.net:443": { "Handlers": { "/": { "Proxy": "http://localhost:6544" } } } }
        });
        assert_eq!(
            find_serve_address(&serve, 6544).as_deref(),
            Some("https://mac.tailnet.ts.net")
        );
    }

    #[test]
    fn plain_http_forwards_do_not_count() {
        // 80 端口的明文转发:手机端只认 https,不能当成可配对的地址。
        let serve = json!({
            "TCP": { "80": { "HTTP": true } },
            "Web": { "mac.tailnet.ts.net:80": { "Handlers": { "/": { "Proxy": "http://127.0.0.1:6544" } } } }
        });
        assert_eq!(find_serve_address(&serve, 6544), None);
    }

    #[test]
    fn only_loopback_proxies_count() {
        assert!(proxies_to_local_port("http://127.0.0.1:6544", 6544));
        assert!(proxies_to_local_port("http://localhost:6544/", 6544));
        assert!(proxies_to_local_port("http://[::1]:6544", 6544));
        assert!(!proxies_to_local_port("http://10.0.0.5:6544", 6544)); // 转发到别的机器
        assert!(!proxies_to_local_port("http://127.0.0.1:16544", 6544)); // 只是后缀像
        assert!(!proxies_to_local_port("not a url", 6544));
    }

    #[test]
    fn empty_or_missing_serve_config_finds_nothing() {
        assert_eq!(find_serve_address(&json!({}), 6544), None);
        assert_eq!(find_serve_address(&Value::Null, 6544), None);
        assert!(used_ports(&Value::Null).is_empty());
    }

    #[test]
    fn picks_the_first_free_https_port() {
        assert_eq!(pick_free_port(&[]), Some(8443));
        assert_eq!(pick_free_port(&used_ports(&real_serve())), Some(8444));
        assert_eq!(pick_free_port(&[8443, 8444, 8445, 8446]), None);
    }

    #[test]
    fn self_name_needs_a_running_backend() {
        let running =
            json!({ "BackendState": "Running", "Self": { "DNSName": "mac.tailnet.ts.net." } });
        assert_eq!(self_dns_name(&running).as_deref(), Ok("mac.tailnet.ts.net"));
        let stopped =
            json!({ "BackendState": "NeedsLogin", "Self": { "DNSName": "mac.tailnet.ts.net." } });
        assert_eq!(self_dns_name(&stopped), Err("NeedsLogin".to_string()));
        assert!(self_dns_name(&json!({})).is_err());
    }

    #[test]
    fn link_encodes_the_address_and_round_trips() {
        let link = build_link("https://mac.tailnet.ts.net:8443");
        assert_eq!(
            link,
            "voiceinput://setup?url=https%3A%2F%2Fmac.tailnet.ts.net%3A8443"
        );
        // 没有会被当成参数分隔符的字符。
        assert!(!link.contains('&') && !link.contains('+') && !link.contains(' '));
    }

    #[test]
    fn qr_is_an_svg_with_a_white_background() {
        let svg = qr_svg(&build_link("https://mac.tailnet.ts.net:8443")).unwrap();
        assert!(
            svg.starts_with("<svg"),
            "应该直接以 <svg 开头,得到:{}",
            &svg[..30]
        );
        // 深色主题下也得是黑码白底,否则手机扫不出来。
        assert!(svg.contains("#ffffff") && svg.contains("#000000"));
    }

    #[test]
    fn remote_mode_is_not_pairable() {
        let info = futures_block(gather(false, 6544));
        assert_eq!(info.status, PairingStatus::NotLocal);
        assert!(info.qr_svg.is_none() && info.address.is_none());
    }

    /// 对着这台机器上真实的 Tailscale 读一遍(只读,不改配置)。需要本机装了 Tailscale 并在线,
    /// 所以默认不跑:`cargo test --lib real_tailscale -- --ignored --nocapture`。
    #[test]
    #[ignore]
    fn real_tailscale_read_only() {
        let info = futures_block(gather(true, 6544));
        println!(
            "status={:?}\naddress={:?}\nlink={:?}\nqr={} bytes\nmessage={:?}\ncommand={:?}",
            info.status,
            info.address,
            info.link,
            info.qr_svg.as_ref().map_or(0, String::len),
            info.message,
            info.command
        );
        // 想看二维码长什么样:设 PAIRING_QR_SVG_OUT=/some/path.svg,会把 SVG 写到那里。
        if let (Ok(path), Some(svg)) = (std::env::var("PAIRING_QR_SVG_OUT"), info.qr_svg.as_ref()) {
            std::fs::write(path, svg).unwrap();
        }
        assert!(matches!(
            info.status,
            PairingStatus::Ready | PairingStatus::NeedsServe
        ));
    }

    fn futures_block<F: std::future::Future>(f: F) -> F::Output {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(f)
    }
}
