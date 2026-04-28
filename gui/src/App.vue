<template>
  <div class="app">
    <header>
      <h1>🎙️ <span class="brand">Voice Input</span></h1>
      <span :class="['badge', connected ? 'connected' : 'disconnected']">
        {{ connected ? '已连接' : '未连接' }}
      </span>
    </header>

    <div class="status-row">
      <div :class="['status-dot', recording ? 'recording' : 'idle']" />
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
        <button class="icon-btn" @click="clearResult" title="清空">🗑️</button>
      </div>
      <p class="result-text">{{ result }}</p>
    </div>

    <div class="settings">
      <details open>
        <summary>模型设置</summary>
        <div class="setting-row">
          <label>STT 模型</label>
          <select v-model="sttModel" @change="switchStt">
            <option v-for="m in sttModels" :key="m.name" :value="m.name">
              {{ m.name }} {{ m.is_loaded ? '✅' : '' }}
            </option>
          </select>
        </div>
        <div class="setting-row">
          <label>LLM 后处理</label>
          <select v-model="llmModel" @change="switchLlm">
            <option v-for="m in llmModels" :key="m.name" :value="m.name">
              {{ m.name }} {{ m.is_loaded ? '✅' : '' }}
            </option>
          </select>
        </div>
      </details>
    </div>

    <div class="footer">
      <span>快捷键: ⌥⌘R</span>
      <span class="version">v2.0</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

interface ModelInfo {
  name: string;
  is_loaded: boolean;
}

// State
const recording = ref(false);
const connected = ref(false);
const result = ref("");
const sttModels = ref<ModelInfo[]>([]);
const llmModels = ref<ModelInfo[]>([]);
const sttModel = ref("");
const llmModel = ref("");
const elapsedMs = ref(0);
let timerInterval: ReturnType<typeof setInterval> | null = null;

const statusText = computed(() => {
  if (recording.value) return "录音中...";
  if (result.value) return "识别完成";
  return connected.value ? "就绪" : "连接服务器...";
});

const timerText = computed(() => {
  const secs = Math.floor(elapsedMs.value / 1000);
  const ms = elapsedMs.value % 1000;
  return `${secs}.${String(ms).padStart(3, "0").slice(0, 2)}s`;
});

// Audio recording + transcription
async function startRecord() {
  recording.value = true;
  elapsedMs.value = 0;
  timerInterval = setInterval(() => { elapsedMs.value += 100; }, 100);
  try {
    await invoke("start_recording");
  } catch (e) {
    console.error("Failed to start recording:", e);
    recording.value = false;
  }
}

async function stopRecord() {
  if (!recording.value) return;
  recording.value = false;
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }

  try {
    const audioData = await invoke<number[]>("stop_recording");
    const bytes = new Uint8Array(audioData);
    if (bytes.length > 320) {
      const text = await invoke<string>("transcribe", { audioData: bytes, language: "auto" });
      if (text) result.value = text;
    }
  } catch (e) {
    console.error("Transcription error:", e);
  }
}

// Model management
async function loadModels() {
  try {
    sttModels.value = await invoke<ModelInfo[]>("get_models");
    if (sttModels.value.length > 0) sttModel.value = sttModels.value[0].name;
    connected.value = true;
  } catch {
    connected.value = false;
  }
  try {
    llmModels.value = await invoke<ModelInfo[]>("get_llm_models");
    if (llmModels.value.length > 0) llmModel.value = llmModels.value[0].name;
  } catch { /* LLM optional */ }
}

async function switchStt() {
  if (sttModel.value) await invoke("switch_model", { name: sttModel.value });
}

async function switchLlm() {
  if (llmModel.value) await invoke("switch_llm_model", { name: llmModel.value });
}

function copyResult() {
  if (result.value) navigator.clipboard.writeText(result.value);
}

function clearResult() {
  result.value = "";
}

onMounted(() => {
  loadModels();
  listen("toggle-recording", () => {
    if (recording.value) stopRecord();
    else startRecord();
  });
});
</script>

<style>
:root {
  --bg: #1a1a2e;
  --card: #16213e;
  --accent: #0f3460;
  --green: #4ecca3;
  --red: #e63946;
  --text: #e8e8e8;
  --muted: #888;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, sans-serif;
  color: var(--text);
  background: var(--bg);
}

* { margin: 0; padding: 0; box-sizing: border-box; }

.app {
  max-width: 380px; margin: 0 auto; padding: 16px;
  display: flex; flex-direction: column; gap: 16px;
  min-height: 100vh;
}

header { display: flex; justify-content: space-between; align-items: center; }
h1 { font-size: 1.3rem; }
.brand { color: var(--green); }
.badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; }
.badge.connected { background: #1b4332; color: var(--green); }
.badge.disconnected { background: #3d1515; color: var(--red); }

.status-row { display: flex; align-items: center; gap: 8px; justify-content: center; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; }
.status-dot.idle { background: var(--muted); }
.status-dot.recording { background: var(--red); animation: pulse 0.8s infinite; }
.status-text { font-size: 0.9rem; }
.timer { font-size: 0.8rem; color: var(--red); font-variant-numeric: tabular-nums; }

@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

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
.icon-btn { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 0.9rem; padding: 2px; }
.icon-btn:hover { color: var(--text); }

.settings details { background: var(--card); border-radius: 12px; padding: 12px; border: 1px solid #0f3460; }
.settings summary { cursor: pointer; font-weight: 600; font-size: 0.85rem; margin-bottom: 8px; }
.setting-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 0.8rem; }
.setting-row select { background: var(--bg); color: var(--text); border: 1px solid #333; border-radius: 6px; padding: 4px 8px; max-width: 200px; }

.footer { display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--muted); margin-top: auto; }
</style>
