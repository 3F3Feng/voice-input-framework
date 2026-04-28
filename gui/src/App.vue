<template>
  <div class="app">
    <h1>🎙️ Voice Input</h1>
    <div class="status">
      <span :class="['indicator', recording ? 'recording' : 'idle']"></span>
      <span>{{ recording ? '录音中...' : '就绪' }}</span>
    </div>
    <button @click="toggleRecording" :class="['record-btn', { active: recording }]">
      {{ recording ? '⏹ 停止' : '⏺ 开始录音' }}
    </button>
    <div v-if="result" class="result">
      <h3>识别结果:</h3>
      <p>{{ result }}</p>
    </div>
    <div class="models">
      <label>模型:</label>
      <select v-model="selectedModel" @change="switchModel">
        <option v-for="m in models" :key="m" :value="m">{{ m }}</option>
      </select>
    </div>
    <p class="shortcut">快捷键: ⌘+⌥+R</p>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

const recording = ref(false);
const result = ref("");
const models = ref<string[]>([]);
const selectedModel = ref("");

async function toggleRecording() {
  recording.value = !recording.value;
  if (!recording.value) {
    // 停止录音时，这里简化为空实现
    // 实际录音和发送音频的逻辑待后续实现
    result.value = "（录音功能待实现 - 需要前端音频录制模块）";
  }
}

async function loadModels() {
  try {
    models.value = await invoke<string[]>("get_models");
    if (models.value.length > 0) {
      selectedModel.value = models.value[0];
    }
  } catch (e) {
    console.error("Failed to load models:", e);
  }
}

async function switchModel() {
  try {
    await invoke("switch_model", { name: selectedModel.value });
  } catch (e) {
    console.error("Failed to switch model:", e);
  }
}

onMounted(() => {
  loadModels();
  listen("toggle-recording", () => {
    toggleRecording();
  });
});
</script>

<style>
:root {
  font-family: Inter, system-ui, Avenir, Helvetica, Arial, sans-serif;
  color: #0f0f0f;
  background-color: #f6f6f6;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.app {
  max-width: 360px;
  margin: 20px auto;
  padding: 20px;
  text-align: center;
}

h1 {
  font-size: 1.5rem;
  margin-bottom: 16px;
}

.status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-bottom: 16px;
}

.indicator {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.indicator.idle {
  background: #ccc;
}

.indicator.recording {
  background: #e53935;
  animation: pulse 1s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.record-btn {
  padding: 12px 24px;
  font-size: 1rem;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  background: #4caf50;
  color: white;
  transition: background 0.2s;
}

.record-btn.active {
  background: #e53935;
}

.record-btn:hover {
  opacity: 0.9;
}

.result {
  margin-top: 16px;
  padding: 12px;
  background: white;
  border-radius: 8px;
  text-align: left;
}

.result h3 {
  font-size: 0.9rem;
  margin-bottom: 4px;
  color: #666;
}

.result p {
  font-size: 1rem;
  line-height: 1.4;
}

.models {
  margin-top: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.models select {
  padding: 4px 8px;
  border-radius: 4px;
  border: 1px solid #ccc;
}

.shortcut {
  margin-top: 12px;
  font-size: 0.8rem;
  color: #999;
}
</style>
