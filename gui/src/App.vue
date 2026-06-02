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
          <!-- Connection -->
          <div class="s-section">
            <div class="s-title">连接</div>
            <div class="s-row">
              <input class="s-input" v-model="serverAddress" placeholder="localhost:6544" list="server-history" @keyup.enter="updateServer" @change="onServerSettingChange" />
              <datalist id="server-history">
                <option v-for="addr in serverHistory" :key="addr" :value="addr" />
              </datalist>
              <button class="s-btn" @click="updateServer" :disabled="connecting">{{ connecting ? '...' : '连接' }}</button>
            </div>
            <div v-if="platformInfo" class="s-tip">
              服务器: {{ platformInfo.system }} {{ platformInfo.arch }} | 后端: {{ platformInfo.backend }}
              <span v-if="platformInfo.gpu"> | GPU: {{ platformInfo.gpu.name }}</span>
            </div>
          </div>

          <!-- Models -->
          <div class="s-section">
            <div class="s-title">STT 模型</div>
            <select class="s-select" v-model="sttModel" @change="switchStt">
              <option v-for="m in sttModels" :key="m.name" :value="m.name" :disabled="m.is_available === false">
                {{ m.name }} {{ m.is_loaded ? '✓' : '' }} {{ m.is_available === false ? '(不兼容)' : '' }}
              </option>
            </select>
            <div v-if="sttLoading" class="s-loading">切换中...</div>
            <div v-if="platformInfo" class="s-tip">
              推荐: {{ platformInfo.recommended_stt }} | 可用: {{ platformInfo.available_models.length }} 个模型
            </div>
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
                <option v-for="(name, id) in audioDevices" :key="id" :value="name">{{ name }}</option>
              </select>
              <button class="s-btn" @click="refreshDevices" title="刷新">🔄</button>
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
import { ref, computed, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// ── Types ──
interface ModelInfo { name: string; is_loaded: boolean; is_available?: boolean; }
interface VoiceInputConfig {
  server: { host: string; port: number; history?: string[] };
  hotkey: { key: string; distinguish_left_right: boolean };
  ui: { start_minimized: boolean; use_floating_indicator: boolean; use_tray: boolean; opacity: number; auto_input?: boolean };
  audio: { device: string | null; language: string };
  llm: { enabled: boolean };
  _version: string;
}
interface HistoryItem { text: string; time: string; }
interface PlatformInfo {
  system: string;
  arch: string;
  backend: string;
  gpu: { name: string; memory_gb: number } | null;
  recommended_stt: string;
  available_models: string[];
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
const serverHistory = ref<string[]>([]);
const platformInfo = ref<PlatformInfo | null>(null);
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
const audioDevices = ref<Record<string, string>>({});
const selectedDevice = ref<string | null>(null);

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
const serverAddress = computed({
  get: () => `${serverHost.value}:${serverPort.value}`,
  set: (val: string) => {
    const parts = val.split(':');
    if (parts.length === 2) {
      serverHost.value = parts[0] || 'localhost';
      serverPort.value = parseInt(parts[1]) || 6544;
    } else {
      serverHost.value = val;
    }
  }
});
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

async function saveConfigPatch(patch: (cfg: VoiceInputConfig) => void) {
  try {
    const cfg = await getConfig();
    patch(cfg);
    await invoke("update_config", { newConfig: cfg });
  } catch (e) { console.error("Config save failed:", e); }
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
  try { audioDevices.value = await invoke<Record<string, string>>("get_audio_devices"); } catch {}
}
function onDeviceChange() {
  saveConfigPatch(cfg => { cfg.audio.device = selectedDevice.value; });
}

// ── Config ──
async function loadConfig() {
  try {
    const cfg = await getConfig();
    serverHost.value = cfg.server.host;
    serverPort.value = cfg.server.port;
    serverHistory.value = cfg.server.history || [];
    version.value = cfg._version;
    hotkeyStr.value = cfg.hotkey.key;
    startMinimized.value = cfg.ui.start_minimized;
    autoInputEnabled.value = cfg.ui.auto_input ?? false;
    selectedDevice.value = cfg.audio.device;
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
      // Save to history
      const address = `${host}:${port}`;
      if (!serverHistory.value.includes(address)) {
        serverHistory.value.unshift(address);
        if (serverHistory.value.length > 20) serverHistory.value = serverHistory.value.slice(0, 20);
      } else {
        // Move to top
        serverHistory.value = [address, ...serverHistory.value.filter(a => a !== address)];
      }
      saveConfigPatch(cfg => {
        cfg.server.host = host;
        cfg.server.port = port;
        cfg.server.history = serverHistory.value;
      });
      // Fetch platform info
      fetchPlatformInfo();
    } else {
      toast("服务器无响应", "err");
    }
  } catch (e) { toast(`连接失败: ${e}`, "err"); }
  connecting.value = false;
}

async function fetchPlatformInfo() {
  try {
    platformInfo.value = await invoke<PlatformInfo>("get_platform_info");
  } catch (e) {
    console.error("Failed to fetch platform info:", e);
    platformInfo.value = null;
  }
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
  try { await invoke<string>("switch_model", { name: sttModel.value }); toast("模型已切换", "ok"); } catch (e) { toast("切换失败", "err"); }
  sttLoading.value = false;
}
async function switchLlm() {
  if (!llmModel.value) return;
  llmLoading.value = true;
  try { await invoke<string>("switch_llm_model", { name: llmModel.value }); toast("LLM 已切换", "ok"); } catch (e) { toast("切换失败", "err"); }
  llmLoading.value = false;
}
async function toggleLlm() {
  try { await invoke("set_llm_enabled", { enabled: llmEnabled.value }); toast(`LLM ${llmEnabled.value ? '已启用' : '已禁用'}`, "ok"); } catch { llmEnabled.value = !llmEnabled.value; }
}

// ── Hotkey ──
function startHotkeyRecording() {
  hotkeyRecording.value = !hotkeyRecording.value;
  hotkeyMsg.value = "";
  if (hotkeyRecording.value) {
    const handler = (e: KeyboardEvent) => {
      e.preventDefault(); e.stopPropagation();
      const parts: string[] = [];
      if (e.code?.startsWith('ControlLeft')) parts.push('left_ctrl');
      else if (e.code?.startsWith('ControlRight')) parts.push('right_ctrl');
      else if (e.code?.startsWith('AltLeft')) parts.push('left_alt');
      else if (e.code?.startsWith('AltRight')) parts.push('right_alt');
      else if (e.code?.startsWith('ShiftLeft')) parts.push('left_shift');
      else if (e.code?.startsWith('ShiftRight')) parts.push('right_shift');
      if (e.key !== 'Control' && e.key !== 'Alt' && e.key !== 'Shift' && e.key !== 'Meta') {
        parts.push(e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase());
      }
      if (parts.length > 0) {
        hotkeyStr.value = parts.join('+');
        hotkeyChanged.value = true;
        hotkeyRecording.value = false;
        document.removeEventListener('keydown', handler);
      }
    };
    document.addEventListener('keydown', handler);
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
  try { await invoke("auto_input", { text: result.value }); toast("已输入", "ok"); } catch { toast("输入失败", "err"); }
}
function clearResult() { result.value = ""; }
async function minimizeToTray() {
  try { await invoke("minimize_to_tray"); } catch {}
}

// ── Lifecycle ──
onMounted(async () => {
  await loadConfig();
  await loadAutostart();
  await refreshDevices();
  await updateServer();

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
      if (autoInputEnabled.value) invoke("auto_input", { text }).catch(() => {});
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
