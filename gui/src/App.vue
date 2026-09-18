<template>
  <div class="app">
    <!-- Header -->
    <header class="header">
      <div class="header-left">
        <span class="app-icon">🎙️</span>
        <span :class="['conn-dot', connected ? 'on' : 'off']"></span>
        <span class="conn-text">{{ connected ? currentModelName : '未连接' }}</span>
      </div>
      <div class="header-right">
        <button class="header-btn" @click="minimizeToTray" title="最小化到托盘">─</button>
        <button class="header-btn" @click="showSettings = !showSettings" :class="{ active: showSettings }">
          {{ showSettings ? '✕' : '⚙' }}
        </button>
      </div>
    </header>

    <!-- Toast -->
    <div class="toast-container">
      <transition-group name="toast">
        <div v-for="t in toasts" :key="t.id" :class="['toast', t.type]">{{ t.msg }}</div>
      </transition-group>
    </div>

    <!-- Settings Panel -->
    <transition name="slide">
      <div v-if="showSettings" class="settings-panel">
        <div class="settings-scroll">
          <!-- 服务器 -->
          <div class="s-section">
            <div class="s-title" style="display:flex;justify-content:space-between;align-items:center">
              <span>服务器</span>
              <button class="s-btn" @click="refreshServers" :disabled="serversLoading">
                {{ serversLoading ? '...' : '刷新' }}
              </button>
            </div>

            <!-- 本地管理 / 远程连接 -->
            <div class="s-row mode-switch">
              <button :class="['s-btn', 'mode-btn', { active: serverMode === 'local' }]"
                @click="switchMode('local')" :disabled="modeBusy">本地管理</button>
              <button :class="['s-btn', 'mode-btn', { active: serverMode === 'remote' }]"
                @click="switchMode('remote')" :disabled="modeBusy">远程连接</button>
            </div>

            <!-- 远程:只连接，不管理进程 -->
            <template v-if="serverMode === 'remote'">
              <div class="s-row" style="margin-top:8px">
                <input class="s-input" v-model="serverHost" placeholder="localhost 或 http://1.2.3.4:6544"
                  @keyup.enter="updateServer" @change="onServerSettingChange" />
                <input class="s-input s-port" v-model.number="serverPort" type="number"
                  @keyup.enter="updateServer" @change="onServerSettingChange" />
                <button class="s-btn" @click="updateServer" :disabled="connecting">{{ connecting ? '...' : '连接' }}</button>
              </div>
              <div class="s-tip">只连接，不管理进程。服务需要在对端自行启动。主机可填裸主机名，也可填完整 URL。</div>
            </template>

            <!-- 本地：两个服务的状态与启停 -->
            <template v-else>
              <div v-for="row in serverRows" :key="row.kind" class="perm-row">
                <div class="perm-info">
                  <div class="perm-head">
                    <span class="s-label">{{ row.label }}</span>
                    <span :class="['perm-state', row.chipClass]">{{ row.chipText }}</span>
                    <span v-if="row.ownerText" :class="['perm-state', row.ownerClass]">{{ row.ownerText }}</span>
                  </div>
                  <div class="s-tip" style="margin-top:2px">{{ row.desc }}</div>
                </div>
                <div class="perm-actions">
                  <button v-if="!row.isUp" class="s-btn" @click="startSrv(row.kind)" :disabled="srvBusy === row.kind">
                    {{ srvBusy === row.kind ? '...' : '启动' }}
                  </button>
                  <button v-else class="s-btn" @click="stopSrv(row.kind)"
                    :disabled="srvBusy === row.kind || !row.canStop"
                    :title="row.canStop ? '' : '外部进程，本应用不会停止它'">
                    {{ srvBusy === row.kind ? '...' : '停止' }}
                  </button>
                  <button class="s-btn" @click="restartSrv(row.kind)" :disabled="srvBusy === row.kind || !row.canRestart">
                    重启
                  </button>
                </div>
              </div>

              <!-- 路径（应用装在 /Applications，仓库在别处，只能配置） -->
              <div class="s-row" style="margin-top:8px">
                <input class="s-input" v-model="repoPath" placeholder="仓库路径（含 services/stt_server.py）" @change="saveLocal" />
                <button class="s-btn" @click="detectPaths" :disabled="detecting">{{ detecting ? '...' : '自动探测' }}</button>
              </div>
              <div class="s-row" style="margin-top:4px">
                <input class="s-input" v-model="pythonPath" placeholder="Python 解释器（如 .venv/bin/python）" @change="saveLocal" />
              </div>
              <div class="s-row" style="margin-top:4px">
                <span class="s-tip" style="margin:0">端口</span>
                <input class="s-input s-port" v-model.number="sttPort" type="number" title="STT 端口" @change="saveLocal" />
                <input class="s-input s-port" v-model.number="llmPort" type="number" title="LLM 端口" @change="saveLocal" />
                <label class="toggle"><input type="checkbox" v-model="localAutoStart" @change="saveLocal" /><span class="slider"></span></label>
                <span class="s-label">随应用启动</span>
              </div>

              <div v-if="pathProblem" class="s-tip srv-problem">⚠ {{ pathProblem }}</div>

              <!-- 子进程输出：起不来的时候唯一能看的东西 -->
              <div v-if="serverLogLines.length" class="srv-logs">
                <div v-for="(l, i) in serverLogLines" :key="i" class="srv-log-line">{{ l }}</div>
              </div>
              <div v-if="serverLogPaths.length" class="s-tip">日志文件：{{ serverLogPaths.join('　') }}</div>
              <div class="s-tip">
                只会停止本应用启动的服务。你自己在终端里跑的会被标成「外部」——直接连接使用，不会重复启动，也不会被停掉。
              </div>
            </template>
          </div>

          <!-- Models -->
          <div class="s-section">
            <div class="s-title">STT 模型</div>
            <select class="s-select" v-model="sttModel" @change="switchStt">
              <option v-for="m in sttModels" :key="m.name" :value="m.name">
                {{ m.name }} {{ m.is_loaded ? '✓' : '' }}
              </option>
            </select>
            <div v-if="sttLoading" class="s-loading">切换中...</div>
          </div>

          <div class="s-section">
            <div class="s-title">LLM 后处理</div>
            <div class="s-row">
              <label class="toggle"><input type="checkbox" v-model="llmEnabled" @change="toggleLlm" /><span class="slider"></span></label>
              <span class="s-label">{{ llmEnabled ? '已启用' : '已禁用' }}</span>
            </div>
            <div v-if="llmEnabled" style="margin-top: 8px;">
              <select class="s-select" v-model="llmModel" @change="switchLlm">
                <option v-for="m in llmModels" :key="m.name" :value="m.name">
                  {{ m.name }} {{ m.is_loaded ? '✓' : '' }}
                </option>
              </select>
            </div>
          </div>

          <!-- Audio -->
          <div class="s-section">
            <div class="s-title">麦克风</div>
            <div class="s-row">
              <select class="s-select" v-model="selectedDevice" @change="onDeviceChange" style="flex:1">
                <option :value="null">默认设备</option>
                <option v-for="d in audioDevices" :key="d.name" :value="d.name">
                  {{ d.default ? `${d.name}（系统默认）` : d.name }}
                </option>
              </select>
              <button class="s-btn" @click="refreshDevices" title="刷新">🔄</button>
            </div>
          </div>

          <!-- macOS 系统权限 -->
          <div class="s-section" v-if="perms?.is_macos">
            <div class="s-title" style="display:flex;justify-content:space-between;align-items:center">
              <span>系统权限</span>
              <button class="s-btn" @click="refreshPermissions" :disabled="permsLoading">
                {{ permsLoading ? '...' : '刷新' }}
              </button>
            </div>
            <div v-for="row in permissionRows" :key="row.key" class="perm-row">
              <div class="perm-info">
                <div class="perm-head">
                  <span class="s-label">{{ row.label }}</span>
                  <span :class="['perm-state', permStateClass(row.status)]">{{ permStateText(row.status) }}</span>
                </div>
                <div class="s-tip" style="margin-top:2px">{{ row.desc }}</div>
              </div>
              <div class="perm-actions">
                <button v-if="row.canRequest" class="s-btn" @click="requestPerm(row.key)" :disabled="permBusy === row.key">
                  {{ permBusy === row.key ? '...' : '请求授权' }}
                </button>
                <button class="s-btn" @click="openPermSettings(row.key)">打开设置</button>
              </div>
            </div>
            <div class="s-tip" style="margin-top:6px">
              已拒绝的权限系统不会再弹窗，需在「系统设置 → 隐私与安全性」中手动勾选；辅助功能改动后可能需要重启本应用。
            </div>
          </div>

          <!-- Hotkey -->
          <div class="s-section">
            <div class="s-title">快捷键</div>
            <div class="s-row">
              <input class="s-input hotkey-field" v-model="hotkeyStr" readonly :placeholder="defaultHotkey"
                :class="{ recording: hotkeyRecording }" @click="startHotkeyRecording" />
              <button class="s-btn" @click="startHotkeyRecording">{{ hotkeyRecording ? '取消' : '录制' }}</button>
              <button class="s-btn" @click="applyHotkey" :disabled="!hotkeyChanged">应用</button>
            </div>
            <div v-if="hotkeyRecording" class="s-tip">请按下快捷键组合...</div>
          </div>

          <!-- Toggles -->
          <div class="s-section">
            <div class="s-row">
              <label class="toggle"><input type="checkbox" v-model="autoInputEnabled" @change="onAutoInputToggle" /><span class="slider"></span></label>
              <span class="s-label">自动输入到窗口</span>
            </div>
            <div class="s-row" style="margin-top:6px">
              <label class="toggle"><input type="checkbox" v-model="autoStart" @change="toggleAutoStart" /><span class="slider"></span></label>
              <span class="s-label">开机自启动</span>
            </div>
            <div class="s-row" style="margin-top:6px">
              <label class="toggle"><input type="checkbox" v-model="startMinimized" @change="toggleStartMinimized" /><span class="slider"></span></label>
              <span class="s-label">启动时最小化</span>
            </div>
          </div>

          <!-- LLM Prompt -->
          <div v-if="llmEnabled" class="s-section">
            <div class="s-title">提示词</div>
            <textarea class="s-textarea" v-model="promptText" rows="3" placeholder="LLM 后处理提示词..." />
            <div class="s-row" style="margin-top:4px">
              <button class="s-btn" @click="loadPrompt" :disabled="promptLoading">加载</button>
              <button class="s-btn" @click="savePrompt" :disabled="promptLoading">保存</button>
              <span v-if="promptStatus" class="s-tip">{{ promptStatus }}</span>
            </div>
          </div>

          <!-- Update -->
          <div class="s-section">
            <div class="s-title">软件更新</div>
            <div v-if="updateInfo" class="update-info">
              <span :class="updateInfo.available ? 'update-new' : 'update-ok'">
                {{ updateInfo.available ? `新版本: ${updateInfo.latest_version}` : '已是最新版本' }}
              </span>
              <span class="s-tip">当前: v{{ updateInfo.current_version }}</span>
              <p v-if="updateInfo.body && updateInfo.available" class="update-body">{{ updateInfo.body }}</p>
            </div>
            <div v-if="updateStatus" class="s-tip" :class="{ ok: updateStatusType === 'ok' }">{{ updateStatus }}</div>
            <div class="s-row" style="margin-top:4px">
              <button class="s-btn" @click="doCheckUpdate" :disabled="updateChecking">
                {{ updateChecking ? '检查中...' : '检查更新' }}
              </button>
              <button v-if="updateInfo?.available" class="s-btn" @click="doInstallUpdate" :disabled="updateInstalling" style="background:var(--green);color:#000">
                {{ updateInstalling ? '下载中...' : '下载安装' }}
              </button>
            </div>
          </div>

          <!-- Debug Log -->
          <div class="s-section">
            <div class="s-title" style="display:flex;justify-content:space-between">
              <span>调试日志</span>
              <span style="color:var(--muted);font-size:0.65rem">{{ guiLogs.length }} 条</span>
            </div>
            <div class="log-box" ref="logBoxRef">
              <div v-for="(entry, i) in guiLogs" :key="i" :class="['log-entry', entry.level]">
                {{ entry.msg }}
              </div>
              <div v-if="guiLogs.length === 0" class="log-empty">暂无日志</div>
            </div>
            <div class="s-row" style="margin-top:4px">
              <button class="s-btn" @click="guiLogs = []">清空</button>
            </div>
          </div>
        </div>
      </div>
    </transition>

    <!-- Main Content -->
    <div class="main" v-show="!showSettings">
      <!-- 缺权限提示(仅 macOS) -->
      <div v-if="missingPermLabels.length" class="perm-banner" @click="showSettings = true">
        ⚠️ {{ missingPermLabels.join('、') }}未授权，相关功能不可用 · 点击前往授权
      </div>

      <!-- Record Button -->
      <div class="record-area">
        <button
          @mousedown="startRecord"
          @mouseup="stopRecord"
          @mouseleave="stopRecord"
          :class="['record-btn', { active: recording, processing: loading }]"
          :disabled="!connected || loading"
        >
          <div class="record-ring"></div>
          <span class="record-icon">{{ recording ? '⏹' : '🎤' }}</span>
        </button>
        <div class="record-status">
          <span v-if="recording" class="status-rec">录音中 {{ timerText }}</span>
          <span v-else-if="loading" class="status-proc">{{ llmProcessing ? 'LLM 处理中' : '识别中' }} {{ processingTimerText }}</span>
          <span v-else-if="connected" class="status-ready">按住说话 · {{ displayHotkey }}</span>
          <span v-else class="status-off">未连接服务器</span>
        </div>
      </div>

      <!-- Audio Level Meter -->
      <div v-if="recording" class="main-level-meter">
        <div class="main-level-bar">
          <div class="main-level-fill" :style="{ width: audioLevel * 100 + '%' }"></div>
        </div>
      </div>

      <!-- Result -->
      <div class="result-area" v-if="result || loading">
        <div v-if="loading && !recording" class="result-loading">
          <div class="spinner"></div>
        </div>
        <div v-if="result" class="result-content">
          <p class="result-text" @click="copyResult">{{ result }}</p>
          <div class="result-actions">
            <button class="r-btn" @click="copyResult" :class="{ ok: copyFeedback }">
              {{ copyFeedback ? '已复制 ✓' : '📋 复制' }}
            </button>
            <button class="r-btn" @click="doAutoInput">⌨️ 输入</button>
            <button class="r-btn" @click="clearResult">✕</button>
          </div>
        </div>
      </div>

      <!-- History -->
      <div class="history-area" v-if="history.length > 0 && !result && !loading">
        <div class="history-title">最近识别</div>
        <div class="history-scroll">
          <div v-for="(item, i) in history" :key="i" class="history-item" @click="result = item.text">
            <span class="history-text">{{ item.text }}</span>
            <span class="history-time">{{ item.time }}</span>
          </div>
        </div>
      </div>

      <!-- Empty state -->
      <div class="empty-state" v-if="!result && !loading && !recording && history.length === 0">
        <div class="empty-icon">🎙️</div>
        <div class="empty-text">按住按钮或按 {{ displayHotkey }} 开始语音输入</div>
      </div>
    </div>

    <!-- Footer -->
    <footer class="footer">
      <span class="footer-text">v{{ version }}</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// ── Types ──
