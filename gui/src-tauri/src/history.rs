//! 识别历史(F16):存在应用数据目录的 `history.json` 里,重启不丢。
//!
//! 以前历史只在前端内存里、最多 20 条,关一次应用就没了;想找回半小时前说过的
//! 一段话只能重说一遍。
//!
//! 放在 Rust 这边而不是前端的 localStorage:数据要和 config.json 放在一起(用户
//! 知道去哪儿删),写盘要能原子替换,托盘启动时也要能读到最近一条。
//!
//! 内存里始终有一份完整列表,磁盘只是它的持久化:
//! - 「保存识别历史」关着时,新记录照样进内存(本次会话里还能找回、搜索),
//!   但打上 `transient` 标记,**永远不会被写进文件**——包括之后因为删除别的条目
//!   而重写文件的那一下;
//! - 删除、清空一律落盘:用户删掉的东西就该从磁盘上消失,不管开关状态。

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{Manager, State};

/// 最多保留多少条。一条几百字节,500 条也就一两百 KB,每次整份重写不成问题;
/// 再多的话列表渲染和整份重写都开始有感觉,而几个月前的一句话也没人会翻。
pub const HISTORY_CAP: usize = 500;

const FILE_NAME: &str = "history.json";
/// 文件格式版本。以后改格式时靠它判断要不要迁移,而不是猜字段。
const FORMAT_VERSION: u32 = 1;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HistoryEntry {
    /// 唯一 id(毫秒时间戳,同一毫秒内的往后顺延),前端删除时用它指认条目。
    pub id: i64,
    /// 识别完成的时间,Unix 毫秒。显示格式交给前端(今天只显示时分)。
    pub ts: i64,
    /// 最终结果(经过 LLM 整理的话就是整理后的文字)。
    pub text: String,
    /// STT 原文。只在和 `text` 不同时才存:没开后处理、或后处理失败退回原文时
    /// 两者一样,存两份只是占地方,前端也据此决定要不要显示「原文」切换。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub original: Option<String>,
    /// 当时用的 STT 模型,拿得到才有。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    /// 「保存识别历史」关着时加进来的:只活在内存里,写文件时跳过。
    #[serde(skip)]
    pub transient: bool,
}

#[derive(Serialize, Deserialize)]
struct HistoryFile {
    version: u32,
    /// 新的在前。
    entries: Vec<HistoryEntry>,
}

/// `history_list` 的返回:`total` 是不算搜索过滤的总条数。前端要分清「一条历史都
/// 没有」(显示空状态)和「有,只是没搜到」(显示「没有匹配的记录」)。
#[derive(Debug, Serialize)]
pub struct HistoryPage {
    pub entries: Vec<HistoryEntry>,
    pub total: usize,
}

// ── 纯逻辑(不碰文件、不碰 Tauri,单测都在这一层) ──

/// 原文只在和最终结果不同时才留。比较时忽略首尾空白:服务端有时给 LLM 输出
/// 多带一个换行,那不算「整理过」。
pub fn normalize_original(text: &str, original: Option<&str>) -> Option<String> {
    let o = original?.trim();
    if o.is_empty() || o == text.trim() {
        None
    } else {
        Some(o.to_string())
    }
}

/// 新 id:用当前毫秒,但保证比已有的最大 id 大——同一毫秒连着加两条(或者系统时钟
/// 往回调过)时,id 撞了的话删一条会把另一条一起删掉。
pub fn next_id(entries: &[HistoryEntry], now_ms: i64) -> i64 {
    let max = entries.iter().map(|e| e.id).max().unwrap_or(i64::MIN);
    now_ms.max(max.saturating_add(1))
}

/// 放到最前面,超出上限的从最旧的一头丢掉。
pub fn push_capped(entries: &mut Vec<HistoryEntry>, entry: HistoryEntry, cap: usize) {
    entries.insert(0, entry);
    entries.truncate(cap);
}

/// 按搜索词过滤。空白分开的每个词都要出现(在结果或原文里),不分大小写。
///
/// 原文也要搜:用户记得的往往是自己说的原话,而 LLM 可能已经换了说法。
pub fn filter<'a>(entries: &'a [HistoryEntry], query: &str) -> Vec<&'a HistoryEntry> {
    let terms: Vec<String> = query.split_whitespace().map(str::to_lowercase).collect();
    if terms.is_empty() {
        return entries.iter().collect();
    }
    entries
        .iter()
        .filter(|e| {
            let hay = format!(
                "{}\n{}",
                e.text.to_lowercase(),
                e.original.as_deref().unwrap_or("").to_lowercase()
            );
            terms.iter().all(|t| hay.contains(t.as_str()))
        })
        .collect()
}

