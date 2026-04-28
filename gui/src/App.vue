<template>
  <div class="app">
    <header>
      <h1>🎙️ <span class="brand">Voice Input</span></h1>
      <span :class="['badge', connected ? 'connected' : 'disconnected']">
        {{ connected ? '已连接' : '未连接' }}
      </span>
    </header>

    <!-- Toast notifications -->
    <div class="toast-container">
      <transition-group name="toast">
        <div v-for="t in toasts" :key="t.id" :class="['toast', t.type]">
          {{ t.msg }}
        </div>
      </transition-group>
    </div>

    <div class="status-row">
      <div :class="['status-dot', recording ? 'recording' : (loading ? 'loading' : 'idle')]" />
      <span class="status-text">{{ statusText }}</span>
      <span v-if="recording" class="timer">{{ timerText }}</span>
    </div>

    <button
      @mousedown="startRecord"
      @mouseup="stopRecord"
      @mouseleave="stopRecord"
      :class="['record-btn', { active: recording }]"
    >
      <span class="icon">{{ recording ? '⏹' : '🎤' }}</span>
      <span class="label">{{ recording ? '松开停止' : '按住说话' }}</span>
    </button>

    <div v-if="result" class="result-box">
      <div class="result-header">
        <span>识别结果</span>
        <button class="icon-btn" @click="copyResult" title="复制">📋</button>
        <button class="icon-btn" @click="doAutoInput" title="输入到窗口">⌨️</button>
        <button class="icon-btn" @click="clearResult" title="清空">🗑️</button>
      </div>
      <p class="result-text">{{ result }}</p>
    </div>

    <div class="settings">
      <details open>
        <summary>连接设置</summary>
        <div class="setting-row">
          <label>服务器地址</label>
          <div class="host-input">
            <input v-model="serverHost" @keyup.enter="updateServer" />
            <button class="tiny-btn" @click="updateServer">连接</button>
            <span :class="['dot', connected ? 'green' : 'red']"></span>
          </div>
        </div>
        <div class="setting-row">
          <label>端口</label>
          <input class="num-input" v-model.number="serverPort" type="number" @keyup.enter="updateServer" />
        </div>
      </details>

      <details>
        <summary>模型设置</summary>
        <div class="setting-row">
          <label>STT 模型</label>
          <select v-model="sttModel" @change="switchStt">
            <option v-for="m in sttModels" :key="m.name" :value="m.name">
              {{ m.name }} {{ m.is_loaded ? '✅' : '' }}
            </option>
          </select>
          <span v-if="sttLoading" class="spinner"></span>
        </div>
        <div v-if="sttStatus" class="status-msg">{{ sttStatus }}</div>
        <div class="setting-row">
          <label>LLM 后处理</label>
          <label class="toggle">
            <input type="checkbox" v-model="llmEnabled" @change="toggleLlm" />
            <span class="slider"></span>
          </label>
        </div>
        <div v-if="llmEnabled" class="setting-row">
          <label>LLM 模型</label>
          <select v-model="llmModel" @change="switchLlm">
            <option v-for="m in llmModels" :key="m.name" :value="m.name">
              {{ m.name }} {{ m.is_loaded ? '✅' : '' }}
            </option>
          </select>
          <span v-if="llmLoading" class="spinner"></span>
        </div>
        <div v-if="llmStatus" class="status-msg">{{ llmStatus }}</div>
      </details>

      <details v-if="llmEnabled">
        <summary>提示词管理</summary>
        <div class="textarea-row">
          <textarea v-model="promptText" rows="4"
            placeholder="输入 LLM 后处理提示词..." />
        </div>
        <div class="btn-row">
          <button class="tiny-btn" @click="loadPrompt" :disabled="promptLoading">📥 加载</button>
          <button class="tiny-btn" @click="savePrompt" :disabled="promptLoading">💾 保存</button>
          <span v-if="promptLoading" class="spinner"></span>
        </div>
        <div v-if="promptStatus" :class="['status-msg', promptStatusType]">{{ promptStatus }}</div>
      </details>

      <details>
        <summary>输入设置</summary>
        <div class="setting-row">
          <label>自动输入到窗口</label>
          <label class="toggle">
            <input type="checkbox" v-model="autoInputEnabled" />
            <span class="slider"></span>
          </label>
        </div>
        <div class="setting-tip">识别完成后自动将结果输入当前活跃窗口</div>
      </details>

      <details>
        <summary>配置管理</summary>
        <div class="btn-row">
          <button class="tiny-btn" @click="saveConfig">💾 保存配置</button>
          <button class="tiny-btn" @click="importOldConfig">📥 导入旧版</button>
        </div>
        <div v-if="configMsg" class="status-msg success">{{ configMsg }}</div>
      </details>

      <!-- Event log -->
      <details>
        <summary>事件日志</summary>
        <div class="log-box" ref="logBox">
          <div v-for="(entry, i) in log" :key="i" :class="['log-entry', entry.type]">
            <span class="log-time">{{ entry.time }}</span>
            <span class="log-msg">{{ entry.msg }}</span>
          </div>
          <div v-if="log.length === 0" class="log-empty">暂无记录</div>
        </div>
        <button class="tiny-btn" @click="clearLog" style="margin-top:4px">清空日志</button>
      </details>
    </div>

    <div class="footer">
      <span>快捷键: ⌥⌘R (mac) / ⌃⌥R (win)</span>
      <span class="version">{{ version }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// ── Types ──
interface ModelInfo { name: string; is_loaded: boolean; }
interface VoiceInputConfig {
  server: { host: string; port: number };
  hotkey: { key: string; distinguish_left_right: boolean };
  ui: { start_minimized: boolean; use_floating_indicator: boolean; use_tray: boolean; opacity: number };
  audio: { device: string | null; language: string };
  llm: { enabled: boolean };
  _version: string;
}

interface LogEntry { time: string; msg: string; type: 'info' | 'ok' | 'err' | 'warn'; }
interface Toast { id: number; msg: string; type: 'info' | 'ok' | 'err'; }

// ── Reactive State ──
const recording = ref(false);
const connected = ref(false);
const loading = ref(false);
const result = ref("");
const version = ref("v2.0");

const sttModels = ref<ModelInfo[]>([]);
const llmModels = ref<ModelInfo[]>([]);
const sttModel = ref("");
const llmModel = ref("");
const sttLoading = ref(false);
const llmLoading = ref(false);
const sttStatus = ref("");
const llmStatus = ref("");
const promptLoading = ref(false);
const promptStatus = ref("");
const promptStatusType = ref<"info" | "ok" | "err">("info");

const serverHost = ref("localhost");
const serverPort = ref(6544);
const llmEnabled = ref(true);
const promptText = ref("");
const configMsg = ref("");
const autoInputEnabled = ref(false);

const elapsedMs = ref(0);
const log = ref<LogEntry[]>([]);
const toasts = ref<Toast[]>([]);
const logBox = ref<HTMLElement | null>(null);

let timerInterval: ReturnType<typeof setInterval> | null = null;
let mediaRecorder: MediaRecorder | null = null;
let audioChunks: Blob[] = [];
let toastId = 0;

// ── Computed ──
const statusText = computed(() => {
  if (recording.value) return "录音中...";
  if (loading.value) return "处理中...";
  if (result.value) return "识别完成";
  return connected.value ? "就绪" : "连接服务器...";
});

const timerText = computed(() => {
  const secs = Math.floor(elapsedMs.value / 1000);
  const ms = elapsedMs.value % 1000;
  return `${secs}.${String(ms).padStart(3,"0").slice(0,2)}s`;
});

// ── Toast & Log helpers ──
function toast(msg: string, type: 'info'|'ok'|'err' = 'info') {
  const id = ++toastId;
  toasts.value.push({ id, msg, type });
  setTimeout(() => {
    toasts.value = toasts.value.filter(t => t.id !== id);
  }, 3000);
}

function logMsg(msg: string, type: 'info'|'ok'|'err'|'warn' = 'info') {
  const now = new Date();
  const time = now.toLocaleTimeString();
  log.value.push({ time, msg, type });
  if (log.value.length > 200) log.value = log.value.slice(-200);
  nextTick(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight;
  });
}