interface ModelInfo { name: string; is_loaded: boolean; }
// 服务器：本地管理 / 远程连接
type ServerMode = "local" | "remote";
type ServerKind = "stt" | "llm";
type ServerState = "not_configured" | "stopped" | "starting" | "running" | "failed";
interface ServerStatus {
  kind: ServerKind;
  state: ServerState;
  port: number;
  /** 由本应用拉起（可停）；false 表示外部进程，只连不管。 */
  managed: boolean;
  pid: number | null;
  current_model: string | null;
  detail: string | null;
  log_path: string | null;
  recent_logs: string[];
}
interface LocalPathReport {
  repo_path: string | null;
  python_path: string | null;
  repo_ok: boolean;
  python_ok: boolean;
  problem: string | null;
}
interface ServerReport {
  mode: ServerMode;
  stt: ServerStatus;
  llm: ServerStatus;
  local_paths: LocalPathReport;
  remote_url: string;
}
interface DetectResult { repo_path: string | null; python_path: string | null; problem: string | null; }
interface LocalServerConfig {
  repo_path: string | null;
  python_path: string | null;
  stt_port: number;
  llm_port: number;
  stt_model: string | null;
  llm_model: string | null;
  auto_start: boolean;
}
interface VoiceInputConfig {
  // mode / local 是后加的：旧 config.json 里没有这两项，Rust 端有 serde 默认值，
  // 读出来一定是 remote + 空 local。
  server: { host: string; port: number; mode: ServerMode; local: LocalServerConfig };
  hotkey: { key: string; distinguish_left_right: boolean };
  ui: { start_minimized: boolean; use_floating_indicator: boolean; use_tray: boolean; opacity: number; auto_input?: boolean };
  audio: { device: string | null; language: string };
  llm: { enabled: boolean };
  _version: string;
}
interface HistoryItem { text: string; time: string; }
/** get_audio_devices 的真实返回：见 src-tauri/src/audio.rs 的 AudioDeviceInfo。
 *  这里以前声明成 Record<string, string>，于是下拉框把整个对象序列化出来当选项名，
 *  选中后写进 cfg.audio.device 的也是个对象，serde 那头直接拒收。 */
