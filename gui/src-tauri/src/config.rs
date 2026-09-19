use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use tauri::Manager;

/// Tauri Voice Input configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VoiceInputConfig {
    pub server: ServerConfig,
    pub hotkey: HotkeyConfig,
    pub ui: UiConfig,
    pub audio: AudioConfig,
    pub llm: LlmConfig,
    pub _version: String,
}

/// 服务器来源:由本应用拉起的本地进程,还是连接一个已经在别处跑着的服务。
///
/// 默认是 `Remote`——这不是审美选择,而是向后兼容的要求:老的 config.json
/// 里 `server` 只有 `{host, port}`,反序列化时 `mode` 走 `Default`,必须落到
/// 「和今天行为完全一致」的那一支上,也就是「只连,不管进程」。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServerMode {
    /// 只连接,不负责进程生命周期(旧行为;也是跨机访问远程服务器的模式)。
    #[default]
    Remote,
    /// 由本应用负责启动 / 停止本机的 STT、LLM 两个 Python 服务。
    Local,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    /// 新增字段一律带 `#[serde(default)]`:只有 `{host, port}` 的旧配置必须
    /// 能原样读出来,并且表现得和加这个功能之前一模一样。
    #[serde(default)]
    pub mode: ServerMode,
    #[serde(default)]
    pub local: LocalServerConfig,
}

/// 本地管理模式下拉起 Python 服务需要的信息。
///
/// 应用装在 `/Applications`,Python 仓库在别处,两者没有固定相对关系,
/// 所以路径只能配置 + 自动探测,不能写死。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LocalServerConfig {
    /// 仓库根目录(包含 `services/stt_server.py`),即子进程的工作目录。
    #[serde(default)]
    pub repo_path: Option<String>,
    /// Python 解释器绝对路径,通常是 `<repo>/.venv/bin/python`。
    #[serde(default)]
    pub python_path: Option<String>,
    #[serde(default = "default_stt_port")]
    pub stt_port: u16,
    #[serde(default = "default_llm_port")]
    pub llm_port: u16,
    /// 留空表示用服务端自己的默认模型(STT 服务还会读它自己持久化的上次选择)。
    #[serde(default)]
    pub stt_model: Option<String>,
    #[serde(default)]
    pub llm_model: Option<String>,
    /// 应用启动时是否自动拉起两个服务。默认关:加载模型很吃内存,
    /// 不该在用户没要求的情况下悄悄占掉。
    #[serde(default)]
    pub auto_start: bool,
}

fn default_stt_port() -> u16 {
    6544
}

fn default_llm_port() -> u16 {
    6545
}

impl Default for LocalServerConfig {
    fn default() -> Self {
        Self {
            repo_path: None,
            python_path: None,
            stt_port: default_stt_port(),
            llm_port: default_llm_port(),
            stt_model: None,
            llm_model: None,
            auto_start: false,
        }
    }
}

