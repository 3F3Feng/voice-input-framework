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
    /// 远程服务的访问令牌(服务端设了 `VIF_API_TOKEN` 时才需要,F20)。
    #[serde(default)]
    pub token: Option<String>,
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
    /// 模型下载源(HuggingFace 镜像),以 `HF_ENDPOINT` 传给子进程。留空用官方源。
    ///
    /// 界面主要是中文用户,而 huggingface.co 在大陆常常连不上:首次加载模型要下几百
    /// MB 到几 GB,连不上就只能看着「正在加载模型」直到超时失败。
    #[serde(default)]
    pub hf_endpoint: Option<String>,
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
            hf_endpoint: None,
        }
    }
}

impl ServerConfig {
    /// 当前该带的令牌:只有远程模式带。本应用拉起的本地服务不设令牌。
    pub fn active_token(&self) -> Option<String> {
        match self.mode {
            ServerMode::Remote => self.token.clone().filter(|t| !t.trim().is_empty()),
            ServerMode::Local => None,
        }
    }

    /// 首次启动、并且探测到了仓库时的出厂设置:本地管理 + 随应用启动。
    /// 返回是否生效。
    ///
    /// `mode` 默认是 `Remote`,那是为老配置的向后兼容定的(见 [`ServerMode`]);
    /// 可对一个刚装好、仓库就在本机的新用户来说,这意味着打开应用先看到一个连
    /// 不存在的 `127.0.0.1:6544` 的「未连接」,得自己翻到 ⚙ → 服务 → 本地管理 →
    /// 启动。所以只在「全新安装 + 探测到仓库」这一种情况下改默认,已有配置一律不动。
    ///
    /// 解释器没找到(仓库里还没建 .venv)时只切本地模式、不勾随应用启动:
    /// 每次启动都去拉一个注定失败的进程只会刷一屏报错,面板上的「没有解释器」
    /// 提示已经够说清楚该做什么了。
    pub fn apply_first_run_defaults(&mut self, fresh_install: bool) -> bool {
        if !fresh_install || self.local.repo_path.is_none() {
            return false;
        }
        self.mode = ServerMode::Local;
        self.local.auto_start = self.local.python_path.is_some();
        true
    }

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
    /// 切换式录音:按一下开始、再按一下结束。默认按住说话。
    #[serde(default)]
    pub toggle: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UiConfig {
    pub start_minimized: bool,
    // 下面这三项**没有任何地方读**(悬浮胶囊、托盘一直是开的,透明度从没接上)。
    // 不删,是为了降级兼容:2.2.0 及以前把它们声明成必填字段,新版写出的
    // config.json 要是少了它们,用户退回旧版时整份配置解析失败——旧版的 `load`
    // 会静默回到出厂值并覆盖文件,仓库路径、快捷键全丢。留着只多几个字节。
    // 加上 serde 默认值,则是为了将来真删掉时,手里没有它们的配置也照样读得出来。
    #[serde(default = "default_true")]
    pub use_floating_indicator: bool,
    #[serde(default = "default_true")]
    pub use_tray: bool,
    #[serde(default = "default_opacity")]
    pub opacity: f64,
    pub auto_input: bool,
    /// 用户有没有明确选过「说完的文字要不要自动输入到光标处」。
    ///
    /// `auto_input` 默认关,新用户按快捷键说完话,目标窗口里什么都没出现,只会
    /// 以为是坏了。所以主界面会问一次(横幅),选了哪边都把它置 true,之后不再问。
    /// 老配置里没有这个字段,读出来是 false;但已经开着自动输入的老用户显然选过了,
    /// 前端不会给他们看横幅。
    #[serde(default)]
    pub output_choice_made: bool,
    /// 自动输入用什么方式把字送进目标窗口,见 [`InputMethod`]。
    #[serde(default)]
    pub input_method: InputMethod,
    /// 识别结果是否写进本机的历史文件(`history.rs`)。默认开:找回刚才说过的话
    /// 是常用需求;关掉是隐私选项,关了之后新结果只留在本次会话的内存里。
    #[serde(default = "default_true")]
    pub save_history: bool,
    /// 首次启动向导(F1)走完 / 跳过了没有。
    ///
    /// 默认 false,老配置里也没有这个字段、读出来同样是 false —— 光看它分不出
    /// 「新用户」和「升级上来的老用户」。所以它只是必要条件:前端还要再看一眼
    /// 是不是全新安装、或者眼下连不上一个能用的服务,两者都不是(老用户、服务
    /// 好好的)就不打扰。条件的完整说明在 `App.vue` 的 `decideOnboarding`。
    #[serde(default)]
    pub onboarding_done: bool,
}

/// 把识别结果送进目标窗口的方式。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InputMethod {
    /// 写剪贴板 → 模拟粘贴 → 把原剪贴板还回去。默认:长文本一次到位,不受输入法
    /// 影响,换行也不会变成「回车 = 发送」。
    #[default]
    Paste,
    /// 逐字模拟键盘。老行为;少数不接受粘贴的输入框(比如某些密码框、远程桌面)
    /// 只能用它。注意文本里的换行会被当成回车键。
    Type,
    /// 只放进剪贴板,不碰目标窗口,由用户自己粘贴。不需要「辅助功能」权限。
    Copy,
}