interface AudioDeviceInfo { name: string; channels: number; default: boolean; }
// macOS TCC 权限(非 macOS 上 is_macos=false 且三项都是 granted)
type PermissionStatus = "granted" | "denied" | "not_determined" | "restricted";
type PermissionKey = "microphone" | "input_monitoring" | "accessibility";
interface PermissionReport {
  is_macos: boolean;
  microphone: PermissionStatus;
  input_monitoring: PermissionStatus;
  accessibility: PermissionStatus;
}

// ── State ──
const recording = ref(false);
const connected = ref(false);
const connecting = ref(false);
const loading = ref(false);
const result = ref("");
const version = ref("2.0.2");
const showSettings = ref(false);
const copyFeedback = ref(false);
const history = ref<HistoryItem[]>([]);

const sttModels = ref<ModelInfo[]>([]);
const llmModels = ref<ModelInfo[]>([]);
const sttModel = ref("");
const llmModel = ref("");
const sttLoading = ref(false);
const llmLoading = ref(false);
const promptLoading = ref(false);
const promptStatus = ref("");

const serverHost = ref("localhost");
const serverPort = ref(6544);

// 服务器管理
const serverMode = ref<ServerMode>("remote");
const serverReport = ref<ServerReport | null>(null);
const serversLoading = ref(false);
const modeBusy = ref(false);
const detecting = ref(false);
const srvBusy = ref<ServerKind | "">("");
const repoPath = ref("");
const pythonPath = ref("");
const sttPort = ref(6544);
const llmPort = ref(6545);
const localAutoStart = ref(false);
let serverPollTimer: ReturnType<typeof setInterval> | null = null;
const llmEnabled = ref(true);
const promptText = ref("");
const autoInputEnabled = ref(false);
const autoStart = ref(false);
const startMinimized = ref(false);

const elapsedMs = ref(0);
const processingMs = ref(0);
const toasts = ref<{ id: number; msg: string; type: string }[]>([]);
const audioLevel = ref(0);
const llmProcessing = ref(false);

const hotkeyStr = ref("");
const hotkeyRecording = ref(false);
const hotkeyChanged = ref(false);
const hotkeyMsg = ref("");
const defaultHotkey = "left_ctrl+left_alt";
const audioDevices = ref<AudioDeviceInfo[]>([]);
const selectedDevice = ref<string | null>(null);

// 权限
const perms = ref<PermissionReport | null>(null);
const permsLoading = ref(false);
const permBusy = ref<PermissionKey | "">("");
const PERM_META: { key: PermissionKey; label: string; desc: string }[] = [
  { key: "microphone", label: "麦克风", desc: "录音识别需要" },
  { key: "input_monitoring", label: "输入监控", desc: "全局快捷键需要" },
  { key: "accessibility", label: "辅助功能", desc: "把文字自动输入到其他窗口需要" },
];

// Update
interface UpdateInfo { available: boolean; current_version: string; latest_version: string; body: string; }
const updateInfo = ref<UpdateInfo | null>(null);
const updateStatus = ref("");
const updateStatusType = ref<"info" | "ok">("info");
const updateChecking = ref(false);
const updateInstalling = ref(false);

// Logs
const guiLogs = ref<{ msg: string; level: string }[]>([]);
const logBoxRef = ref<HTMLElement | null>(null);

let timerInterval: ReturnType<typeof setInterval> | null = null;
let levelInterval: ReturnType<typeof setInterval> | null = null;
let processingTimerInterval: ReturnType<typeof setInterval> | null = null;
let toastId = 0;

// ── Computed ──
const currentModelName = computed(() => {
  const loaded = sttModels.value.find(m => m.is_loaded);
  return loaded?.name || sttModel.value || "";
});
const displayHotkey = computed(() => hotkeyStr.value || defaultHotkey);
const timerText = computed(() => {
  const s = Math.floor(elapsedMs.value / 1000);
  const ms = elapsedMs.value % 1000;
  return `${s}.${String(ms).padStart(3, "0").slice(0, 1)}s`;
});
const processingTimerText = computed(() => {
  const s = Math.floor(processingMs.value / 1000);
  const ms = processingMs.value % 1000;
  return `${s}.${String(ms).padStart(3, "0").slice(0, 1)}s`;
});

