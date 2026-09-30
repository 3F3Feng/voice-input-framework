//! 局域网共享:让手机(或别的设备)通过局域网直连本机的 STT 服务。
//!
//! 以前要在终端里手动设 `VIF_STT_HOST=0.0.0.0` 和 `VIF_API_TOKEN`、再放行防火墙,
//! 对测试的人来说太繁琐(尤其是 Windows)。现在设置里一个开关:打开后本应用拉起的服务改绑
//! `0.0.0.0`、两个服务都带上自动生成的令牌(见 `server_manager::network_settings`),
//! 界面直接告诉你手机里该填的地址和令牌。
//!
//! 安全上的底线:**没有令牌绝不对局域网开放**。令牌为空时当成没开(见 `config::lan_token_of`)。
//! 只在「本地管理」模式下有意义;远程模式的服务不在这台电脑上。

use serde::Serialize;
use std::net::{IpAddr, Ipv4Addr, UdpSocket};

use crate::config::LocalServerConfig;

/// 令牌长度:24 位字母数字,约 143 位熵。
pub const TOKEN_LEN: usize = 24;

const ALPHABET: &[u8; 62] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

/// 用系统随机数生成一个令牌。
///
/// 按字节取模会让字母表前几位出现得更多(256 不是 62 的倍数),所以用拒绝采样:
/// 只收小于 248(= 62 × 4)的字节,再对 62 取模,每个字符等概率。
pub fn generate_token() -> Result<String, String> {
    let mut out = String::with_capacity(TOKEN_LEN);
    let mut buf = [0u8; 64];
    while out.len() < TOKEN_LEN {
        getrandom::getrandom(&mut buf).map_err(|e| e.to_string())?;
        for &b in &buf {
            if b < 248 {
                out.push(ALPHABET[(b % 62) as usize] as char);
                if out.len() == TOKEN_LEN {
                    break;
                }
            }
        }
    }
    Ok(out)
}

/// 是不是常见的局域网私有地址(10/8、172.16/12、192.168/16)。
fn is_private_lan(ip: Ipv4Addr) -> bool {
    ip.is_private()
}

/// 本机在局域网里的主地址:系统选哪块网卡去访问外网,就用那块的地址。
///
/// UDP 的 `connect` 只是让系统选好路由和本地地址,**不会发出任何数据包**。
/// 没有网络、或者主地址不是私有地址(比如直接拿着公网 IP)时返回 `None`。
/// 多块网卡时只给主的那一块;想用别的地址,手机里手填即可。
pub fn primary_lan_ip() -> Option<Ipv4Addr> {
    let socket = UdpSocket::bind("0.0.0.0:0").ok()?;
    socket.connect("8.8.8.8:80").ok()?;
    match socket.local_addr().ok()?.ip() {
        IpAddr::V4(ip) if is_private_lan(ip) => Some(ip),
        _ => None,
    }
}

/// 给前端的整包信息。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LanShareInfo {
    pub enabled: bool,
    /// 手机里要填的令牌。没开共享时是 `None`(不回显)。
    pub token: Option<String>,
    pub port: u16,
    /// 手机里要填的服务地址,如 `http://192.168.1.23:6544`。找不到局域网地址时是 `None`。
    pub address: Option<String>,
    /// Windows 上放行入站端口的命令(要在管理员 PowerShell 里执行);别的系统是 `None`。
    pub firewall_command: Option<String>,
}

pub fn address_for(ip: Ipv4Addr, port: u16) -> String {
    format!("http://{ip}:{port}")
}

pub fn firewall_command(port: u16) -> String {
    format!(
        "New-NetFirewallRule -DisplayName \"Voice Input STT\" -Direction Inbound -Protocol TCP -LocalPort {port} -Action Allow -Profile Private"
    )
}

