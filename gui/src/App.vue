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
        <summary>连接设置</summary>
        <div class="setting-row">
          <label>服务器地址</label>
          <div class="host-input">
            <input v-model="serverHost" placeholder="localhost" @change="updateServer" />
            <span :class="['dot', connected ? 'green' : 'red']"></span>
          </div>
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
import { ref, computed, onMounted, onUnmounted } from "vue";
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
const serverHost = ref("localhost");
const elapsedMs = ref(0);
let timerInterval: ReturnType<typeof setInterval> | null = null;
let mediaRecorder: MediaRecorder | null = null;
let audioChunks: Blob[] = [];

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

// ── WAV 编码 ──
function encodeWav(samples: Float32Array, sampleRate: number): Uint8Array {
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
  const blockAlign = numChannels * (bitsPerSample / 8);
  const dataSize = samples.length * (bitsPerSample / 8);
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  const writeStr = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);          // PCM
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeStr(36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset += 2;
  }
  return new Uint8Array(buffer);
}

// ── 录音 ──
async function startRecord() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=pcm" });
    
    // Fallback: use default codec if PCM not supported
    if (!MediaRecorder.isTypeSupported("audio/webm;codecs=pcm")) {
      mediaRecorder = new MediaRecorder(stream);
    }

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());

      // Decode audio to PCM via AudioContext
      const blob = new Blob(audioChunks, { type: mediaRecorder?.mimeType });
      const arrayBuf = await blob.arrayBuffer();
      
      try {
        const audioCtx = new OfflineAudioContext(1, 16000, 16000);
        const audioBuf = await audioCtx.decodeAudioData(arrayBuf);
        
        // Resample to 16kHz mono
        const original = audioBuf.getChannelData(0);
        const ratio = original.length / 16000;
        const resampled = new Float32Array(16000);
        for (let i = 0; i < 16000; i++) {
          const srcIdx = Math.floor(i * ratio);
          resampled[i] = original[Math.min(srcIdx, original.length - 1)];
        }

        // Encode to WAV and send to STT
        const wav = encodeWav(resampled, 16000);
        if (wav.length > 44 + 320) {
          const audioArray = Array.from(wav);
          const text = await invoke<string>("transcribe", { audioData: audioArray, language: "auto" });
          if (text) result.value = text;
        }
      } catch (e) {
        console.error("Audio decode error:", e);
      }
    };

    recording.value = true;
    elapsedMs.value = 0;
    timerInterval = setInterval(() => { elapsedMs.value += 100; }, 100);
    mediaRecorder.start();
  } catch (e) {
    console.error("Failed to start recording:", e);
    recording.value = false;
  }
}

async function stopRecord() {
  if (!recording.value || !mediaRecorder) return;
  recording.value = false;
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  if (mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
}

// ── 服务器连接 ──
async function updateServer() {
  connected.value = false;
  const host = serverHost.value.trim() || "localhost";
  try {
    await invoke("set_server_host", { host });
    await loadModels();
  } catch { /* will retry below */ }
}

// ── 模型管理 ──
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
  } catch { /* optional */ }
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
.host-input { display: flex; align-items: center; gap: 6px; }
.host-input input { background: var(--bg); color: var(--text); border: 1px solid #333; border-radius: 6px; padding: 4px 8px; width: 160px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.green { background: var(--green); }
.dot.red { background: var(--red); }

.footer { display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--muted); margin-top: auto; }
</style>