function clearLog() { log.value = []; }

// ── WAV Encoder ──
function encodeWav(samples: Float32Array, sr: number): Uint8Array {
  const nc = 1, bps = 16;
  const br = sr * nc * (bps/8), ba = nc * (bps/8);
  const ds = samples.length * (bps/8);
  const buf = new ArrayBuffer(44 + ds);
  const v = new DataView(buf);
  const w = (o: number, s: string) => { for (let i=0;i<s.length;i++) v.setUint8(o+i,s.charCodeAt(i)); };
  w(0,"RIFF"); v.setUint32(4,36+ds,true); w(8,"WAVE");
  w(12,"fmt "); v.setUint32(16,16,true);
  v.setUint16(20,1,true); v.setUint16(22,nc,true);
  v.setUint32(24,sr,true); v.setUint32(28,br,true);
  v.setUint16(32,ba,true); v.setUint16(34,bps,true);
  w(36,"data"); v.setUint32(40,ds,true);
  let o = 44;
  for (const s of samples) {
    const c = Math.max(-1,Math.min(1,s));
    v.setInt16(o,c<0?c*0x8000:c*0x7FFF,true);
    o += 2;
  }
  return new Uint8Array(buf);
}

// ── Recording ──
async function startRecord() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      const blob = new Blob(audioChunks, { type: mediaRecorder?.mimeType });
      try {
        const ab = await blob.arrayBuffer();
        const ctx = new OfflineAudioContext(1, 16000, 16000);
        const abuf = await ctx.decodeAudioData(ab);
        const orig = abuf.getChannelData(0);
        const ratio = orig.length / 16000;
        const resampled = new Float32Array(16000);
        for (let i = 0; i < 16000; i++) resampled[i] = orig[Math.min(Math.floor(i*ratio),orig.length-1)];
        const wav = encodeWav(resampled, 16000);
        if (wav.length > 44 + 320) {
          logMsg("正在识别...", "info");
          loading.value = true;
          const text = await invoke<string>("transcribe_ws", { audioData: Array.from(wav) });
          loading.value = false;
          if (text) {
            result.value = text;
            logMsg(`识别完成: "${text.slice(0,40)}${text.length>40?'...':''}"`, "ok");
            toast("识别完成 ✅", "ok");
            // Auto-input if enabled
            if (autoInputEnabled.value) {
              logMsg("自动输入已启用，正在输入到窗口...", "info");
              try {
                await invoke("auto_input", { text });
                logMsg("自动输入完成", "ok");
              } catch (e) {
                logMsg(`自动输入失败: ${e}`, "err");
              }
            }
          }
        }
      } catch (e) { logMsg(`音频解码失败: ${e}`, "err"); }
    };
    recording.value = true;
    elapsedMs.value = 0;
    timerInterval = setInterval(() => { elapsedMs.value += 100; }, 100);
    mediaRecorder.start();
    logMsg("开始录音", "info");
  } catch (e) {
    logMsg(`录音启动失败: ${e}`, "err");
    recording.value = false;
  }
}