const permissionRows = computed(() =>
  PERM_META.map(m => {
    const status: PermissionStatus = perms.value ? perms.value[m.key] : "granted";
    // 麦克风/输入监控:只有「未询问」时系统才会弹窗,拒绝后再调用毫无反应。
    // 辅助功能:AXIsProcessTrusted 只有受信任/不受信任两态,拿不到「未询问」;
    // 而带 prompt 的查询每次都会弹引导窗,并把本应用加进系统设置的列表里
    // (没请求过的应用根本不会出现在那个列表,用户想勾也勾不到),所以只要
    // 没授权就一直提供「请求授权」。
    const canRequest = m.key === "accessibility" ? status !== "granted" : status === "not_determined";
    return { ...m, status, canRequest };
  })
);
// ── 服务器状态行 ──
const SERVER_META: { kind: ServerKind; label: string }[] = [
  { kind: "stt", label: "STT 语音识别" },
  { kind: "llm", label: "LLM 后处理" },
];

function srvStateText(s: ServerState) {
  return { not_configured: "未配置", stopped: "未运行", starting: "启动中", running: "运行中", failed: "失败" }[s] || s;
}
function srvStateClass(s: ServerState) {
  return s === "running" ? "ok" : s === "starting" ? "warn" : s === "failed" ? "bad" : "";
}

const serverRows = computed(() =>
  SERVER_META.map(m => {
    const status: ServerStatus | null = serverReport.value ? serverReport.value[m.kind] : null;
    const state: ServerState = status?.state ?? "stopped";
    const isUp = state === "running" || state === "starting";
    // 「本应用 / 外部」这个区分必须显式画出来：停止按钮只对前者有效，
    // 不标出来的话按钮为什么是灰的就没人看得懂。
    const ownerText = !status ? "" : status.managed ? "本应用" : state === "running" ? "外部" : "";
    const desc = status
      ? [
          `端口 ${status.port}`,
          status.pid ? `pid ${status.pid}` : "",
          status.current_model ? `模型 ${status.current_model}` : "",
          status.detail || "",
        ].filter(Boolean).join("　")
      : "状态未知";
    return {
      kind: m.kind,
      label: m.label,
      status,
      isUp,
      chipText: srvStateText(state),
      chipClass: srvStateClass(state),
      ownerText,
      ownerClass: ownerText === "本应用" ? "ok" : "warn",
      canStop: !!status?.managed,
      // 外部进程停不掉，「重启」也就无从谈起。
      canRestart: !isUp || !!status?.managed,
      desc,
    };
  })
);

const pathProblem = computed(() =>
  serverMode.value === "local" ? serverReport.value?.local_paths.problem ?? null : null
);
/** 两个服务的日志尾巴合起来给用户看，各自带前缀。 */
const serverLogLines = computed(() => {
  const r = serverReport.value;
  if (!r || serverMode.value !== "local") return [];
  const take = (s: ServerStatus, tag: string) => s.recent_logs.slice(-8).map(l => `[${tag}] ${l}`);
  return [...take(r.stt, "STT"), ...take(r.llm, "LLM")];
});
const serverLogPaths = computed(() => {
  const r = serverReport.value;
  if (!r || serverMode.value !== "local") return [];
  return [r.stt.log_path, r.llm.log_path].filter((p): p is string => !!p);
});

const missingPermLabels = computed(() =>
  perms.value?.is_macos
    ? permissionRows.value.filter(r => r.status !== "granted").map(r => r.label)
    : []
);

// ── Helpers ──
function toast(msg: string, type = "info") {
  const id = ++toastId;
  toasts.value.push({ id, msg, type });
  setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id); }, 2500);
  const prefix = type === "err" ? "[ERROR]" : type === "ok" ? "[OK]" : "[INFO]";
  guiLogs.value.push({ msg: `${prefix} ${msg}`, level: type });
  if (guiLogs.value.length > 500) guiLogs.value = guiLogs.value.slice(-500);
}

async function getConfig(): Promise<VoiceInputConfig> {
  return await invoke("get_config");
}

/** 返回是否保存成功。失败会弹 toast（并进日志面板）——以前这里只 console.error，
 *  没人看得见：麦克风选了半天存不进去，界面上一点动静都没有。 */
async function saveConfigPatch(patch: (cfg: VoiceInputConfig) => void): Promise<boolean> {
  try {
    const cfg = await getConfig();
    patch(cfg);
    await invoke("update_config", { newConfig: cfg });
    return true;
  } catch (e) {
    console.error("Config save failed:", e);
    toast(`设置保存失败: ${e}`, "err");
    return false;
  }
}

function addToHistory(text: string) {
  if (!text) return;
  const now = new Date();
  history.value.unshift({ text, time: now.toLocaleTimeString() });
  if (history.value.length > 20) history.value = history.value.slice(0, 20);
}

// ── Recording ──
async function startRecord() {
  if (!connected.value || loading.value) return;
  if (recording.value) return;  // state lock: prevent double-trigger
  recording.value = true;       // set state BEFORE await to block bounces
  try {
    loading.value = false;
    result.value = "";
    await invoke("start_recording");
    elapsedMs.value = 0;
    timerInterval = setInterval(() => { elapsedMs.value += 100; }, 100);
    levelInterval = setInterval(async () => {
      try { audioLevel.value = await invoke<number>("get_audio_level"); } catch {}
    }, 100);
  } catch (e) {
    toast(`录音失败: ${e}`, "err");
    recording.value = false;
  }
}

async function stopRecord() {
  if (!recording.value) return;  // already stopped
  recording.value = false;  // set state BEFORE await to block bounces
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  if (levelInterval) { clearInterval(levelInterval); levelInterval = null; }
  loading.value = true;
  processingMs.value = 0;
  processingTimerInterval = setInterval(() => { processingMs.value += 100; }, 100);
  try {
    await invoke<string>("stop_recording");
  } catch (e) {
    loading.value = false;
    if (processingTimerInterval) { clearInterval(processingTimerInterval); processingTimerInterval = null; }
    toast(`转录失败: ${e}`, "err");
  }
}

// ── Devices ──
async function refreshDevices() {
  try {
    audioDevices.value = await invoke<AudioDeviceInfo[]>("get_audio_devices");
  } catch (e) {
    // 设备列举失败别静默：下拉框只剩「默认设备」时用户根本猜不到是出了错。
    toast(`读取麦克风列表失败: ${e}`, "err");
  }
}
async function onDeviceChange() {
  const ok = await saveConfigPatch(cfg => { cfg.audio.device = selectedDevice.value; });
  if (ok) toast(selectedDevice.value ? `麦克风已切换到 ${selectedDevice.value}` : "已切回默认麦克风", "ok");
}

// ── 权限(macOS) ──
function permStateText(s: PermissionStatus) {
  return { granted: "已授权", denied: "已拒绝", not_determined: "未询问", restricted: "受限" }[s] || s;
}
function permStateClass(s: PermissionStatus) {
  return s === "granted" ? "ok" : s === "not_determined" ? "warn" : "bad";
}
async function refreshPermissions() {
  permsLoading.value = true;
  try { perms.value = await invoke<PermissionReport>("get_permissions"); }
  catch (e) { console.error("get_permissions failed:", e); }
  permsLoading.value = false;
}

