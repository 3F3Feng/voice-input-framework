//! STT 服务的后台心跳。
//!
//! 以前连接状态只在两个时机更新:启动时连一次,以及设置面板开着、而且是本地
//! 模式时每 3 秒轮询一次服务器面板。远程模式从来不轮询。于是服务挂了,头部
//! 照样是绿点加模型名,录音按钮照样亮着——要等用户按住快捷键说完一句话,才看到
//! 一句连不上的报错,那句话白说了(R12、R13)。
//!
//! 绿点本身也不准:能拉到 `/models` 就算「已连接」,模型还在加载、或者已经加载
//! 失败时头部一样是绿的(R11)。
//!
//! 现在由 Rust 在后台每 5 秒打一次 `{当前 STT 地址}/health`,把「可达 / 加载中 /
//! 加载失败 / 就绪」记在 `AppState` 里,只在状态变化时发 `stt-health` 事件给前端;
//! 开始录音前也看这份状态,不可用就直接说原因,不开始录音。
//!
//! 判定规则(`Tracker`)是纯逻辑,不碰网络也不碰 tauri,单测覆盖。

use serde::Serialize;
use serde_json::Value;
use std::time::Duration;

/// 心跳间隔。本地回环一次 `/health` 几毫秒,5 秒一次谈不上负担;再长,服务挂了
/// 之后头部要很久才变。
pub const INTERVAL: Duration = Duration::from_secs(5);

/// 之前能连上时,连续失败几次才判定为「连不上」。
///
/// 不能一次就判:MLX 推理会占住服务的事件循环,一次较长的转写期间 `/health`
/// 完全可能超时。一次超时就把头部改成「未连接」、把录音按钮置灰,正在说话的
/// 用户会被吓一跳,下一轮又变回来。两次(约 10 秒以上都答不上来)才算数。
const FAILURES_BEFORE_UNREACHABLE: u32 = 2;

/// 从客户端这边看,STT 服务处在什么状态。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SttHealthState {
    /// 还没有探测结果(刚启动、刚换了地址)。这时不拦录音——没有证据说它不行。
    Unknown,
    /// 连不上。
    Unreachable,
    /// 服务在,模型还在加载。
    Loading,
    /// 服务在,模型加载失败了,原因在 `error`。
    Error,
    /// 模型就绪,可以录音。
    Ready,
}

/// 心跳的一次结论,也是 `stt-health` 事件的内容。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SttHealth {
    pub state: SttHealthState,
    /// 服务能不能连上(`Loading` / `Error` / `Ready` 都是能连上)。
    pub reachable: bool,
    /// `/health` 原样报的 `status`(ok / loading / error),连不上时为空。
    pub status: Option<String>,
    pub current_model: Option<String>,
    /// 模型加载失败的原因(`state == Error` 时有值)。
    pub error: Option<String>,
    /// 这份结论说的是哪个地址。地址一换,旧结论就不作数了。
    pub url: String,
}

impl SttHealth {
    pub fn unknown(url: &str) -> Self {
        Self {
            state: SttHealthState::Unknown,
            reachable: false,
            status: None,
            current_model: None,
            error: None,
            url: url.to_string(),
        }
    }

    fn unreachable(url: &str) -> Self {
        // 不带具体的连接错误:它每次可能不一样(拒绝 / 超时),带上的话状态
        // 会在「连不上」之间来回「变化」,白发事件。
        Self {
            state: SttHealthState::Unreachable,
            ..Self::unknown(url)
        }
    }

    /// 从 `/health` 的应答得出状态。
    pub fn from_health(url: &str, data: &Value) -> Self {
        let status = data["status"].as_str().map(str::to_string);
        let error = data["error"]
            .as_str()
            .map(str::trim)
            .filter(|e| !e.is_empty())
            .map(str::to_string);
        let state = match status.as_deref() {
            Some("loading") => SttHealthState::Loading,
            Some("error") => SttHealthState::Error,
            // 不认识的状态(或者没有 status 字段)按就绪处理:能答 /health 就说明
            // 服务在,拦住录音的代价比放行大——真有问题,转写时会把原因报出来。
            _ => SttHealthState::Ready,
        };
        Self {
            state,
            reachable: true,
            error: match state {
                SttHealthState::Error => {
                    Some(error.unwrap_or_else(|| "原因未知,见服务日志".to_string()))
                }
                _ => None,
            },
            status,
            current_model: data["current_model"]
                .as_str()
                .filter(|m| !m.is_empty())
                .map(str::to_string),
            url: url.to_string(),
        }
    }
}