async function stopRecord() {
  if (!recording.value || !mediaRecorder) return;
  recording.value = false;
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  if (mediaRecorder.state !== "inactive") mediaRecorder.stop();
  logMsg("录音结束", "info");
}

// ── Config ──
async function loadConfig() {
  try {
    const cfg = await invoke<VoiceInputConfig>("get_config");
    serverHost.value = cfg.server.host;
    serverPort.value = cfg.server.port;
    version.value = `v${cfg._version}`;
    logMsg("配置已加载", "info");
  } catch (e) {
    logMsg(`加载配置失败: ${e}`, "warn");
  }
}

async function saveConfig() {
  try {
    const cfg = await invoke<VoiceInputConfig>("get_config");
    cfg.server.host = serverHost.value;
    cfg.server.port = serverPort.value;
    await invoke("update_config", { newConfig: cfg });
    configMsg.value = "✅ 配置已保存";
    toast("配置已保存 ✅", "ok");
    logMsg("配置已保存", "ok");
    setTimeout(() => { configMsg.value = ""; }, 3000);
  } catch (e) {
    configMsg.value = `❌ 保存失败`;
    toast(`配置保存失败`, "err");
    logMsg(`保存配置失败: ${e}`, "err");
  }
}

async function importOldConfig() {
  try {
    const cfg = await invoke<VoiceInputConfig>("import_old_config");
    serverHost.value = cfg.server.host;
    serverPort.value = cfg.server.port;
    toast("旧版配置已导入 ✅", "ok");
    logMsg("旧版配置已导入并转换", "ok");
    await updateServer();
  } catch (e) {
    toast("导入旧版配置失败 ❌", "err");
    logMsg(`导入旧版配置失败: ${e}`, "err");
  }
}