/// 写进文件的内容。`transient` 的条目不写——这就是「关掉保存」的全部含义。
pub fn serialize(entries: &[HistoryEntry]) -> Result<String, String> {
    let file = HistoryFile {
        version: FORMAT_VERSION,
        entries: entries.iter().filter(|e| !e.transient).cloned().collect(),
    };
    serde_json::to_string(&file).map_err(|e| format!("序列化识别历史失败: {}", e))
}

/// 读文件内容。空白文字、空文本的条目丢掉,超上限的截掉(手改过文件也不至于炸)。
pub fn parse(data: &str) -> Result<Vec<HistoryEntry>, String> {
    let file: HistoryFile =
        serde_json::from_str(data).map_err(|e| format!("识别历史文件解析失败: {}", e))?;
    let mut entries: Vec<HistoryEntry> = file
        .entries
        .into_iter()
        .filter(|e| !e.text.trim().is_empty())
        .collect();
    entries.truncate(HISTORY_CAP);
    Ok(entries)
}

// ── 存储 ──

/// 内存里的那份。`None` = 还没从磁盘读过。
///
/// 所有读改写都在这一把锁里做完(包括写盘):两条识别结果几乎同时进来时,
/// 各自「读文件 → 加一条 → 写回」会让后写的那次把先加的那条盖掉。
static STORE: Mutex<Option<Vec<HistoryEntry>>> = Mutex::new(None);

fn history_path(app: &tauri::AppHandle) -> PathBuf {
    app.path()
        .app_data_dir()
        .unwrap_or_else(|_| crate::config::home_dir().join(".config/voice-input"))
        .join(FILE_NAME)
}

fn load_from_disk(path: &Path) -> Vec<HistoryEntry> {
    let Ok(data) = fs::read_to_string(path) else {
        // 没有文件是常态(第一次用、或者清空过),不是错误。
        return Vec::new();
    };
    match parse(&data) {
        Ok(entries) => entries,
        Err(e) => {
            // 和 config.json 一样:读不懂的文件先挪到一边再从空列表开始。留在原地的话,
            // 下一次加记录就会用新列表把它覆盖掉,原来的历史再也找不回来。
            crate::log_error!("[history] {}({}),本次从空列表开始", e, path.display());
            let backup = path.with_extension("json.corrupt");
            if fs::rename(path, &backup).is_ok() {
                crate::log_error!("[history] 原文件已保留为 {}", backup.display());
            }
            Vec::new()
        }
    }
}

/// 原子写:先写临时文件再 rename,理由同 `config.rs` 的 `save`——写到一半被强杀
/// 留下半截文件,下次启动整份历史都读不出来。
fn write_to_disk(path: &Path, entries: &[HistoryEntry]) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("创建数据目录失败: {}", e))?;
    }
    let json = serialize(entries)?;
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, json).map_err(|e| format!("写入识别历史失败: {}", e))?;
    fs::rename(&tmp, path).map_err(|e| {
        let _ = fs::remove_file(&tmp);
        format!("替换识别历史文件失败: {}", e)
    })
}

/// 拿到(必要时先从磁盘读出)内存里的列表,在锁内做 `f`。
fn with_store<T>(
    app: &tauri::AppHandle,
    f: impl FnOnce(&Path, &mut Vec<HistoryEntry>) -> Result<T, String>,
) -> Result<T, String> {
    let path = history_path(app);
    let mut guard = STORE.lock().map_err(|e| e.to_string())?;
    let entries = guard.get_or_insert_with(|| load_from_disk(&path));
    f(&path, entries)
}

fn save_enabled(state: &crate::AppState) -> bool {
    state
        .config
        .lock()
        .map(|c| c.ui.save_history)
        .unwrap_or(true)
}

/// 启动时把托盘「复制最近一条」接上上次的最后一条,不然重启后那一项是灰的,
/// 要等说完第一句话才能用。「保存识别历史」关着时不接:用户关它就是不想留痕。
pub fn seed_tray(app: &tauri::AppHandle) {
    let state = app.state::<crate::AppState>();
    if !save_enabled(&state) {
        return;
    }
    let latest = with_store(app, |_, entries| {
        Ok(entries.first().map(|e| e.text.clone()))
    });
    if let Ok(Some(text)) = latest {
        crate::tray::remember_result(app, &text);
    }
}

