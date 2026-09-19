<template>
  <div class="app">
    <!-- Header -->
    <header class="header">
      <div class="header-left">
        <span class="app-icon">🎙️</span>
        <!-- 正在等服务器起来的时候别断言「未连接」：本地模式下服务刚拉起，
             模型要加载十几秒，这段时间说「未连接」看着就像服务坏了。 -->
        <span :class="['conn-dot', connected ? 'on' : connecting ? 'wait' : 'off']"></span>
        <span class="conn-text">{{ connected ? currentModelName : connecting ? '连接中…' : '未连接' }}</span>
      </div>
      <div class="header-right">
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
                    :title="row.stopHint">
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
                端口上已经有服务就直接连接，不会重复启动。你自己在终端里跑的服务，只要工作目录就是上面这个仓库，会标成「外部（本项目）」，照样可以从这里停止和重启；认不出来源的进程标成「外部（未识别）」，本应用只连接、绝不停它。
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
              <label class="toggle"><input type="checkbox" v-model="llmEnabled" @change="toggleLlm" :disabled="llmToggling" /><span class="slider"></span></label>
              <span class="s-label" :class="{ 'llm-busy': llmToggling }">{{ llmToggleText }}</span>
            </div>
            <!-- 本地管理模式下这个开关不只是个标志位:LLM 服务跟着它起停。
                 加载模型要几秒,开关这几秒是锁着的——得让用户知道那不是卡死。 -->
            <div v-if="serverMode === 'local'" class="s-tip" style="margin-top:4px">
              LLM 服务跟着这个开关走：打开时启动（加载模型要几秒），关闭时停止，不用一直占着内存。只停本应用启动的那个；你自己在终端里跑的服务会保留。
            </div>
            <div v-if="llmEnabled" style="margin-top: 8px;">
              <select v-if="llmModels.length" class="s-select" v-model="llmModel" @change="switchLlm">
                <option v-for="m in llmModels" :key="m.name" :value="m.name">
                  {{ m.name }} {{ m.is_loaded ? '✓' : '' }}
                </option>
              </select>
              <!-- 空下拉框什么也不说明。拿不到列表基本只有一个原因:LLM 服务
                   还没起来(或刚起来还在加载),所以直说,并给一个重试按钮。 -->
              <div v-else class="s-tip">
                取不到 LLM 模型列表——LLM 服务可能还没就绪。
                <button class="s-btn" style="margin-left:6px" @click="loadLlmModels"
                  :disabled="llmModelsLoading">{{ llmModelsLoading ? '...' : '重试' }}</button>
              </div>
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
          <span v-else-if="connecting" class="status-proc">正在连接服务器…</span>
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
/** 与后端 `ServerOwner` 一一对应。 */
type ServerOwner = "app" | "external_project" | "external_unknown";
interface ServerStatus {
  kind: ServerKind;
  state: ServerState;
  port: number;
  /** 进程归属三档：本应用启动 / 外部但确认是本项目 / 外部且认不出。 */
  owner: ServerOwner;
  /** 能不能从这里停。规则由后端算好，前端别自己再推一遍。 */
  can_stop: boolean;
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
const llmModelsLoading = ref(false);
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
/** 开关正在生效中。本地模式下这几秒是在等 LLM 服务加载模型。 */
const llmToggling = ref(false);
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

/** 归属三档的显示文案与配色。 */
const OWNER_CHIP: Record<ServerOwner, { text: string; cls: string }> = {
  // 本应用 spawn 的，生命周期完全归我们管。
  app: { text: "本应用启动", cls: "ok" },
  // 用户自己在终端里起的，但校验过确实是本项目的服务——能停，只是得让用户
  // 知道这不是应用起的，免得他以为自己的终端会话会跟着一起消失。
  external_project: { text: "外部（本项目）", cls: "warn" },
  // 端口上有东西，但认不出是谁。中性色：不是错误，只是管不着。
  external_unknown: { text: "外部（未识别）", cls: "muted" },
};

/** 开关旁边那行字。生效中要说清楚在等什么,否则几秒钟的静默像是没反应。 */
const llmToggleText = computed(() => {
  if (llmToggling.value) {
    if (serverMode.value !== "local") return "处理中…";
    return llmEnabled.value ? "正在启动 LLM 服务…" : "正在停止 LLM 服务…";
  }
  return llmEnabled.value ? "已启用" : "已禁用";
});

const serverRows = computed(() =>
  SERVER_META.map(m => {
    const status: ServerStatus | null = serverReport.value ? serverReport.value[m.kind] : null;
    const state: ServerState = status?.state ?? "stopped";
    const isUp = state === "running" || state === "starting";
    // 归属必须显式画出来：停止按钮为什么能点 / 为什么是灰的，全靠这个 chip 解释。
    // 「本应用启动」在失败时也要显示——用户得知道那是自己这边的进程没起来。
    const showOwner = !!status && (isUp || status.owner === "app");
    const chip = status ? OWNER_CHIP[status.owner] : null;
    const canStop = !!status?.can_stop;
    // LLM 现在是跟着后处理开关走的。开关关着时这一行显示「未运行」是**预期结果**,
    // 不说一声的话看起来就像服务起不来。
    const offBecauseToggle = m.kind === "llm" && !llmEnabled.value && state === "stopped";
    // 反过来的那一半:开关开着、服务却没在跑(手动点了「停止」,或者它自己崩了)。
    // 这是真正坏掉的组合——STT 每次转录都会去反代一个空端口,白等一次超时,
    // 而界面上没有任何一处会说破。必须显式喊出来。
    const onButMissing = m.kind === "llm" && llmEnabled.value && (state === "stopped" || state === "failed");
    const desc = status
      ? [
          `端口 ${status.port}`,
          status.pid ? `pid ${status.pid}` : "",
          status.current_model ? `模型 ${status.current_model}` : "",
          status.detail || "",
          offBecauseToggle ? "已随「LLM 后处理」关闭；打开那个开关会自动启动" : "",
          onButMissing ? "⚠「LLM 后处理」开着但服务没在跑，转录时的后处理会失败；点「启动」，或把那个开关关掉" : "",
        ].filter(Boolean).join("　")
      : "状态未知";
    return {
      kind: m.kind,
      label: m.label,
      status,
      isUp,
      chipText: srvStateText(state),
      chipClass: srvStateClass(state),
      ownerText: showOwner && chip ? chip.text : "",
      ownerClass: chip ? chip.cls : "",
      canStop,
      // 停不掉的（认不出身份）自然也谈不上重启。
      canRestart: !isUp || canStop,
      stopHint: canStop ? "" : "这个进程认不出是不是本项目的服务，本应用不会碰它",
      desc,
    };
  })
);

/** STT 服务此刻的状态。客户端连的就是它,连接状态该跟着它走。
 *  `null` = 还没拿到第一份报告。 */
const sttState = computed<ServerState | null>(() => serverReport.value?.stt.state ?? null);

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
    // LLM 服务不一定是从这个界面上打开的:自动启动、启动后对账补起、或者用户
    // 点了服务器面板上的「启动」,都会让它在列表还空着的时候跑起来。轮询到这一刻
    // 就补拉一次,不然下拉框会一直空着。
    if (r.llm.state === "running" && llmModels.value.length === 0) await loadLlmModels();
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
  let switched = false;
  try {
    const url = await invoke<string>("set_server_mode", { mode });
    serverMode.value = mode;
    toast(mode === "local" ? `已切到本地管理（${url}）` : `已切到远程连接（${url}）`, "ok");
    await refreshServers();
    switched = true;
  } catch (e) { toast(`切换失败: ${e}`, "err"); }
  modeBusy.value = false;
  syncServerPolling();
  // 换了模式就是换了连接目标,得重连,而不是留着上一套的 connected 和模型列表。
  // 以前这里只 `loadModels()`：请求打的确实是新地址，但 `connected` 一直是切换前
  // 那个值——切过去连不上也照样显示已连接，切回来连上了也照样是「未连接」。
  //
  // 预算按新目标重算（作废上一轮剩下的），并且**不 await**：本地模式下服务可能
  // 正在加载模型，不该把两个模式按钮跟着锁上几十秒。
  if (switched) {
    connectDeadline = 0;
    ensureConnected(connectBudget());
  }
}