// 触发系统授权弹窗，最多等 30 秒（Rust 端轮询，超时返回当前状态）
async function requestPerm(key: PermissionKey) {
  permBusy.value = key;
  try {
    const next = await invoke<PermissionStatus>("request_permission", { permission: key });
    if (perms.value) perms.value[key] = next;
    toast(next === "granted" ? "已授权" : "尚未授权，请在系统设置中手动勾选后点「刷新」", next === "granted" ? "ok" : "err");
  } catch (e) { toast(`权限申请失败: ${e}`, "err"); }
  permBusy.value = "";
  await refreshPermissions();
}

async function openPermSettings(key: PermissionKey) {
  try {
    await invoke("open_permission_settings", { permission: key });
    toast("已打开系统设置，勾选后请点「刷新」", "info");
  } catch (e) { toast(`打开系统设置失败: ${e}`, "err"); }
}

// ── 服务器管理 ──
async function refreshServers() {
  serversLoading.value = true;
  try {
    const r = await invoke<ServerReport>("get_server_report");
    serverReport.value = r;
    serverMode.value = r.mode;
  } catch (e) { console.error("get_server_report failed:", e); }
  serversLoading.value = false;
}

/** 只在设置面板打开且处于本地模式时轮询：启动中要看着它变成运行中。 */
function syncServerPolling() {
  const want = showSettings.value && serverMode.value === "local";
  if (want && !serverPollTimer) {
    serverPollTimer = setInterval(refreshServers, 3000);
  } else if (!want && serverPollTimer) {
    clearInterval(serverPollTimer);
    serverPollTimer = null;
  }
}

async function switchMode(mode: ServerMode) {
  if (mode === serverMode.value) return;
  modeBusy.value = true;
  try {
    const url = await invoke<string>("set_server_mode", { mode });
    serverMode.value = mode;
    toast(mode === "local" ? `已切到本地管理（${url}）` : `已切到远程连接（${url}）`, "ok");
    await refreshServers();
    await loadModels();
  } catch (e) { toast(`切换失败: ${e}`, "err"); }
  modeBusy.value = false;
  syncServerPolling();
}

async function startSrv(kind: ServerKind) {
  srvBusy.value = kind;
  try {
    const msg = await invoke<string>("start_server", { kind });
    toast(msg, "ok");
  } catch (e) { toast(`启动失败: ${e}`, "err"); }
  srvBusy.value = "";
  await refreshServers();
}

async function stopSrv(kind: ServerKind) {
  srvBusy.value = kind;
  try {
    const msg = await invoke<string>("stop_server", { kind });
    toast(msg, "ok");
  } catch (e) { toast(`停止失败: ${e}`, "err"); }
  srvBusy.value = "";
  await refreshServers();
}

async function restartSrv(kind: ServerKind) {
  srvBusy.value = kind;
  try {
    const msg = await invoke<string>("restart_server", { kind });
    toast(msg, "ok");
  } catch (e) { toast(`重启失败: ${e}`, "err"); }
  srvBusy.value = "";
  await refreshServers();
}

/** 保存本地模式的路径 / 端口 / 自启设置，并回显路径是否可用。 */
async function saveLocal() {
  const local: LocalServerConfig = {
    repo_path: repoPath.value.trim() || null,
    python_path: pythonPath.value.trim() || null,
    stt_port: sttPort.value || 6544,
    llm_port: llmPort.value || 6545,
    stt_model: null,
    llm_model: null,
    auto_start: localAutoStart.value,
  };
  try {
    const report = await invoke<LocalPathReport>("set_local_server_config", { local });
    if (report.problem) toast(report.problem, "err");
  } catch (e) { toast(`保存失败: ${e}`, "err"); }
  await refreshServers();
}

/** 自动探测仓库 / 解释器。探测不到时把原因说出来，而不是静默无反应。 */
async function detectPaths() {
  detecting.value = true;
  try {
    const d = await invoke<DetectResult>("detect_local_server");
    if (d.repo_path) {
      repoPath.value = d.repo_path;
      pythonPath.value = d.python_path || "";
      await saveLocal();
      toast(d.problem ? d.problem : `已探测到：${d.repo_path}`, d.problem ? "err" : "ok");
    } else {
      toast(d.problem || "没有探测到仓库，请手动填写路径", "err");
    }
  } catch (e) { toast(`探测失败: ${e}`, "err"); }
  detecting.value = false;
}

// ── Config ──
async function loadConfig() {
  try {
    const cfg = await getConfig();
    serverHost.value = cfg.server.host;
    serverPort.value = cfg.server.port;
    version.value = cfg._version;
    hotkeyStr.value = cfg.hotkey.key;
    startMinimized.value = cfg.ui.start_minimized;
    autoInputEnabled.value = cfg.ui.auto_input ?? false;
    selectedDevice.value = cfg.audio.device;
    // 旧配置没有 server.mode / server.local，Rust 端补了默认值；这里仍然
    // 用 ?? 兜一层，免得手改过配置文件时前端直接崩。
    serverMode.value = cfg.server.mode ?? "remote";
    const local = cfg.server.local;
    if (local) {
      repoPath.value = local.repo_path ?? "";
      pythonPath.value = local.python_path ?? "";
      sttPort.value = local.stt_port ?? 6544;
      llmPort.value = local.llm_port ?? 6545;
      localAutoStart.value = local.auto_start ?? false;
    }
  } catch {}
}
async function loadAutostart() {
  try { autoStart.value = await invoke<boolean>("get_autostart"); } catch {}
}
function onServerSettingChange() {
  saveConfigPatch(cfg => { cfg.server.host = serverHost.value.trim() || "localhost"; cfg.server.port = serverPort.value; });
}
async function toggleAutoStart() {
  try { await invoke("set_autostart", { enabled: autoStart.value }); } catch { autoStart.value = !autoStart.value; }
}
function toggleStartMinimized() { saveConfigPatch(cfg => { cfg.ui.start_minimized = startMinimized.value; }); }
function onAutoInputToggle() { saveConfigPatch(cfg => { cfg.ui.auto_input = autoInputEnabled.value; }); }

// ── Connection ──
async function updateServer() {
  connected.value = false;
  connecting.value = true;
  const host = serverHost.value.trim() || "localhost";
  const port = serverPort.value || 6544;
  try {
    await invoke("set_server_host", { host, port });
    const ok = await loadModels();
    if (ok) {
      connected.value = true;
      toast("已连接", "ok");
      saveConfigPatch(cfg => { cfg.server.host = host; cfg.server.port = port; });
    } else {
      toast("服务器无响应", "err");
    }
  } catch (e) { toast(`连接失败: ${e}`, "err"); }
  connecting.value = false;
}