// ── 命令 ──

#[tauri::command]
pub async fn history_list(
    app: tauri::AppHandle,
    query: Option<String>,
) -> Result<HistoryPage, String> {
    with_store(&app, |_, entries| {
        Ok(HistoryPage {
            entries: filter(entries, query.as_deref().unwrap_or(""))
                .into_iter()
                .cloned()
                .collect(),
            total: entries.len(),
        })
    })
}

/// 记一条。返回加进去的条目;「保存识别历史」关着时照样返回(只在内存里)。
#[tauri::command]
pub async fn history_add(
    app: tauri::AppHandle,
    state: State<'_, crate::AppState>,
    text: String,
    original: Option<String>,
) -> Result<Option<HistoryEntry>, String> {
    if text.trim().is_empty() {
        return Ok(None);
    }
    let persist = save_enabled(&state);
    // 模型名取心跳最近一次看到的:前端手里的那个可能是下拉框里选了还没加载完的。
    let model = state
        .stt_health
        .lock()
        .ok()
        .and_then(|h| h.current_model.clone())
        .filter(|m| !m.is_empty());
    let now = chrono::Utc::now().timestamp_millis();
    with_store(&app, |path, entries| {
        let entry = HistoryEntry {
            id: next_id(entries, now),
            ts: now,
            original: normalize_original(&text, original.as_deref()),
            text,
            model,
            transient: !persist,
        };
        push_capped(entries, entry.clone(), HISTORY_CAP);
        if persist {
            // 写盘失败不影响这次识别本身(结果已经在屏幕上了),记日志、告诉前端。
            write_to_disk(path, entries).inspect_err(|e| crate::log_error!("[history] {}", e))?;
        }
        Ok(Some(entry))
    })
}

#[tauri::command]
pub async fn history_delete(app: tauri::AppHandle, id: i64) -> Result<(), String> {
    with_store(&app, |path, entries| {
        let before = entries.len();
        entries.retain(|e| e.id != id);
        if entries.len() == before {
            return Ok(());
        }
        write_to_disk(path, entries)
    })
}

/// 把一条的文字换成用户改过的版本(F16 后续)。
///
/// 结果框可以改错字再复制 / 输入,可历史里记的一直是改之前的识别结果 —— 回头从历史里
/// 再拿,错字又回来了。改之前那份放进 `original`(没有的话),「原文」切换照样看得到。
#[tauri::command]
pub async fn history_update_text(
    app: tauri::AppHandle,
    state: State<'_, crate::AppState>,
    id: i64,
    text: String,
) -> Result<(), String> {
    if text.trim().is_empty() {
        return Ok(());
    }
    let persist = save_enabled(&state);
    with_store(&app, |path, entries| {
        let Some(entry) = entries.iter_mut().find(|e| e.id == id) else {
            return Ok(());
        };
        if entry.text == text {
            return Ok(());
        }
        if entry.original.is_none() {
            entry.original = Some(std::mem::take(&mut entry.text));
        }
        entry.text = text;
        if persist && !entry.transient {
            write_to_disk(path, entries)?;
        }
        Ok(())
    })
}