// ── Connection ──
async function updateServer() {
  connected.value = false;
  loading.value = true;
  const host = serverHost.value.trim() || "localhost";
  logMsg(`正在连接服务器 ${host}:${serverPort.value}...`, "info");
  try {
    await invoke("set_server_host", { host });
    await loadModels();
    connected.value = true;
    toast("服务器已连接 ✅", "ok");
    logMsg("服务器已连接", "ok");
  } catch (e) {
    logMsg(`连接服务器失败: ${e}`, "err");
    toast("连接服务器失败 ❌", "err");
  }
  loading.value = false;
}

// ── Models ──
async function loadModels() {
  try {
    const sttList = await invoke<ModelInfo[]>("get_models");
    sttModels.value = sttList;
    if (sttList.length > 0 && !sttModel.value) sttModel.value = sttList[0].name;
    logMsg(`获取到 ${sttList.length} 个 STT 模型`, "info");
  } catch { connected.value = false; logMsg("获取 STT 模型失败", "err"); }

  try {
    const llmList = await invoke<ModelInfo[]>("get_llm_models");
    llmModels.value = llmList;
    if (llmList.length > 0 && !llmModel.value) llmModel.value = llmList[0].name;
    logMsg(`获取到 ${llmList.length} 个 LLM 模型`, "info");
  } catch { logMsg("获取 LLM 模型失败（未配置后端）", "warn"); }

  try {
    llmEnabled.value = await invoke<boolean>("get_llm_enabled");
    logMsg(`LLM 后处理: ${llmEnabled.value ? '已启用' : '已禁用'}`, "info");
  } catch { /* optional */ }
}

async function switchStt() {
  if (!sttModel.value) return;
  sttLoading.value = true;
  sttStatus.value = `正在切换模型 ${sttModel.value}...`;
  logMsg(`切换 STT 模型: ${sttModel.value}`, "info");
  try {
    const msg = await invoke<string>("switch_model", { name: sttModel.value });
    sttStatus.value = `✅ ${msg}`;
    toast(`STT 模型切换成功`, "ok");
    logMsg(`STT 模型已切换: ${sttModel.value}`, "ok");
  } catch (e) {
    sttStatus.value = `❌ 切换失败: ${e}`;
    logMsg(`STT 模型切换失败: ${e}`, "err");
    toast(`STT 模型切换失败`, "err");
  }
  sttLoading.value = false;
}

async function switchLlm() {
  if (!llmModel.value) return;
  llmLoading.value = true;
  llmStatus.value = `正在切换模型 ${llmModel.value}...`;
  logMsg(`切换 LLM 模型: ${llmModel.value}`, "info");
  try {
    const msg = await invoke<string>("switch_llm_model", { name: llmModel.value });
    llmStatus.value = `✅ ${msg}`;
    toast(`LLM 模型切换成功`, "ok");
    logMsg(`LLM 模型已切换: ${llmModel.value}`, "ok");
  } catch (e) {
    llmStatus.value = `❌ 切换失败: ${e}`;
    logMsg(`LLM 模型切换失败: ${e}`, "err");
    toast(`LLM 模型切换失败`, "err");
  }
  llmLoading.value = false;
}

async function toggleLlm() {
  logMsg(`LLM 后处理: ${llmEnabled.value ? '启用' : '禁用'}中...`, "info");
  try {
    await invoke("set_llm_enabled", { enabled: llmEnabled.value });
    toast(`LLM 后处理已${llmEnabled.value ? '启用' : '禁用'} ✅`, "ok");
    logMsg(`LLM 后处理已${llmEnabled.value ? '启用' : '禁用'}`, "ok");
  } catch (e) {
    llmEnabled.value = !llmEnabled.value;
    logMsg(`切换 LLM 状态失败: ${e}`, "err");
    toast("LLM 切换失败", "err");
  }
}

// ── LLM Prompt ──
async function loadPrompt() {
  promptLoading.value = true;
  promptStatus.value = "加载中...";
  promptStatusType.value = "info";
  logMsg("正在加载 LLM 提示词...", "info");
  try {
    promptText.value = await invoke<string>("get_llm_prompt");
    promptStatus.value = "✅ 已加载";
    promptStatusType.value = "ok";
    toast("提示词已加载 ✅", "ok");
    logMsg("LLM 提示词已加载", "ok");
  } catch (e) {
    promptStatus.value = `❌ ${e}`;
    promptStatusType.value = "err";
    logMsg(`加载提示词失败: ${e}`, "err");
  }
  promptLoading.value = false;
}

