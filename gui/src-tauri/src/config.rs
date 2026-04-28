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

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
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
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AudioConfig {
    pub device: Option<String>,
    pub language: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmConfig {
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
                host: "localhost".into(),
                port: 6544,
            },
            hotkey: HotkeyConfig {
                key: "Ctrl+Alt+R".into(),
                distinguish_left_right: false,
            },
            ui: UiConfig {
                start_minimized: false,
                use_floating_indicator: true,
                use_tray: true,
                opacity: 0.8,
            },
            audio: AudioConfig {
                device: None,
                language: "auto".into(),
            },
            llm: LlmConfig {
                enabled: true,
            },
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
            if let Ok(cfg) = serde_json::from_str::<VoiceInputConfig>(&data) {
                return cfg;
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
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| format!("序列化配置失败: {}", e))?;
        fs::write(&path, json).map_err(|e| format!("写入配置失败: {}", e))?;
        Ok(())
    }

    fn migrate_from_old(old: OldPythonConfig) -> Self {
        VoiceInputConfig {
            server: ServerConfig {
                host: old
                    .server
                    .as_ref()
                    .and_then(|s| s.host.clone())
                    .unwrap_or_else(|| "localhost".into()),
                port: old.server.as_ref().and_then(|s| s.port).unwrap_or(6544),
            },
            hotkey: HotkeyConfig {
                key: old
                    .hotkey
                    .as_ref()
                    .and_then(|h| h.key.clone())
                    .unwrap_or_else(|| "Ctrl+Alt+R".into()),
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