// ── Models ──
async function loadModels(): Promise<boolean> {
  let ok = false;
  try {
    const list = await invoke<ModelInfo[]>("get_models");
    sttModels.value = list;
    if (list.length > 0) { const loaded = list.find(m => m.is_loaded); sttModel.value = loaded?.name || list[0].name; }
    ok = true;
  } catch (e) { console.error("get_models error:", e); }
  try {
    const llmList = await invoke<ModelInfo[]>("get_llm_models");
    llmModels.value = llmList;
    if (llmList.length > 0) { const loaded = llmList.find(m => m.is_loaded); llmModel.value = loaded?.name || llmList[0].name; }
  } catch {}
  try { llmEnabled.value = await invoke<boolean>("get_llm_enabled"); } catch {}
  return ok;
}

async function switchStt() {
  if (!sttModel.value) return;
  sttLoading.value = true;
  // 失败原因要带上：光说「切换失败」，用户既不知道是模型没下全还是服务没起来。
  try { await invoke<string>("switch_model", { name: sttModel.value }); toast("模型已切换", "ok"); } catch (e) { toast(`切换失败: ${e}`, "err"); }
  sttLoading.value = false;
}
async function switchLlm() {
  if (!llmModel.value) return;
  llmLoading.value = true;
  try { await invoke<string>("switch_llm_model", { name: llmModel.value }); toast("LLM 已切换", "ok"); } catch (e) { toast(`LLM 切换失败: ${e}`, "err"); }
  llmLoading.value = false;
}
async function toggleLlm() {
  try { await invoke("set_llm_enabled", { enabled: llmEnabled.value }); toast(`LLM ${llmEnabled.value ? '已启用' : '已禁用'}`, "ok"); } catch { llmEnabled.value = !llmEnabled.value; }
}

// ── Hotkey ──
// 当前录制监听器(用于取消/卸载时移除;防止残留监听重复触发)
let hotkeyHandler: ((e: KeyboardEvent) => void) | null = null;

function startHotkeyRecording() {
  hotkeyRecording.value = !hotkeyRecording.value;
  hotkeyMsg.value = "";
  if (hotkeyRecording.value) {
    // 修饰键跨事件累积(按 ctrl 再按 alt 不结束;主键按下或纯修饰键
    // 组合全部松开时才结束)。旧实现每次事件新建 parts,且按下 ctrl
    // 就因 parts 非空立即结束——只能录到单个键。
    let mods: string[] = [];
    const modName = (e: KeyboardEvent): string | null => {
      switch (e.code) {
        case 'ControlLeft': return 'left_ctrl';
        case 'ControlRight': return 'right_ctrl';
        case 'AltLeft': return 'left_alt';
        case 'AltRight': return 'right_alt';
        case 'ShiftLeft': return 'left_shift';
        case 'ShiftRight': return 'right_shift';
        case 'MetaLeft': return 'left_cmd';
        case 'MetaRight': return 'right_cmd';
        default: return null;
      }
    };
    const cleanup = () => {
      if (hotkeyHandler) {
        document.removeEventListener('keydown', hotkeyHandler);
        document.removeEventListener('keyup', hotkeyHandler);
        hotkeyHandler = null;
      }
    };
    const finish = (mainKey: string | null) => {
      const parts = [...mods];
      if (mainKey) parts.push(mainKey.length === 1 ? mainKey.toLowerCase() : mainKey.toLowerCase());
      if (parts.length === 0) return;  // 无内容不结束
      hotkeyStr.value = parts.join('+');
      hotkeyChanged.value = true;
      hotkeyRecording.value = false;
      cleanup();
    };
    const handler = (e: KeyboardEvent) => {
      e.preventDefault(); e.stopPropagation();
      const m = modName(e);
      if (e.type === 'keydown') {
        if (m) {
          if (!mods.includes(m)) mods.push(m);
          return;  // 只累积修饰键,等待主键
        }
        finish(e.key);  // 主键按下 → 结束
      } else if (e.type === 'keyup') {
        // 纯修饰键组合:全部松开时结束(如 ctrl+alt 无主键)
        if (m && mods.length > 0) finish(null);
      }
    };
    hotkeyHandler = handler;
    document.addEventListener('keydown', handler);
    document.addEventListener('keyup', handler);
  } else {
    // 用户点"取消":移除监听
    if (hotkeyHandler) {
      document.removeEventListener('keydown', hotkeyHandler);
      document.removeEventListener('keyup', hotkeyHandler);
      hotkeyHandler = null;
    }
  }
}
async function applyHotkey() {
  if (!hotkeyStr.value) return;
  try {
    await invoke("register_hotkey", { shortcut: hotkeyStr.value });
    hotkeyChanged.value = false;
    toast("快捷键已更新", "ok");
    saveConfigPatch(cfg => { cfg.hotkey.key = hotkeyStr.value; });
  } catch (e) { toast("更新失败", "err"); }
}

// ── Prompt ──
async function loadPrompt() {
  promptLoading.value = true;
  try { promptText.value = await invoke<string>("get_llm_prompt"); promptStatus.value = "已加载"; } catch { promptStatus.value = "加载失败"; }
  promptLoading.value = false;
}
async function savePrompt() {
  if (!promptText.value.trim()) return;
  promptLoading.value = true;
  try { await invoke("save_llm_prompt", { text: promptText.value }); promptStatus.value = "已保存"; toast("提示词已保存", "ok"); } catch { promptStatus.value = "保存失败"; }
  promptLoading.value = false;
}

// ── Update ──
async function doCheckUpdate() {
  updateChecking.value = true;
  updateStatus.value = "检查中...";
  updateStatusType.value = "info";
  // 10秒超时，防止卡死在"检查中..."
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("超时")), 10000));
  try {
    const info = await Promise.race([
      invoke<UpdateInfo>("check_update"),
      timeout
    ]) as UpdateInfo;
    updateInfo.value = info;
    if (info.available) { updateStatus.value = `发现新版本 ${info.latest_version}`; toast(`新版本 ${info.latest_version} 可用`, "ok"); }
    else { updateStatus.value = "已是最新版本"; updateStatusType.value = "ok"; }
  } catch (e) { updateStatus.value = `检查失败: ${e}`; }
  updateChecking.value = false;
}
async function doInstallUpdate() {
  updateInstalling.value = true;
  updateStatus.value = "正在下载...";
  updateStatusType.value = "info";
  const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("下载超时")), 120000));
  try {
    const msg = await Promise.race([
      invoke<string>("install_update"),
      timeout
    ]) as string;
    updateStatus.value = msg;
    updateStatusType.value = "ok";
    toast("更新已安装，重启后生效", "ok");
  } catch (e) { updateStatus.value = `安装失败: ${e}`; }
  updateInstalling.value = false;
}

// ── Result ──
function copyResult() {
  if (!result.value) return;
  navigator.clipboard.writeText(result.value);
  copyFeedback.value = true;
  toast("已复制", "ok");
  setTimeout(() => { copyFeedback.value = false; }, 2000);
}
async function doAutoInput() {
  if (!result.value) return;
  try { await invoke("auto_input", { text: result.value }); toast("已输入", "ok"); }
  catch (e) {
    // 缺「辅助功能」权限时 Rust 端会返回可读原因,原样展示,不要吞掉
    toast(`${e}`, "err");
    refreshPermissions();
  }
}
function clearResult() { result.value = ""; }
async function minimizeToTray() {
  try { await invoke("minimize_to_tray"); } catch {}
}

// ── Lifecycle ──
// 打开设置面板时重新查一次:用户可能刚在系统设置里改过授权
watch(showSettings, open => { if (open) refreshPermissions(); });