/** 起完 / 重启完之后把连接补上。客户端只连 STT——LLM 是由 STT 服务端反代的,
 *  起它不改变连接状态,但模型列表值得刷新一下。 */
async function connectAfterServerAction(kind: ServerKind) {
  if (kind === "stt") await ensureConnected(connectBudget());
  else if (connected.value) await loadModels();
}

async function startSrv(kind: ServerKind) {
  srvBusy.value = kind;
  try {
    const msg = await invoke<string>("start_server", { kind });
    toast(msg, "ok");
  } catch (e) { toast(`启动失败: ${e}`, "err"); }
  srvBusy.value = "";
  await refreshServers();
  // 以前到这里就结束了:面板上写着「运行中」,头部却一直「未连接」,录音按钮
  // 一直是灰的,除非用户自己回到远程那一栏点「连接」。
  await connectAfterServerAction(kind);
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
  await connectAfterServerAction(kind);
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
// 连接不再是「点一次按钮、试一次」。服务刚被拉起时 MLX 要 10–30 秒加载模型,
// 这期间 `/models` 必然不通,单次尝试的结果就是界面永远停在「未连接」,而旁边
// 的服务器面板明明写着「运行中」。这里统一成一个带截止时间的重试循环。

/** 重试间隔。模型加载期间每 2 秒问一次,不迟钝也不打搅。 */
const CONNECT_RETRY_MS = 2000;
/** 服务「启动中」时的等待预算。MLX STT 首次加载模型实测 10–30 秒。 */
const CONNECT_BUDGET_STARTING_MS = 60000;
/** 服务 `/health` 已经通了才连,一次多半就成,留点余量给模型列表接口。 */
const CONNECT_BUDGET_RUNNING_MS = 8000;

// 同一时刻只允许一轮循环在跑。想连的人只是把截止时间往后推,不会各自起一个
// 循环去打服务端——「每个 3 秒轮询 tick 都重试一次」正是要避免的那种风暴。
let connectRun: Promise<void> | null = null;
let connectDeadline = 0;

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

/**
 * 连接该重试多久。
 *
 * 只有本地管理模式下我们才知道服务的死活:正在加载模型就值得等下去,已经健康
 * 的一次就该通,停着的连都不必连(只试一次,让用户立刻看到结果而不是干等)。
 * 远程模式一律只试一次,和以前的「连接」按钮行为逐字相同。
 */
function connectBudget(): number {
  if (serverMode.value !== "local") return 0;
  const state = serverReport.value?.stt.state;
  if (state === "running") return CONNECT_BUDGET_RUNNING_MS;
  if (state === "starting") return CONNECT_BUDGET_STARTING_MS;
  return 0;
}

/** 连一次。地址由 Rust 按当前模式算(`effective_stt_url`),前端不再自己拼:
 *  本地管理连的是 `local.stt_port`,远程才是 host/port,而 host 还可能本身就是
 *  一条完整 URL。能取到模型列表才算连上。 */
async function connectOnce(): Promise<boolean> {
  await invoke<string>("connect_effective_server");
  const ok = await loadModels();
  connected.value = ok;
  return ok;
}

/**
 * 连到通为止,但有上限。
 *
 * `budgetMs = 0` 就是以前的行为:只试一次,不通就报「服务器无响应」。
 * 已经有一轮在跑时不会再起第二轮,只把截止时间往后推。
 */
function ensureConnected(budgetMs = 0): Promise<void> {
  connectDeadline = Math.max(connectDeadline, Date.now() + budgetMs);
  if (!connectRun) {
    connectRun = connectLoop().finally(() => { connectRun = null; connectDeadline = 0; });
  }
  return connectRun;
}

async function connectLoop() {
  connecting.value = true;
  connected.value = false;
  try {
    for (;;) {
      let failure = "";
      try {
        if (await connectOnce()) { toast("已连接", "ok"); return; }
      } catch (e) { failure = `${e}`; }
      // 预算用完才认输。中途每次失败都不吭声:服务还在加载模型是预期内的,
      // 每 2 秒弹一次红字只会把日志面板刷满。
      if (Date.now() >= connectDeadline) {
        toast(failure ? `连接失败: ${failure}` : "服务器无响应", "err");
        return;
      }
      await sleep(CONNECT_RETRY_MS);
    }
  } finally { connecting.value = false; }
}

/** 远程模式下手动「连接」。行为保持不变:存下输入框里的地址,只试一次。 */
async function updateServer() {
  const host = serverHost.value.trim() || "localhost";
  const port = serverPort.value || 6544;
  try {
    // `set_server_host` 自己会把地址写进配置并落盘,这里不用再存一遍。
    await invoke("set_server_host", { host, port });
  } catch (e) { toast(`连接失败: ${e}`, "err"); return; }
  await ensureConnected(0);
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
  await loadLlmModels();
  try { llmEnabled.value = await invoke<boolean>("get_llm_enabled"); } catch {}
  return ok;
}

/**
 * 拉 LLM 模型列表。单独一个函数是因为它和 STT 的列表**不是同时可用的**:
 * LLM 服务跟着后处理开关起停,关着的时候这个请求必然失败。
 *
 * 以前它只在 `loadModels()` 里跟着 STT 一起拉一次,失败了就空着,之后再没人
 * 补过——于是「打开后处理开关 → LLM 服务起来了 → 下拉框还是空的,没法选模型」。
 * 现在开关打开后、以及服务器面板看到 LLM 跑起来时都会再拉一次。
 */
async function loadLlmModels(): Promise<boolean> {
  llmModelsLoading.value = true;
  try {
    const llmList = await invoke<ModelInfo[]>("get_llm_models");
    llmModels.value = llmList;
    if (llmList.length > 0) { const loaded = llmList.find(m => m.is_loaded); llmModel.value = loaded?.name || llmList[0].name; }
    return llmList.length > 0;
  } catch (e) {
    console.error("get_llm_models error:", e);
    // 留着上一轮的陈列表会让用户以为还能选:服务已经停了,选了也切不动。
    llmModels.value = [];
    return false;
  } finally { llmModelsLoading.value = false; }
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
/**
 * 拨「LLM 后处理」开关。
 *
 * 本地管理模式下这一下不只是翻标志位:LLM 服务跟着起停,打开时要等它把模型
 * 加载完(几秒)。所以整个过程锁住开关并把文案改成「正在启动 LLM 服务…」——
 * 不然用户看到的是拨了之后几秒钟毫无反应,只会再拨一次。
 *
 * 失败一律把开关拨回去。界面上写着「已启用」而服务端并没有,是最坏的一种结果:
 * 之后每次转录都会去反代一个空端口。后端在失败时不会翻标志位,前端这里跟着回滚,
 * 两边就始终说的是同一件事。
 */
async function toggleLlm() {
  const want = llmEnabled.value;
  llmToggling.value = true;
  try {
    const msg = await invoke<string>("set_llm_enabled", { enabled: want });
    toast(msg || `LLM ${want ? '已启用' : '已禁用'}`, "ok");
  } catch (e) {
    llmEnabled.value = !want;
    toast(`${e}`, "err");
  }
  llmToggling.value = false;
  // 后端在返回成功之前已经等到 LLM 服务能应答了,这时候列表一定拉得到。
  // 关掉的时候不用拉:服务正要停,下拉框也已经收起来了。
  if (llmEnabled.value) await loadLlmModels();
  // 服务的状态刚刚被改过,面板上那一行得跟着更新。
  if (serverMode.value === "local") await refreshServers();
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
// ── Lifecycle ──
// 打开设置面板时重新查一次:用户可能刚在系统设置里改过授权
watch(showSettings, open => { if (open) refreshPermissions(); });

// 连接状态跟着观测到的服务健康走,而不是散在各个调用点上手动置 true / false。
//
// 只认「状态变了」的那一下,不是每个轮询 tick 都试:服务真起不来的时候,后者
// 就是每 3 秒一次的重试风暴。`ensureConnected` 那层的单飞再兜一次底——启动按钮
// 和这个 watcher 撞上时,后来的只是把截止时间往后推。
watch(sttState, (next, prev) => {
  if (serverMode.value !== "local" || next === prev) return;
  if (next === "running") {
    if (!connected.value) ensureConnected(CONNECT_BUDGET_RUNNING_MS);
  } else if (connected.value) {
    // 看到的不是「运行中」,连接就是断的——问的是此刻的健康状况,不是
    // 「上一次看到的是不是运行中」:后者会漏掉从没被观测到运行过的情形
    // (面板一直关着,轮询没跑过),于是服务停了录音按钮还亮着,
    // 等用户按下去才失败。
    connected.value = false;
  }
});

onMounted(async () => {
  await loadConfig();
  await loadAutostart();
  await refreshDevices();
  await refreshPermissions();
  await refreshServers();

  // 启动时这一次连接**不能 await**：下面还要注册快捷键 / 转录的事件监听，
  // 而本地模式下这个循环可能要等几十秒。以前它是一次性的所以看不出来。
  //
  // `auto_start` 时服务是 Rust 的 setup() 异步拉起的，这一刻 STT 多半还没
  // 开始监听端口（状态还是 stopped），所以不能只看状态来定预算。
  const bootBudget = serverMode.value === "local" && localAutoStart.value
    ? CONNECT_BUDGET_STARTING_MS
    : connectBudget();
  ensureConnected(bootBudget);

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
/* 连接中：黄色 + 呼吸，和「连不上」的死红区分开 */
.conn-dot.wait { background: var(--yellow); animation: conn-pulse 1.2s ease-in-out infinite; }
@keyframes conn-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
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
/* 开关生效中(本地模式下是在等 LLM 加载模型):换个颜色,别让这几秒看起来像卡死 */
.s-label.llm-busy { color: var(--yellow); }
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
/* 「外部（未识别）」用的中性色：它不是错误，只是本应用管不着，别拿红色吓人。 */
.perm-state.muted { color: var(--muted); background: rgba(113, 113, 122, 0.12); border-color: rgba(113, 113, 122, 0.3); }
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