async function savePrompt() {
  if (!promptText.value.trim()) {
    promptStatus.value = "⚠️ 提示词不能为空";
    promptStatusType.value = "err";
    return;
  }
  promptLoading.value = true;
  promptStatus.value = "保存中...";
  promptStatusType.value = "info";
  logMsg("正在保存 LLM 提示词...", "info");
  try {
    await invoke("save_llm_prompt", { text: promptText.value });
    promptStatus.value = "✅ 已保存";
    promptStatusType.value = "ok";
    toast("提示词已保存 ✅", "ok");
    logMsg("LLM 提示词已保存", "ok");
  } catch (e) {
    promptStatus.value = `❌ ${e}`;
    promptStatusType.value = "err";
    logMsg(`保存提示词失败: ${e}`, "err");
  }
  promptLoading.value = false;
}

// ── Result ──
function copyResult() {
  if (result.value) {
    navigator.clipboard.writeText(result.value);
    toast("已复制到剪贴板 ✅", "ok");
    logMsg("结果已复制到剪贴板", "ok");
  }
}

async function doAutoInput() {
  if (!result.value) return;
  logMsg(`正在输入: "${result.value.slice(0,40)}${result.value.length>40?'...':''}"`, "info");
  try {
    await invoke("auto_input", { text: result.value });
    toast("已输入到窗口 ✅", "ok");
    logMsg("自动输入完成", "ok");
  } catch (e) {
    logMsg(`自动输入失败: ${e}`, "err");
    toast("输入失败（请确认目标窗口处于激活状态）", "err");
  }
}

function clearResult() {
  result.value = "";
  logMsg("结果已清空", "info");
}

// ── Lifecycle ──
onMounted(async () => {
  logMsg("Voice Input 客户端启动", "info");
  await loadConfig();
  await updateServer();
  listen("toggle-recording", () => {
    if (recording.value) stopRecord(); else startRecord();
  });
});

onUnmounted(() => {
  if (timerInterval) clearInterval(timerInterval);
});
</script>

<style>
:root {
  --bg: #1a1a2e;
  --card: #16213e;
  --accent: #0f3460;
  --green: #4ecca3;
  --red: #e63946;
  --yellow: #f0a500;
  --text: #e8e8e8;
  --muted: #888;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--text);
  background: var(--bg);
}
* { margin: 0; padding: 0; box-sizing: border-box; }

.app {
  max-width: 380px; margin: 0 auto; padding: 16px;
  display: flex; flex-direction: column; gap: 12px;
  min-height: 100vh;
}