onMounted(async () => {
  await loadConfig();
  await loadAutostart();
  await refreshDevices();
  await refreshPermissions();
  await refreshServers();
  await updateServer();

  // 设置面板开着 + 本地模式时才轮询状态：「启动中 → 运行中」要肉眼可见，
  // 但面板关着时没人看，没必要每 3 秒打一次 /health。
  watch([showSettings, serverMode], syncServerPolling, { immediate: true });

  // Hotkey lifecycle is handled entirely in Rust (start/stop recording + transcription).
  // Frontend only updates UI state to reflect what Rust already did.
  listen("hotkey-press", () => {
    recording.value = true;
    result.value = "";
    elapsedMs.value = 0;
    timerInterval = setInterval(() => { elapsedMs.value += 100; }, 100);
    levelInterval = setInterval(async () => {
      try { audioLevel.value = await invoke<number>("get_audio_level"); } catch {}
    }, 100);
  });
  listen("hotkey-release", () => {
    // 没在录音就什么都不做。Rust 那边遇到录音失败 / 5 分钟安全超时会先补发一次
    // hotkey-release 把界面收干净，等用户手指真正松开时事件还会再来一次；
    // 不挡住的话第二次会重新点亮 loading 并起一个永远没人来关的计时器。
    if (!recording.value) return;
    recording.value = false;
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    if (levelInterval) { clearInterval(levelInterval); levelInterval = null; }
    loading.value = true;
    processingMs.value = 0;
    processingTimerInterval = setInterval(() => { processingMs.value += 100; }, 100);
  });
  listen("tray-check-update", () => { showSettings.value = true; doCheckUpdate(); });

  // 后台定时检查更新（启动后延迟30秒，之后每6小时自动检查一次）
  setTimeout(() => doCheckUpdate(), 30000);
  setInterval(() => doCheckUpdate(), 6 * 60 * 60 * 1000);

  listen("transcribe-progress", (event) => {
    const data = event.payload as any;
    if (data?.type === "llm_start" || data?.type === "llm_progress") llmProcessing.value = true;
  });

  listen<string>("transcribe-done", (event) => {
    loading.value = false;
    llmProcessing.value = false;
    if (processingTimerInterval) { clearInterval(processingTimerInterval); processingTimerInterval = null; }
    const text = event.payload;
    if (text) {
      result.value = text;
      addToHistory(text);
      toast("识别完成", "ok");
      // 自动输入失败(最常见是缺「辅助功能」权限)必须让用户看见:
      // 以前这里 catch 成空函数,转录一切正常但目标窗口什么都没出现。
      if (autoInputEnabled.value) {
        invoke("auto_input", { text }).catch(e => { toast(`${e}`, "err"); refreshPermissions(); });
      }
    }
  });

  listen<string>("transcribe-error", (event) => {
    loading.value = false;
    llmProcessing.value = false;
    if (processingTimerInterval) { clearInterval(processingTimerInterval); processingTimerInterval = null; }
    toast(`失败: ${event.payload}`, "err");
  });

  // Auto-check for updates (silent)
  try {
    const info = await invoke<UpdateInfo>("check_update");
    if (info.available) { updateInfo.value = info; toast(`新版本 ${info.latest_version} 可用`, "ok"); }
  } catch {}
});

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval);
  if (levelInterval) clearInterval(levelInterval);
  if (processingTimerInterval) clearInterval(processingTimerInterval);
  if (serverPollTimer) clearInterval(serverPollTimer);
  if (hotkeyHandler) {
    document.removeEventListener('keydown', hotkeyHandler);
    document.removeEventListener('keyup', hotkeyHandler);
    hotkeyHandler = null;
  }
});
</script>