/// 把一次次探测结果折算成状态,只在状态变化时报告。
pub struct Tracker {
    url: String,
    failures: u32,
    current: SttHealth,
}

impl Tracker {
    pub fn new(url: &str) -> Self {
        Self {
            url: url.to_string(),
            failures: 0,
            current: SttHealth::unknown(url),
        }
    }

    /// 喂一次对 `url` 的探测结果。状态变了才返回新状态(用来发事件)。
    pub fn observe(&mut self, url: &str, probe: Result<Value, String>) -> Option<SttHealth> {
        let before = self.current.clone();
        // 客户端被指到了别的地址(切模式、改端口、改远程地址):旧地址的结论和
        // 失败计数一概作废。
        if url != self.url {
            self.url = url.to_string();
            self.failures = 0;
            self.current = SttHealth::unknown(url);
        }
        match probe {
            Ok(data) => {
                self.failures = 0;
                self.current = SttHealth::from_health(url, &data);
            }
            Err(_) => {
                self.failures += 1;
                // 之前就没连上过(未知 / 已经连不上):一次失败就够了,没有「一次
                // 慢转写」可以体谅。之前是连着的:要连续失败够次数才改口。
                let needed = if self.current.reachable {
                    FAILURES_BEFORE_UNREACHABLE
                } else {
                    1
                };
                if self.failures >= needed {
                    self.current = SttHealth::unreachable(url);
                }
            }
        }
        (self.current != before).then(|| self.current.clone())
    }
}

/// 开始录音前的闸门(R13):服务不可用就不开始,直接说原因。
///
/// 以前快捷键路径完全不看连接状态(按钮路径靠 `:disabled` 挡住了,快捷键没有),
/// 说完一整句才看到连不上的报错,这段话白说了。
///
/// 还没有探测结果、或者结论属于别的地址(刚换过地址,心跳还没轮到)时放行:
/// 没有证据说它不行,不能因为心跳慢了一拍就拦住用户。
pub fn recording_gate(health: &SttHealth, current_url: &str) -> Result<(), String> {
    if health.url != current_url {
        return Ok(());
    }
    match health.state {
        SttHealthState::Unknown | SttHealthState::Ready => Ok(()),
        SttHealthState::Unreachable => {
            Err("未连接 STT 服务,这句话不会被识别。请先在设置里启动或连接服务。".to_string())
        }
        SttHealthState::Loading => Err("模型还在加载,请稍等片刻再说。".to_string()),
        SttHealthState::Error => Err(format!(
            "模型加载失败:{}。请在设置里换一个模型或重启服务。",
            health.error.as_deref().unwrap_or("原因未知")
        )),
    }
}