/// 清空:内存、文件一起清,托盘里的「最近一条」也忘掉——用户点清空,
/// 就不该还能从托盘里把上一句复制出来。
#[tauri::command]
pub async fn history_clear(app: tauri::AppHandle) -> Result<(), String> {
    with_store(&app, |path, entries| {
        entries.clear();
        match fs::remove_file(path) {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(format!("删除识别历史文件失败: {}", e)),
        }
    })?;
    crate::tray::forget_result(&app);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(id: i64, text: &str) -> HistoryEntry {
        HistoryEntry {
            id,
            ts: id,
            text: text.into(),
            original: None,
            model: None,
            transient: false,
        }
    }

    #[test]
    fn original_is_kept_only_when_it_differs() {
        assert_eq!(
            normalize_original("你好。", Some("你好")),
            Some("你好".into())
        );
        assert_eq!(normalize_original("你好", Some("你好")), None);
        // LLM 多带的换行不算「整理过」。
        assert_eq!(normalize_original("你好\n", Some(" 你好")), None);
        assert_eq!(normalize_original("你好", Some("   ")), None);
        assert_eq!(normalize_original("你好", None), None);
    }

    #[test]
    fn ids_are_unique_even_within_one_millisecond() {
        let entries = vec![entry(1000, "a")];
        assert_eq!(next_id(&entries, 1000), 1001);
        // 时钟往回调过也不能撞。
        assert_eq!(next_id(&entries, 5), 1001);
        assert_eq!(next_id(&entries, 2000), 2000);
        assert_eq!(next_id(&[], 42), 42);
    }

    #[test]
    fn push_puts_newest_first_and_drops_the_oldest() {
        let mut entries = vec![entry(2, "二"), entry(1, "一")];
        push_capped(&mut entries, entry(3, "三"), 2);
        let ids: Vec<i64> = entries.iter().map(|e| e.id).collect();
        assert_eq!(ids, vec![3, 2]);
    }

    #[test]
    fn cap_holds_at_the_limit() {
        let mut entries = Vec::new();
        for i in 0..(HISTORY_CAP as i64 + 20) {
            push_capped(&mut entries, entry(i, "x"), HISTORY_CAP);
        }
        assert_eq!(entries.len(), HISTORY_CAP);
        assert_eq!(entries[0].id, HISTORY_CAP as i64 + 19);
    }

    #[test]
    fn filter_matches_every_term_case_insensitively() {
        let entries = vec![
            entry(3, "明天下午开会讨论 Roadmap"),
            entry(2, "roadmap 已经发出去了"),
            entry(1, "晚饭吃什么"),
        ];
        let hit: Vec<i64> = filter(&entries, "ROADMAP").iter().map(|e| e.id).collect();
        assert_eq!(hit, vec![3, 2]);
        let hit: Vec<i64> = filter(&entries, "roadmap  开会")
            .iter()
            .map(|e| e.id)
            .collect();
        assert_eq!(hit, vec![3]);
        assert!(filter(&entries, "不存在").is_empty());
    }

    #[test]
    fn empty_query_returns_everything_in_order() {
        let entries = vec![entry(2, "b"), entry(1, "a")];
        assert_eq!(filter(&entries, "  ").len(), 2);
    }

    /// 用户记得的常是自己说的原话,LLM 可能已经改写了。
    #[test]
    fn filter_also_searches_the_original() {
        let mut e = entry(1, "我们周五发布。");
        e.original = Some("那个我们就是周五发吧".into());
        let entries = vec![e];
        assert_eq!(filter(&entries, "就是").len(), 1);
    }

    #[test]
    fn round_trips_through_json() {
        let mut e = entry(7, "整理后");
        e.original = Some("原文".into());
        e.model = Some("qwen3-asr".into());
        let json = serialize(&[e.clone(), entry(6, "只有结果")]).unwrap();
        let back = parse(&json).unwrap();
        assert_eq!(back, vec![e, entry(6, "只有结果")]);
    }

    /// 没有原文、没有模型的条目,文件里就不出现这两个键。
    #[test]
    fn absent_fields_are_not_written() {
        let json = serialize(&[entry(1, "a")]).unwrap();
        assert!(!json.contains("original"));
        assert!(!json.contains("model"));
        assert!(!json.contains("transient"));
    }

    /// 关掉「保存识别历史」时加的条目,之后哪怕因为删别的条目而重写文件,也不会被写进去。
    #[test]
    fn transient_entries_never_reach_the_file() {
        let mut secret = entry(2, "不该落盘");
        secret.transient = true;
        let json = serialize(&[secret, entry(1, "早就存过的")]).unwrap();
        let back = parse(&json).unwrap();
        assert_eq!(back, vec![entry(1, "早就存过的")]);
    }

    #[test]
    fn parse_drops_blank_entries_and_enforces_the_cap() {
        let mut entries: Vec<HistoryEntry> = (0..(HISTORY_CAP as i64 + 5))
            .map(|i| entry(i, "x"))
            .collect();
        entries.insert(0, entry(-1, "   "));
        let json = serialize(&entries).unwrap();
        let back = parse(&json).unwrap();
        assert_eq!(back.len(), HISTORY_CAP);
        assert_eq!(back[0].id, 0);
    }

    #[test]
    fn garbage_is_an_error_not_an_empty_list() {
        // 读不懂必须报出来(调用方会把文件挪走备份),不能当成「没有历史」然后覆盖掉。
        assert!(parse("{ not json").is_err());
        assert!(parse("[]").is_err());
    }
}