fn default_true() -> bool {
    true
}

fn default_opacity() -> f64 {
    0.8
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
                token: None,
            },
            hotkey: HotkeyConfig {
                key: "left_ctrl+left_alt".into(),
                distinguish_left_right: true,
                toggle: false,
            },
            ui: UiConfig {
                start_minimized: false,
                use_floating_indicator: true,
                use_tray: true,
                opacity: 0.8,
                auto_input: false,
                output_choice_made: false,
                input_method: InputMethod::default(),
                save_history: true,
                onboarding_done: false,
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

/// 用户主目录,退回 "." 兜底。判断逻辑(`HOME` → `USERPROFILE`,空串不算)只在
/// `server_manager::home_dir` 写一份;Windows 上通常没有 `HOME`,以前只读它,
/// 老配置找不到、兜底目录也落在了当前工作目录里。
pub fn home_dir() -> PathBuf {
    crate::server_manager::home_dir().unwrap_or_else(|| PathBuf::from("."))
}

/// 旧版快捷键录制存坏的写法,能修就返回修好的。
///
/// 旧录制用 `e.key` 取主键名:空格的 `e.key` 是 `" "`,于是 Ctrl+Space 被存成
/// `"left_ctrl+ "`。以前解析时空白段被悄悄丢掉,它就成了单按 Ctrl 触发;现在
/// 解析器会拒绝空段,这种配置启动后快捷键直接不生效。存坏的只可能是空格这一种
/// (别的键的 `e.key` 都不是空白),所以按空格修回来。
pub fn repair_legacy_hotkey(key: &str) -> Option<String> {
    let parts: Vec<&str> = key.split('+').collect();
    if parts.len() < 2 || !parts.iter().any(|p| !p.is_empty() && p.trim().is_empty()) {
        return None;
    }
    let fixed: Vec<String> = parts
        .iter()
        .filter(|p| !p.is_empty())
        .map(|p| {
            if p.trim().is_empty() {
                "space".to_string()
            } else {
                p.trim().to_string()
            }
        })
        .collect();
    Some(fixed.join("+"))
}

impl VoiceInputConfig {
    fn config_path(app: &tauri::AppHandle) -> PathBuf {
        let dir = app
            .path()
            .app_data_dir()
            .unwrap_or_else(|_| home_dir().join(".config/voice-input"));
        dir.join("config.json")
    }

    /// 这是不是一次真正的全新安装:磁盘上既没有本应用的 config.json,也没有
    /// 老 Python 客户端的配置可迁移。必须在 `load` **之前**问——`load` 找不到文件
    /// 时会立刻用默认值写一份出来。
    ///
    /// 配置损坏(文件在、读不懂)和从老客户端迁移过来的都不算:那是老用户,
    /// 他们的连接方式不能被悄悄改掉。
    pub fn is_fresh_install(app: &tauri::AppHandle) -> bool {
        !Self::config_path(app).exists() && !Self::old_config_path().exists()
    }

    fn old_config_path() -> PathBuf {
        home_dir().join(".voice_input_config.json")
    }

    pub fn load(app: &tauri::AppHandle) -> Self {
        let path = Self::config_path(app);

        // Try loading Tauri config first
        if let Ok(data) = fs::read_to_string(&path) {
            match serde_json::from_str::<VoiceInputConfig>(&data) {
                Ok(mut cfg) => {
                    if let Some(fixed) = repair_legacy_hotkey(&cfg.hotkey.key) {
                        crate::log_info!(
                            "[config] 快捷键「{}」是旧版录制功能存坏的,已修正为「{}」",
                            cfg.hotkey.key,
                            fixed
                        );
                        cfg.hotkey.key = fixed;
                        if let Err(e) = cfg.save(app) {
                            crate::log_error!("[config] 修正后的快捷键没能写回配置: {}", e);
                        }
                    }
                    return cfg;
                }
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
                token: None,
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
                toggle: false,
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
                output_choice_made: false,
                input_method: InputMethod::default(),
                save_history: true,
                onboarding_done: false,
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
    #[test]
    fn legacy_space_hotkey_is_repaired() {
        use super::repair_legacy_hotkey;
        assert_eq!(
            repair_legacy_hotkey("left_ctrl+ ").as_deref(),
            Some("left_ctrl+space")
        );
        assert_eq!(
            repair_legacy_hotkey("left_ctrl+left_alt+ ").as_deref(),
            Some("left_ctrl+left_alt+space")
        );
        assert_eq!(repair_legacy_hotkey("left_ctrl+left_alt"), None);
        assert_eq!(repair_legacy_hotkey("left_ctrl+space"), None);
        assert_eq!(repair_legacy_hotkey(" "), None);
    }

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

    fn detected(cfg: &mut VoiceInputConfig, python: bool) {
        cfg.server.local.repo_path = Some("/Users/me/voice-input-framework".into());
        if python {
            cfg.server.local.python_path =
                Some("/Users/me/voice-input-framework/.venv/bin/python".into());
        }
    }

    #[test]
    fn first_run_with_repo_defaults_to_local_and_auto_start() {
        let mut cfg = VoiceInputConfig::default();
        detected(&mut cfg, true);
        assert!(cfg.server.apply_first_run_defaults(true));
        assert_eq!(cfg.server.mode, ServerMode::Local);
        assert!(cfg.server.local.auto_start);
    }

    /// 有仓库没 venv:切本地(面板会说缺解释器),但不每次启动都去拉一个必败的进程。
    #[test]
    fn first_run_without_python_does_not_auto_start() {
        let mut cfg = VoiceInputConfig::default();
        detected(&mut cfg, false);
        assert!(cfg.server.apply_first_run_defaults(true));
        assert_eq!(cfg.server.mode, ServerMode::Local);
        assert!(!cfg.server.local.auto_start);
    }

    #[test]
    fn first_run_without_repo_stays_remote() {
        let mut cfg = VoiceInputConfig::default();
        assert!(!cfg.server.apply_first_run_defaults(true));
        assert_eq!(cfg.server.mode, ServerMode::Remote);
        assert!(!cfg.server.local.auto_start);
    }

    /// 已有配置(哪怕刚探测到仓库)绝不改连接方式:那是向后兼容的底线。
    #[test]
    fn existing_config_is_never_switched_to_local() {
        let mut cfg: VoiceInputConfig = serde_json::from_str(LEGACY_CONFIG).unwrap();
        detected(&mut cfg, true);
        assert!(!cfg.server.apply_first_run_defaults(false));
        assert_eq!(cfg.server.mode, ServerMode::Remote);
        assert!(!cfg.server.local.auto_start);
    }

    /// 老配置里没有 `output_choice_made`,读出来是「没选过」;自动输入开着的
    /// 那份照样保持开着(前端据此不再弹横幅)。
    #[test]
    fn legacy_config_has_no_output_choice_yet() {
        let cfg: VoiceInputConfig = serde_json::from_str(LEGACY_CONFIG).unwrap();
        assert!(!cfg.ui.output_choice_made);
        assert!(cfg.ui.auto_input);
    }

    /// 老配置里没有 `save_history`:读出来是「保存」,和默认值一致。
    #[test]
    fn legacy_config_saves_history_by_default() {
        let cfg: VoiceInputConfig = serde_json::from_str(LEGACY_CONFIG).unwrap();
        assert!(cfg.ui.save_history);
        assert!(VoiceInputConfig::default().ui.save_history);
    }

    /// 没用的三个 UI 字段缺了也要读得出来(将来删掉它们时不至于读坏新配置)。
    #[test]
    fn unused_ui_fields_are_optional() {
        let json = r#"{
          "server": { "host": "127.0.0.1", "port": 6544 },
          "hotkey": { "key": "left_ctrl+left_alt", "distinguish_left_right": true },
          "ui": { "start_minimized": false, "auto_input": false },
          "audio": { "device": null, "language": "auto" },
          "llm": { "enabled": true },
          "_version": "2.0"
        }"#;
        let cfg: VoiceInputConfig = serde_json::from_str(json).unwrap();
        assert!(cfg.ui.use_tray);
        assert!((cfg.ui.opacity - 0.8).abs() < f64::EPSILON);
    }

    /// 老配置没有 `onboarding_done`:读出来是「没走过向导」,和新装的默认值一致。
    /// 光凭这一项不会给老用户弹向导(前端还要求全新安装或连不上服务)。
    #[test]
    fn onboarding_flag_defaults_to_not_done() {
        let cfg: VoiceInputConfig = serde_json::from_str(LEGACY_CONFIG).unwrap();
        assert!(!cfg.ui.onboarding_done);
        assert!(!VoiceInputConfig::default().ui.onboarding_done);
    }

    /// 走完向导存下去的 `true` 必须读得回来,否则每次启动都会再弹一次。
    #[test]
    fn onboarding_flag_round_trips() {
        let json = r#"{
          "server": { "host": "127.0.0.1", "port": 6544 },
          "hotkey": { "key": "left_ctrl+left_alt", "distinguish_left_right": true },
          "ui": { "start_minimized": false, "auto_input": true, "onboarding_done": true },
          "audio": { "device": null, "language": "auto" },
          "llm": { "enabled": true },
          "_version": "2.0"
        }"#;
        let cfg: VoiceInputConfig = serde_json::from_str(json).unwrap();
        assert!(cfg.ui.onboarding_done);
        let back: VoiceInputConfig =
            serde_json::from_str(&serde_json::to_string(&cfg).unwrap()).unwrap();
        assert!(back.ui.onboarding_done);
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