/// 起后台心跳。每一轮都从 `AppState.stt` 现读地址:客户端会在 `set_server_mode`、
/// `set_local_server_config`、`set_server_host`、`connect_effective_server` 里被
/// 重新指向,心跳必须跟着走。
pub fn spawn(app: tauri::AppHandle) {
    use tauri::{Emitter, Manager};

    fn current_url(app: &tauri::AppHandle) -> Option<String> {
        let state = app.state::<crate::AppState>();
        let url = state.stt.lock().ok().map(|c| c.stt_url.clone());
        url
    }

    tauri::async_runtime::spawn(async move {
        let mut tracker: Option<Tracker> = None;
        loop {
            if let Some(url) = current_url(&app) {
                let probe = crate::stt::SttClient::new(&url).get_health().await;
                // 探测期间地址被改了:这次结果说的是旧地址,丢掉,下一轮再说。
                if current_url(&app).as_deref() == Some(url.as_str()) {
                    let t = tracker.get_or_insert_with(|| Tracker::new(&url));
                    if let Some(changed) = t.observe(&url, probe) {
                        let state = app.state::<crate::AppState>();
                        if let Ok(mut slot) = state.stt_health.lock() {
                            *slot = changed.clone();
                        }
                        let _ = app.emit("stt-health", &changed);
                    }
                }
            }
            tokio::time::sleep(INTERVAL).await;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const URL: &str = "http://127.0.0.1:6544";

    fn ok() -> Result<Value, String> {
        Ok(json!({"status": "ok", "current_model": "qwen"}))
    }
    fn down() -> Result<Value, String> {
        Err("连不上".into())
    }

    #[test]
    fn health_statuses_map_to_states() {
        let h = SttHealth::from_health(URL, &json!({"status": "loading"}));
        assert_eq!(h.state, SttHealthState::Loading);
        assert!(h.reachable);
        let h = SttHealth::from_health(URL, &json!({"status": "error", "error": "OOM"}));
        assert_eq!(h.state, SttHealthState::Error);
        assert_eq!(h.error.as_deref(), Some("OOM"));
        // 失败却没给原因,也不能是空的。
        let h = SttHealth::from_health(URL, &json!({"status": "error"}));
        assert!(h.error.is_some());
        let h = SttHealth::from_health(URL, &json!({"status": "ok", "current_model": "m"}));
        assert_eq!(h.state, SttHealthState::Ready);
        assert_eq!(h.current_model.as_deref(), Some("m"));
    }

    /// R12 的核心:服务挂了,状态要自己变;但一次超时(长转写占着事件循环)不算。
    #[test]
    fn a_single_timeout_while_up_is_tolerated_two_are_not() {
        let mut t = Tracker::new(URL);
        assert_eq!(t.observe(URL, ok()).unwrap().state, SttHealthState::Ready);
        assert_eq!(t.observe(URL, down()), None, "一次失败不该改口");
        assert_eq!(
            t.observe(URL, down()).unwrap().state,
            SttHealthState::Unreachable
        );
        // 恢复了立刻改回来。
        assert_eq!(t.observe(URL, ok()).unwrap().state, SttHealthState::Ready);
        // 中间夹一次成功,计数清零。
        assert_eq!(t.observe(URL, down()), None);
        assert_eq!(t.observe(URL, ok()), None, "状态没变就不发事件");
        assert_eq!(t.observe(URL, down()), None);
    }

    #[test]
    fn never_reached_is_unreachable_after_one_failure() {
        let mut t = Tracker::new(URL);
        assert_eq!(
            t.observe(URL, down()).unwrap().state,
            SttHealthState::Unreachable
        );
        assert_eq!(t.observe(URL, down()), None, "一直连不上就别反复发事件");
    }

    #[test]
    fn changing_the_url_resets_the_verdict() {
        let mut t = Tracker::new(URL);
        t.observe(URL, ok());
        let other = "http://10.0.0.2:6544";
        // 新地址第一次就失败:新地址从没连上过,一次就判,不能沿用旧地址的「已连接」。
        let h = t.observe(other, down()).unwrap();
        assert_eq!(h.state, SttHealthState::Unreachable);
        assert_eq!(h.url, other);
    }

    #[test]
    fn loading_to_ready_and_model_changes_are_reported() {
        let mut t = Tracker::new(URL);
        assert_eq!(
            t.observe(URL, Ok(json!({"status": "loading", "current_model": "a"})))
                .unwrap()
                .state,
            SttHealthState::Loading
        );
        assert_eq!(
            t.observe(URL, Ok(json!({"status": "ok", "current_model": "a"})))
                .unwrap()
                .state,
            SttHealthState::Ready
        );
        let h = t
            .observe(URL, Ok(json!({"status": "ok", "current_model": "b"})))
            .unwrap();
        assert_eq!(h.current_model.as_deref(), Some("b"));
    }

    /// R13:服务不可用时不开始录音,并说清楚为什么。
    #[test]
    fn recording_is_refused_with_a_reason_unless_ready_or_unknown() {
        let mut h = SttHealth::unknown(URL);
        assert!(recording_gate(&h, URL).is_ok(), "没有探测结果时不拦");
        h.state = SttHealthState::Ready;
        assert!(recording_gate(&h, URL).is_ok());

        h.state = SttHealthState::Unreachable;
        assert!(recording_gate(&h, URL)
            .unwrap_err()
            .contains("未连接 STT 服务"));
        h.state = SttHealthState::Loading;
        assert!(recording_gate(&h, URL)
            .unwrap_err()
            .contains("模型还在加载"));
        h.state = SttHealthState::Error;
        h.error = Some("OOM".into());
        let e = recording_gate(&h, URL).unwrap_err();
        assert!(e.contains("模型加载失败") && e.contains("OOM"), "{}", e);

        // 结论属于旧地址(刚换过地址,心跳还没轮到):不拦。
        h.state = SttHealthState::Unreachable;
        assert!(recording_gate(&h, "http://127.0.0.1:7000").is_ok());
    }
}