/// 读出当前状态。`ip` 由调用方传入,好让这个函数不碰网络、能直接测。
pub fn info(local: &LocalServerConfig, ip: Option<Ipv4Addr>, windows: bool) -> LanShareInfo {
    let token = crate::config::lan_token_of(local);
    let enabled = token.is_some();
    LanShareInfo {
        enabled,
        token,
        port: local.stt_port,
        address: ip
            .filter(|_| enabled)
            .map(|ip| address_for(ip, local.stt_port)),
        firewall_command: (enabled && windows).then(|| firewall_command(local.stt_port)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shared(token: Option<&str>) -> LocalServerConfig {
        LocalServerConfig {
            lan_share: true,
            lan_token: token.map(String::from),
            ..LocalServerConfig::default()
        }
    }

    #[test]
    fn token_is_24_alphanumeric_chars_and_different_each_time() {
        let a = generate_token().unwrap();
        let b = generate_token().unwrap();
        assert_eq!(a.len(), TOKEN_LEN);
        assert!(a.bytes().all(|c| ALPHABET.contains(&c)), "{a}");
        assert_ne!(a, b, "两次生成的令牌不应相同");
    }

    #[test]
    fn token_uses_the_whole_alphabet_roughly_evenly() {
        // 拒绝采样没写错的话,62 个字符都会出现,而且不会有哪个远多于平均。
        let mut counts = [0usize; 62];
        for _ in 0..400 {
            for c in generate_token().unwrap().bytes() {
                counts[ALPHABET.iter().position(|&x| x == c).unwrap()] += 1;
            }
        }
        let total: usize = counts.iter().sum(); // 9600,平均每个约 155
        assert_eq!(total, 400 * TOKEN_LEN);
        assert!(
            counts.iter().all(|&n| n > 80 && n < 260),
            "分布不均匀: {counts:?}"
        );
    }

    #[test]
    fn sharing_is_off_by_default() {
        let i = info(
            &LocalServerConfig::default(),
            Some(Ipv4Addr::new(192, 168, 1, 23)),
            true,
        );
        assert!(!i.enabled);
        assert_eq!((i.token, i.address, i.firewall_command), (None, None, None));
    }

    #[test]
    fn enabled_shows_token_address_and_the_windows_firewall_command() {
        let i = info(
            &shared(Some("abc123")),
            Some(Ipv4Addr::new(192, 168, 1, 23)),
            true,
        );
        assert!(i.enabled);
        assert_eq!(i.token.as_deref(), Some("abc123"));
        assert_eq!(i.address.as_deref(), Some("http://192.168.1.23:6544"));
        let cmd = i.firewall_command.unwrap();
        assert!(
            cmd.contains("-LocalPort 6544") && cmd.contains("-Profile Private"),
            "{cmd}"
        );
    }

    #[test]
    fn no_firewall_command_outside_windows() {
        let i = info(
            &shared(Some("abc123")),
            Some(Ipv4Addr::new(10, 0, 0, 2)),
            false,
        );
        assert_eq!(i.firewall_command, None);
    }

    #[test]
    fn sharing_without_a_token_counts_as_off() {
        // 开关开着但令牌是空的:绝不能当成「开放且不设令牌」。
        for t in [None, Some(""), Some("   ")] {
            let i = info(&shared(t), Some(Ipv4Addr::new(192, 168, 1, 23)), true);
            assert!(!i.enabled, "令牌 {t:?} 不应算开启");
            assert_eq!(i.token, None);
        }
    }

    #[test]
    fn address_follows_the_configured_port() {
        let mut local = shared(Some("t"));
        local.stt_port = 7544;
        let i = info(&local, Some(Ipv4Addr::new(172, 16, 0, 9)), false);
        assert_eq!(i.address.as_deref(), Some("http://172.16.0.9:7544"));
        assert_eq!(i.port, 7544);
    }

    #[test]
    fn no_address_when_there_is_no_lan_ip() {
        let i = info(&shared(Some("t")), None, false);
        assert!(i.enabled);
        assert_eq!(i.address, None);
    }

    #[test]
    fn only_private_ranges_count_as_lan() {
        assert!(is_private_lan(Ipv4Addr::new(192, 168, 1, 23)));
        assert!(is_private_lan(Ipv4Addr::new(10, 1, 2, 3)));
        assert!(is_private_lan(Ipv4Addr::new(172, 16, 5, 5)));
        assert!(!is_private_lan(Ipv4Addr::new(8, 8, 8, 8)));
        assert!(!is_private_lan(Ipv4Addr::new(100, 64, 0, 1))); // CGNAT / Tailscale 不是局域网
        assert!(!is_private_lan(Ipv4Addr::new(169, 254, 1, 1))); // 链路本地
    }
}