header { display: flex; justify-content: space-between; align-items: center; }
h1 { font-size: 1.3rem; }
.brand { color: var(--green); }
.badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; }
.badge.connected { background: #1b4332; color: var(--green); }
.badge.disconnected { background: #3d1515; color: var(--red); }

/* Toast */
.toast-container {
  position: fixed; top: 12px; right: 12px; z-index: 999;
  display: flex; flex-direction: column; gap: 6px;
}
.toast {
  padding: 6px 12px; border-radius: 8px; font-size: 0.75rem;
  box-shadow: 0 2px 8px rgba(0,0,0,0.4); min-width: 160px;
}
.toast.ok { background: #1b4332; color: var(--green); border: 1px solid var(--green); }
.toast.err { background: #3d1515; color: var(--red); border: 1px solid var(--red); }
.toast.info { background: #1a2a4a; color: #88ccff; border: 1px solid #336699; }
.toast-enter-active, .toast-leave-active { transition: all 0.3s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(40px); }

.status-row { display: flex; align-items: center; gap: 8px; justify-content: center; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; }
.status-dot.idle { background: var(--muted); }
.status-dot.recording { background: var(--red); animation: pulse 0.8s infinite; }
.status-dot.loading { background: var(--yellow); animation: pulse 1.2s infinite; }
.status-text { font-size: 0.9rem; }
.timer { font-size: 0.8rem; color: var(--red); font-variant-numeric: tabular-nums; }

@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
@keyframes spin { to { transform: rotate(360deg); } }

.record-btn {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  width: 140px; height: 140px; margin: 0 auto; border-radius: 50%;
  border: 4px solid var(--accent); background: var(--card);
  color: var(--text); cursor: pointer; transition: all 0.2s;
}
.record-btn.active { border-color: var(--red); background: #3d1515; }
.record-btn:hover { transform: scale(1.05); }
.record-btn:active { transform: scale(0.95); }
.record-btn .icon { font-size: 2rem; }
.record-btn .label { font-size: 0.75rem; color: var(--muted); }

.result-box {
  background: var(--card); border-radius: 12px; padding: 12px;
  border: 1px solid #0f3460;
}
.result-header { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; font-size: 0.8rem; color: var(--muted); }
.result-header :first-child { flex: 1; }
.result-text { font-size: 0.95rem; line-height: 1.5; }
.icon-btn, .tiny-btn { background: var(--accent); color: var(--text); border: none; border-radius: 6px; cursor: pointer; }
.icon-btn { font-size: 0.9rem; padding: 2px; }
.icon-btn:hover { color: white; }
.tiny-btn { padding: 3px 8px; font-size: 0.7rem; white-space: nowrap; }
.tiny-btn:hover { filter: brightness(1.3); }
.tiny-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.settings details {
  background: var(--card); border-radius: 12px; padding: 12px;
  border: 1px solid #0f3460;
}
.settings summary { cursor: pointer; font-weight: 600; font-size: 0.85rem; margin-bottom: 8px; }
.setting-row {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px; font-size: 0.8rem; gap: 6px;
}
.setting-row select {
  background: var(--bg); color: var(--text); border: 1px solid #333;
  border-radius: 6px; padding: 4px 8px; max-width: 160px; flex: 1;
}
.host-input { display: flex; align-items: center; gap: 6px; flex: 1; }
.host-input input {
  background: var(--bg); color: var(--text); border: 1px solid #333;
  border-radius: 6px; padding: 4px 8px; width: 100%;
}
.num-input {
  background: var(--bg); color: var(--text); border: 1px solid #333;
  border-radius: 6px; padding: 4px 8px; width: 70px;
}
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot.green { background: var(--green); }
.dot.red { background: var(--red); }

/* Toggle switch */
.toggle { position: relative; display: inline-block; width: 36px; height: 20px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.slider {
  position: absolute; cursor: pointer; inset: 0;
  background: #555; border-radius: 20px; transition: 0.3s;
}
.slider::before {
  content: ""; position: absolute; width: 16px; height: 16px;
  left: 2px; bottom: 2px; background: white; border-radius: 50%; transition: 0.3s;
}
.toggle input:checked + .slider { background: var(--green); }
.toggle input:checked + .slider::before { transform: translateX(16px); }

/* Spinner */
.spinner {
  width: 14px; height: 14px; border: 2px solid var(--muted);
  border-top-color: var(--green); border-radius: 50%;
  animation: spin 0.6s linear infinite; flex-shrink: 0;
}

/* Status messages */
.status-msg { font-size: 0.7rem; padding: 4px 8px; border-radius: 6px; margin-bottom: 4px; }
.status-msg.ok { color: var(--green); }
.status-msg.err { color: var(--red); }
.setting-tip { font-size: 0.65rem; color: var(--muted); padding: 4px 0; }

.textarea-row { margin-bottom: 6px; }
.textarea-row textarea {
  width: 100%; background: var(--bg); color: var(--text);
  border: 1px solid #333; border-radius: 6px; padding: 6px;
  font-size: 0.8rem; resize: vertical; font-family: inherit;
}
.btn-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }

/* Log box */
.log-box {
  background: var(--bg); border-radius: 6px; padding: 6px;
  max-height: 180px; overflow-y: auto; font-size: 0.7rem;
  font-family: monospace; line-height: 1.6;
}
.log-entry { display: flex; gap: 6px; }
.log-time { color: var(--muted); flex-shrink: 0; }
.log-msg { word-break: break-all; }
.log-entry.ok .log-msg { color: var(--green); }
.log-entry.err .log-msg { color: var(--red); }
.log-entry.warn .log-msg { color: var(--yellow); }
.log-empty { color: var(--muted); font-style: italic; }

.footer { display: flex; justify-content: space-between; font-size: 0.65rem; color: var(--muted); margin-top: auto; }
</style>