<style>
:root {
  --bg: #0f0f14;
  --card: #1a1a24;
  --surface: #22222e;
  --border: #2a2a38;
  --green: #4ade80;
  --red: #f87171;
  --yellow: #fbbf24;
  --blue: #60a5fa;
  --text: #e4e4e7;
  --muted: #71717a;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", Roboto, sans-serif;
  color: var(--text);
  background: var(--bg);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body, #app { height: 100%; }

.app {
  display: flex; flex-direction: column; height: 100vh;
  overflow: hidden; user-select: none;
}

/* ── Header ── */
.header { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: var(--card); border-bottom: 1px solid var(--border); flex-shrink: 0; }
.header-left { display: flex; align-items: center; gap: 8px; }
.app-icon { font-size: 1.1rem; }
.conn-dot { width: 7px; height: 7px; border-radius: 50%; }
.conn-dot.on { background: var(--green); box-shadow: 0 0 6px var(--green); }
.conn-dot.off { background: var(--red); }
.conn-text { font-size: 0.75rem; color: var(--muted); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.header-right { display: flex; gap: 4px; }
.header-btn { background: none; border: none; color: var(--muted); font-size: 1rem; cursor: pointer; padding: 4px 8px; border-radius: 6px; transition: all 0.15s; }
.header-btn:hover { color: var(--text); background: var(--surface); }
.header-btn.active { color: var(--blue); }

/* ── Toast ── */
.toast-container { position: fixed; top: 48px; left: 50%; transform: translateX(-50%); z-index: 100; display: flex; flex-direction: column; gap: 6px; }
.toast { padding: 6px 16px; border-radius: 8px; font-size: 0.72rem; backdrop-filter: blur(8px); animation: toast-in 0.2s ease; }
.toast.ok { background: rgba(74, 222, 128, 0.15); color: var(--green); border: 1px solid rgba(74, 222, 128, 0.3); }
.toast.err { background: rgba(248, 113, 113, 0.15); color: var(--red); border: 1px solid rgba(248, 113, 113, 0.3); }
.toast.info { background: rgba(96, 165, 250, 0.15); color: var(--blue); border: 1px solid rgba(96, 165, 250, 0.3); }
@keyframes toast-in { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
.toast-enter-active, .toast-leave-active { transition: all 0.2s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(-8px); }

/* ── Settings Panel ── */
.settings-panel { position: absolute; top: 44px; left: 0; right: 0; bottom: 24px; background: var(--bg); z-index: 50; overflow: hidden; }
.settings-scroll { height: 100%; overflow-y: auto; padding: 12px 14px; }
.slide-enter-active, .slide-leave-active { transition: transform 0.2s ease; }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); }
.slide-enter-to, .slide-leave-from { transform: translateX(0); }

.s-section { margin-bottom: 16px; }
.s-title { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
.s-row { display: flex; align-items: center; gap: 6px; }
.s-input { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 0.78rem; flex: 1; outline: none; }
.s-input:focus { border-color: var(--blue); }
.s-port { width: 60px; flex: none; }
.s-btn { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; font-size: 0.72rem; cursor: pointer; white-space: nowrap; }
.s-btn:hover:not(:disabled) { border-color: var(--blue); color: var(--blue); }
.s-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.s-select { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 0.78rem; width: 100%; outline: none; }
.s-loading { font-size: 0.7rem; color: var(--yellow); margin-top: 4px; }
.s-label { font-size: 0.78rem; color: var(--text); }
.s-tip { font-size: 0.68rem; color: var(--muted); margin-top: 4px; }
.s-textarea { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-size: 0.75rem; width: 100%; resize: vertical; font-family: inherit; outline: none; }
.s-textarea:focus { border-color: var(--blue); }
.hotkey-field { cursor: pointer; text-align: center; font-family: monospace; }
.hotkey-field.recording { border-color: var(--yellow); animation: pulse-border 1s infinite; }
@keyframes pulse-border { 0%,100% { border-color: var(--yellow); } 50% { border-color: transparent; } }

/* Permissions */
.perm-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.perm-row:last-of-type { border-bottom: none; }
.perm-info { min-width: 0; }
.perm-actions { display: flex; gap: 4px; flex-shrink: 0; }
.perm-head { display: flex; align-items: center; gap: 6px; }
.perm-state { font-size: 0.65rem; padding: 1px 6px; border-radius: 999px; border: 1px solid transparent; white-space: nowrap; }
.perm-state.ok { color: var(--green); background: rgba(74, 222, 128, 0.12); border-color: rgba(74, 222, 128, 0.3); }
.perm-state.warn { color: var(--yellow); background: rgba(251, 191, 36, 0.12); border-color: rgba(251, 191, 36, 0.3); }
.perm-state.bad { color: var(--red); background: rgba(248, 113, 113, 0.12); border-color: rgba(248, 113, 113, 0.3); }
.perm-banner { width: 100%; max-width: 360px; background: rgba(251, 191, 36, 0.12); border: 1px solid rgba(251, 191, 36, 0.3); color: var(--yellow); border-radius: 8px; padding: 8px 10px; font-size: 0.7rem; line-height: 1.4; text-align: center; cursor: pointer; }
.perm-banner:hover { background: rgba(251, 191, 36, 0.2); }

/* 服务器 */
.mode-switch { gap: 0; }
.mode-btn { flex: 1; border-radius: 0; }
.mode-btn:first-child { border-radius: 6px 0 0 6px; }
.mode-btn:last-child { border-radius: 0 6px 6px 0; border-left: none; }
.mode-btn.active { background: rgba(96, 165, 250, 0.15); color: var(--blue); border-color: rgba(96, 165, 250, 0.4); }
.srv-problem { color: var(--yellow); }
.srv-logs { margin-top: 6px; max-height: 110px; overflow-y: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
.srv-log-line { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.62rem; color: var(--muted); line-height: 1.45; white-space: pre-wrap; word-break: break-all; }

/* Update */
.update-info { display: flex; flex-direction: column; gap: 4px; margin-bottom: 6px; }
.update-new { color: var(--green); font-weight: 600; font-size: 0.85rem; }
.update-ok { color: var(--muted); font-size: 0.8rem; }
.update-body { font-size: 0.7rem; color: var(--muted); margin-top: 4px; max-height: 80px; overflow-y: auto; line-height: 1.4; }

/* Toggle */
.toggle { position: relative; display: inline-block; width: 34px; height: 18px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; inset: 0; background: var(--surface); border-radius: 18px; transition: 0.2s; border: 1px solid var(--border); }
.slider::before { content: ""; position: absolute; width: 14px; height: 14px; left: 1px; bottom: 1px; background: var(--muted); border-radius: 50%; transition: 0.2s; }
.toggle input:checked + .slider { background: rgba(74, 222, 128, 0.2); border-color: var(--green); }
.toggle input:checked + .slider::before { transform: translateX(16px); background: var(--green); }

.spinner { width: 20px; height: 20px; border: 2px solid var(--border); border-top-color: var(--green); border-radius: 50%; animation: spin 0.6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.s-btn { padding: 4px 10px; font-size: 0.7rem; }

/* Log box */
.log-box { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 6px; max-height: 200px; overflow-y: auto; font-size: 0.65rem; font-family: monospace; line-height: 1.5; }
.log-entry { word-break: break-all; margin-bottom: 2px; }
.log-empty { color: var(--muted); font-style: italic; font-size: 0.7rem; padding: 8px; }

/* ── Main Content ── */
.main { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 16px; gap: 16px; overflow: hidden; }

/* Record */
.record-area { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.record-btn { position: relative; width: 100px; height: 100px; border-radius: 50%; background: var(--card); border: 3px solid var(--border); cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.2s; flex-shrink: 0; }
.record-btn:hover:not(:disabled) { border-color: var(--text); transform: scale(1.04); }
.record-btn:active:not(:disabled) { transform: scale(0.96); }
.record-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.record-btn.active { border-color: var(--red); background: rgba(248, 113, 113, 0.08); }
.record-btn.processing { border-color: var(--yellow); }
.record-ring { position: absolute; inset: -6px; border-radius: 50%; border: 2px solid transparent; transition: all 0.3s; }
.record-btn.active .record-ring { border-color: rgba(248, 113, 113, 0.3); animation: ring-pulse 1.2s infinite; }
@keyframes ring-pulse { 0%,100% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.08); opacity: 0.5; } }
.record-icon { font-size: 2rem; z-index: 1; }
.record-status { text-align: center; font-size: 0.78rem; }
.status-rec { color: var(--red); }
.status-proc { color: var(--yellow); }
.status-ready { color: var(--muted); }
.status-off { color: var(--muted); }

/* Audio level */
.main-level-meter { width: 100%; max-width: 360px; display: flex; justify-content: center; }
.main-level-bar { width: 60%; height: 5px; background: var(--surface); border-radius: 3px; overflow: hidden; }
.main-level-fill { height: 100%; background: linear-gradient(90deg, var(--green), var(--yellow), var(--red)); border-radius: 3px; transition: width 0.08s ease; }

/* Result */
.result-area { width: 100%; max-width: 360px; flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.result-loading { display: flex; justify-content: center; padding: 16px; }
.result-content { background: var(--card); border-radius: 12px; border: 1px solid var(--border); display: flex; flex-direction: column; flex: 1; min-height: 0; overflow: hidden; }
.result-text { flex: 1; padding: 14px; font-size: 0.92rem; line-height: 1.6; overflow-y: auto; cursor: text; user-select: text; min-height: 0; word-break: break-word; }
.result-actions { display: flex; gap: 1px; border-top: 1px solid var(--border); flex-shrink: 0; }
.r-btn { flex: 1; background: var(--surface); color: var(--muted); border: none; padding: 8px; font-size: 0.72rem; cursor: pointer; transition: all 0.15s; }
.r-btn:hover { color: var(--text); background: var(--card); }
.r-btn.ok { color: var(--green); }

/* History */
.history-area { width: 100%; max-width: 360px; flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.history-title { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
.history-scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.history-item { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; cursor: pointer; transition: all 0.15s; display: flex; flex-direction: column; gap: 2px; }
.history-item:hover { border-color: var(--blue); }
.history-text { font-size: 0.82rem; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history-time { font-size: 0.65rem; color: var(--muted); }

/* Empty */
.empty-state { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 24px; }
.empty-icon { font-size: 2.5rem; opacity: 0.3; }
.empty-text { font-size: 0.78rem; color: var(--muted); text-align: center; line-height: 1.5; }

/* Footer */
.footer { display: flex; justify-content: center; padding: 6px; border-top: 1px solid var(--border); flex-shrink: 0; }
.footer-text { font-size: 0.6rem; color: var(--muted); }
</style>