impl ServerConfig {
    /// 客户端实际该连的 STT 地址。
    ///
    /// - 本地管理:永远是回环 + 本地端口。此时 `host` 字段(远程地址)被忽略,
    ///   但**保留不动**,用户切回远程模式时原样还在。
    /// - 远程:`host` 允许是裸主机名,也允许是完整 URL(见 `stt.rs` 的
    ///   `SttClient::new`)。已经是完整 URL 时原样透传——里面已经带了端口,
    ///   再拼一次会拼出 `http://1.2.3.4:6544:6544`。
    pub fn effective_stt_url(&self) -> String {
        match self.mode {
            ServerMode::Local => format!("http://127.0.0.1:{}", self.local.stt_port),
            ServerMode::Remote => {
                if self.host.starts_with("http://") || self.host.starts_with("https://") {
                    self.host.clone()
                } else {
                    format!("http://{}:{}", self.host, self.port)
                }
            }
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HotkeyConfig {
    pub key: String,
    pub distinguish_left_right: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UiConfig {
    pub start_minimized: bool,
    pub use_floating_indicator: bool,
    pub use_tray: bool,
    pub opacity: f64,
    pub auto_input: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioConfig {
    pub device: Option<String>,
    pub language: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmConfig {
    /// LLM 后处理开关的**本地缓存**,不是权威。
    ///
    /// 权威一直在 STT 服务那边(`GET/PUT /llm/enabled`,由它持久化到
    /// `~/.config/voice-input-framework/stt_state.json`),因为真正按这个标志位
    /// 决定要不要做后处理的就是它。这个字段以前从头到尾没有任何地方读过——
    /// 一份写了却没人看的「第二真相」。
    ///
    /// 现在它有且只有一个职责:回答一个**在 STT 起来之前问不到**的问题——
    /// 应用启动时要不要拉起 LLM 服务(见 `lib.rs` 的 `auto_start` 一段)。
    /// 缓存过期了不要紧:STT 一健康就会拿权威值对账并把这里改正过来
    /// (`reconcile_llm_after_start`),所以两个标志位不会各说各话。
    pub enabled: bool,
}

/// Old Python client config format (for migration)
#[derive(Debug, Deserialize)]
struct OldPythonConfig {
    server: Option<OldServerConfig>,
    hotkey: Option<OldHotkeyConfig>,
    ui: Option<OldUiConfig>,
    audio: Option<OldAudioConfig>,
    llm: Option<OldLlmConfig>,
}

#[derive(Debug, Deserialize)]
struct OldServerConfig {
    host: Option<String>,
    port: Option<u16>,
}

#[derive(Debug, Deserialize)]
struct OldHotkeyConfig {
    key: Option<String>,
    distinguish_left_right: Option<bool>,
}

#[derive(Debug, Deserialize)]
struct OldUiConfig {
    start_minimized: Option<bool>,
    use_floating_indicator: Option<bool>,
    use_tray: Option<bool>,
    opacity: Option<f64>,
}

#[derive(Debug, Deserialize)]
struct OldAudioConfig {
    device: Option<serde_json::Value>,
    language: Option<String>,
}

#[derive(Debug, Deserialize)]
struct OldLlmConfig {
    enabled: Option<bool>,
}

impl Default for VoiceInputConfig {
    fn default() -> Self {
        Self {
            server: ServerConfig {
                // 用 127.0.0.1 而非 localhost:避免 Windows IPv6(::1)优先解析导致连接延迟
                host: "127.0.0.1".into(),
                port: 6544,
                mode: ServerMode::default(),
                local: LocalServerConfig::default(),
            },
            hotkey: HotkeyConfig {
                key: "left_ctrl+left_alt".into(),
                distinguish_left_right: true,
            },
            ui: UiConfig {
                start_minimized: false,
                use_floating_indicator: true,
                use_tray: true,
                opacity: 0.8,
                auto_input: false,
            },
            audio: AudioConfig {
                device: None,
                language: "auto".into(),
            },
            llm: LlmConfig { enabled: true },
            _version: "2.0".into(),
        }
    }
}

impl VoiceInputConfig {
    fn config_path(app: &tauri::AppHandle) -> PathBuf {
        let dir = app.path().app_data_dir().unwrap_or_else(|_| {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
            PathBuf::from(home).join(".config/voice-input")
        });
        dir.join("config.json")
    }

    fn old_config_path() -> PathBuf {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
        PathBuf::from(home).join(".voice_input_config.json")
    }

    pub fn load(app: &tauri::AppHandle) -> Self {
        let path = Self::config_path(app);

        // Try loading Tauri config first
        if let Ok(data) = fs::read_to_string(&path) {
            match serde_json::from_str::<VoiceInputConfig>(&data) {
                Ok(cfg) => return cfg,
                // 文件在,但读不懂。以前这里是一句静默的 `if let Ok`,于是所有设置
                // (仓库路径、端口、快捷键、随应用启动……)悄悄回到出厂值,用户看到的
                // 是「怎么像刚装上一样」,而下面那条兜底分支紧接着就用默认值把这个
                // 文件覆盖掉 —— 原来的内容再也找不回来。
                //
                // 所以:说出来,并且先把残文件挪到一边再走兜底。挪走而不是留在原地,
                // 是因为兜底那一步一定会写同名文件;备份存在才谈得上「还能救」。
                Err(e) => {
                    crate::log_error!(
                        "[config] {} 解析失败({}),本次用默认配置启动",
                        path.display(),
                        e
                    );
                    let backup = path.with_extension("json.corrupt");
                    match fs::rename(&path, &backup) {
                        Ok(()) => crate::log_error!(
                            "[config] 原文件已保留为 {},修好后可改回 config.json",
                            backup.display()
                        ),
                        Err(e) => {
                            crate::log_error!("[config] 原文件没能备份,将被覆盖: {}", e)
                        }
                    }
                }
            }
        }

        // Try migrating old Python config
        let old_path = Self::old_config_path();
        if old_path.exists() {
            if let Ok(data) = fs::read_to_string(&old_path) {
                if let Ok(old_cfg) = serde_json::from_str::<OldPythonConfig>(&data) {
                    let new_cfg = Self::migrate_from_old(old_cfg);
                    // Rename old config
                    let backup = old_path.with_extension("json.bak");
                    let _ = fs::rename(&old_path, backup);
                    // Save new config
                    if let Some(dir) = path.parent() {
                        let _ = fs::create_dir_all(dir);
                    }
                    if let Ok(json) = serde_json::to_string_pretty(&new_cfg) {
                        let _ = fs::write(&path, json);
                    }
                    return new_cfg;
                }
            }
        }

        // Fall back to defaults
        let default_cfg = VoiceInputConfig::default();
        if let Some(dir) = path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        if let Ok(json) = serde_json::to_string_pretty(&default_cfg) {
            let _ = fs::write(&path, json);
        }
        default_cfg
    }

    pub fn save(&self, app: &tauri::AppHandle) -> Result<(), String> {
        let path = Self::config_path(app);
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir).map_err(|e| format!("创建配置目录失败: {}", e))?;
        }
        let json =
            serde_json::to_string_pretty(self).map_err(|e| format!("序列化配置失败: {}", e))?;
        // 先写临时文件再 rename 覆盖,而不是直接 `fs::write`。`fs::write` 是
        // 「先截断、再写」:写到一半断电或被强杀,留下的就是一个半截的 config.json,
        // 下次启动解析不了 —— 全部设置作废。rename 在同一个目录内是原子的,
        // 要么是完整的旧文件,要么是完整的新文件,不存在中间态。
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, json).map_err(|e| format!("写入配置失败: {}", e))?;
        fs::rename(&tmp, &path).map_err(|e| {
            let _ = fs::remove_file(&tmp);
            format!("替换配置文件失败: {}", e)
        })?;
        Ok(())
    }

    fn migrate_from_old(old: OldPythonConfig) -> Self {
        VoiceInputConfig {
            server: ServerConfig {
                host: old
                    .server
                    .as_ref()
                    .and_then(|s| s.host.clone())
                    .unwrap_or_else(|| "127.0.0.1".into()),
                port: old.server.as_ref().and_then(|s| s.port).unwrap_or(6544),
                // 老 Python 客户端没有「本地管理」概念,迁移过来一律是远程/手动。
                mode: ServerMode::default(),
                local: LocalServerConfig::default(),
            },
            hotkey: HotkeyConfig {
                key: old
                    .hotkey
                    .as_ref()
                    .and_then(|h| h.key.clone())
                    .unwrap_or_else(|| "left_ctrl+left_alt".into()),
                distinguish_left_right: old
                    .hotkey
                    .as_ref()
                    .and_then(|h| h.distinguish_left_right)
                    .unwrap_or(true),
            },
            ui: UiConfig {
                start_minimized: old
                    .ui
                    .as_ref()
                    .and_then(|u| u.start_minimized)
                    .unwrap_or(false),
                use_floating_indicator: old
                    .ui
                    .as_ref()
                    .and_then(|u| u.use_floating_indicator)
                    .unwrap_or(true),
                use_tray: old.ui.as_ref().and_then(|u| u.use_tray).unwrap_or(true),
                opacity: old.ui.as_ref().and_then(|u| u.opacity).unwrap_or(0.8),
                auto_input: false,
            },
            audio: AudioConfig {
                device: old.audio.as_ref().and_then(|a| {
                    a.device.as_ref().and_then(|d| match d {
                        serde_json::Value::Number(n) => Some(n.to_string()),
                        serde_json::Value::String(s) => Some(s.clone()),
                        _ => None,
                    })
                }),
                language: old
                    .audio
                    .as_ref()
                    .and_then(|a| a.language.clone())
                    .unwrap_or_else(|| "auto".into()),
            },
            llm: LlmConfig {
                enabled: old.llm.as_ref().and_then(|l| l.enabled).unwrap_or(true),
            },
            _version: "2.0".into(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 用户磁盘上真实存在的老配置(只有 `{host, port}`,没有 `mode` / `local`)。
    /// 这个字符串就是 `~/Library/Application Support/com.voiceinput.app/config.json`
    /// 的形状——加服务器管理功能不能让它读不出来。
    const LEGACY_CONFIG: &str = r#"{
      "server": { "host": "localhost", "port": 6544 },
      "hotkey": { "key": "left_ctrl+left_alt", "distinguish_left_right": true },
      "ui": { "start_minimized": false, "use_floating_indicator": true,
              "use_tray": true, "opacity": 0.8, "auto_input": true },
      "audio": { "device": null, "language": "auto" },
      "llm": { "enabled": true },
      "_version": "2.0"
    }"#;

    #[test]
    fn legacy_config_still_loads() {
        let cfg: VoiceInputConfig = serde_json::from_str(LEGACY_CONFIG).unwrap();
        assert_eq!(cfg.server.host, "localhost");
        assert_eq!(cfg.server.port, 6544);
        // 缺失的新字段必须落到「远程 / 不管进程」,也就是加功能之前的行为。
        assert_eq!(cfg.server.mode, ServerMode::Remote);
        assert!(cfg.server.local.repo_path.is_none());
        assert!(!cfg.server.local.auto_start);
    }

    /// 老配置算出来的 STT 地址,必须和加功能前 `SttClient::new("localhost")`
    /// 得到的地址逐字符相同,否则就是悄悄改了连接目标。
    #[test]
    fn legacy_config_resolves_to_todays_url() {
        let cfg: VoiceInputConfig = serde_json::from_str(LEGACY_CONFIG).unwrap();
        assert_eq!(cfg.server.effective_stt_url(), "http://localhost:6544");
    }

    /// 远程模式下 host 可以是完整 URL(跨机访问),此时不能再拼一次端口。
    #[test]
    fn remote_full_url_is_passed_through() {
        let mut cfg = VoiceInputConfig::default();
        cfg.server.host = "http://1.2.3.4:6544".into();
        assert_eq!(cfg.server.effective_stt_url(), "http://1.2.3.4:6544");
    }

    /// https 也要原样透传:远程服务在反代后面时地址就是 https 的。
    #[test]
    fn remote_https_url_is_passed_through() {
        let mut cfg = VoiceInputConfig::default();
        cfg.server.host = "https://stt.example.com".into();
        assert_eq!(cfg.server.effective_stt_url(), "https://stt.example.com");
    }

    /// 远程模式填完整 URL 时,`port` 字段必须完全不参与。
    ///
    /// `set_server_host` 以前自己拼 `http://{host}:{port}`,于是用户填
    /// `http://1.2.3.4:6544` 会被拼成 `http://1.2.3.4:6544:6544`——一条连不上
    /// 的地址。改成走 `effective_stt_url()` 之后端口只可能出现一次。
    #[test]
    fn full_url_host_never_gets_the_port_appended_twice() {
        let mut cfg = VoiceInputConfig::default();
        cfg.server.host = "http://1.2.3.4:6544".into();
        cfg.server.port = 6544;
        assert_eq!(cfg.server.effective_stt_url(), "http://1.2.3.4:6544");
    }

    /// 本地模式忽略 host,但不擦掉它——切回远程时用户填的地址还得在。
    #[test]
    fn local_mode_uses_loopback_and_keeps_remote_host() {
        let mut cfg = VoiceInputConfig::default();
        cfg.server.host = "192.168.1.9".into();
        cfg.server.mode = ServerMode::Local;
        cfg.server.local.stt_port = 7544;
        assert_eq!(cfg.server.effective_stt_url(), "http://127.0.0.1:7544");
        assert_eq!(cfg.server.host, "192.168.1.9");
    }

    /// 本地模式下 host 哪怕是一条完整 URL,也一样不参与——「本地管理」的
    /// 含义就是连自己拉起的那个进程。
    #[test]
    fn local_mode_ignores_a_full_url_host() {
        let mut cfg = VoiceInputConfig::default();
        cfg.server.host = "https://stt.example.com".into();
        cfg.server.mode = ServerMode::Local;
        assert_eq!(cfg.server.effective_stt_url(), "http://127.0.0.1:6544");
    }

    /// 本地模式跟的是 `local.stt_port`,不是 `server.port`。
    ///
    /// 这就是「从界面启动服务后一直未连接」的形状:前端拿 `host` + `port`
    /// 自己拼地址,本地管理模式下拼出来的是远程那对字段,和服务实际在听的
    /// 端口毫无关系。地址只能有 `effective_stt_url` 一个出处。
    #[test]
    fn local_mode_follows_the_local_stt_port_not_the_remote_fields() {
        let mut cfg = VoiceInputConfig::default();
        cfg.server.host = "192.168.1.9".into();
        cfg.server.port = 6544;
        cfg.server.mode = ServerMode::Local;
        cfg.server.local.stt_port = 7544;
        let naive = format!("http://{}:{}", cfg.server.host, cfg.server.port);
        assert_eq!(cfg.server.effective_stt_url(), "http://127.0.0.1:7544");
        assert_ne!(cfg.server.effective_stt_url(), naive);
    }

    /// 存下去的配置必须能再读回来(新字段的 serde 表示自洽)。
    #[test]
    fn round_trips_through_json() {
        let mut cfg = VoiceInputConfig::default();
        cfg.server.mode = ServerMode::Local;
        cfg.server.local.repo_path = Some("/Users/me/voice-input-framework".into());
        cfg.server.local.python_path =
            Some("/Users/me/voice-input-framework/.venv/bin/python".into());
        let json = serde_json::to_string(&cfg).unwrap();
        let back: VoiceInputConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(back.server.mode, ServerMode::Local);
        assert_eq!(
            back.server.local.repo_path.as_deref(),
            Some("/Users/me/voice-input-framework")
        );
    }

    #[test]
    fn mode_serializes_as_snake_case() {
        assert_eq!(
            serde_json::to_string(&ServerMode::Local).unwrap(),
            "\"local\""
        );
        assert_eq!(
            serde_json::to_string(&ServerMode::Remote).unwrap(),
            "\"remote\""
        );
    }
}
