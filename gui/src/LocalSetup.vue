<!--
  「本机安装」:把服务端代码取下来、把 Python 环境建好(Rust 那边见 local_setup.rs)。

  以前这一步只有一句「在终端里运行这条命令」,再往前的 `git clone` 连命令都不给,
  要用户自己去 README 里抄。没碰过命令行的人选了「本地管理」就卡在这儿。

  向导的「准备本机服务」和设置页的环境体检下面各放一个。没事可做(仓库在、环境好)
  时什么都不显示。
-->
<template>
  <div v-if="visible" class="ls-box">
    <!-- 装好之后没事可做了:只留下那句结果,不再摆着「重建环境」。 -->
    <template v-if="!needed && !busy"></template>
    <template v-else-if="!status?.repo_path">
      <div class="s-label">{{ t('这台电脑上还没有服务端代码', "The service code isn't on this computer yet") }}</div>
      <div class="s-tip">{{ t('可以在这里一键下载并装好:取代码(git clone)→ 装 Python 依赖。依赖有几百 MB 到几 GB,视网络要几分钟到十几分钟。', 'Download and set it up here in one click: fetch the code (git clone) → install the Python dependencies. The dependencies are hundreds of MB to a few GB, so this takes a few minutes or more depending on your network.') }}</div>
      <div class="s-row" style="margin-top:4px">
        <span class="s-tip" style="margin:0">{{ t('下载到', 'Download to') }}</span>
        <input class="s-input" v-model="dir" :disabled="busy" :placeholder="status?.target_dir ?? ''" @change="refresh" />
      </div>
      <div v-if="status?.target_state === 'occupied'" class="s-tip s-err">{{ t('这个文件夹里已经有别的文件,不会往里面下载。换一个不存在或空的文件夹。', "This folder already contains other files, so nothing will be downloaded into it. Pick a folder that doesn't exist or is empty.") }}</div>
      <template v-if="status && !status.git">
        <div class="s-tip srv-problem">{{ status.git_hint.text }}</div>
        <div v-if="status.git_hint.command" class="s-row" style="margin-top:4px">
          <code class="env-cmd">{{ status.git_hint.command }}</code>
          <button class="s-btn" @click="copy(status.git_hint.command)">{{ copied ? t('已复制', 'Copied') : t('复制', 'Copy') }}</button>
          <button class="s-btn" @click="refresh" :disabled="checking">{{ checking ? '…' : t('重新检测', 'Check again') }}</button>
        </div>
      </template>
    </template>
    <template v-else>
      <div class="s-label">{{ status.python_path ? t('依赖没装全?在这里重建环境', 'Dependencies incomplete? Rebuild the environment here') : t('代码在了,还没有 Python 环境', 'The code is here, but there is no Python environment yet') }}</div>
      <div class="s-tip">{{ t('会在仓库里运行建环境脚本(scripts/setup-env),装好的部分不会重下。', "Runs the project's setup script (scripts/setup-env) in the repository; what's already installed isn't downloaded again.") }}</div>
    </template>

    <div v-if="(needed || busy) && status?.llm_optional" class="s-row" style="margin-top:4px">
      <label class="toggle"><input type="checkbox" v-model="withLlm" :disabled="busy" /><span class="slider"></span></label>
      <span class="s-tip" style="margin:0">{{ t('同时装上 LLM 后处理要用的 llama.cpp(预编译包,约几十 MB;模型要等打开后处理开关时才下载)', 'Also install llama.cpp for LLM post-processing (prebuilt, a few dozen MB; the model is only downloaded when you turn post-processing on)') }}</span>
    </div>

    <div v-if="needed || busy" class="s-row" style="margin-top:6px">
      <button class="s-btn ls-primary" @click="run" :disabled="!canRun">
        {{ busy ? stageText : (status?.repo_path ? (status.python_path ? t('重建环境', 'Rebuild environment') : t('一键建环境', 'Set up environment')) : t('下载并安装', 'Download and install')) }}
      </button>
      <span v-if="status?.running && !busy" class="s-tip" style="margin:0">{{ t('有一次安装或更新正在进行…', 'An install or update is in progress…') }}</span>
    </div>
    <div v-if="busy && line" class="s-tip svc-line" :title="line">{{ line }}</div>
    <div v-if="busy" class="s-tip">{{ t('可以先去做别的;完整输出在「日志」里。', 'You can do something else meanwhile; the full output is under Logs.') }}</div>
    <div v-if="result" :class="['s-tip', 'svc-result', result.ok ? 'svc-ok' : 's-err']">{{ result.msg }}</div>
    <div v-if="result && !result.ok" class="s-row" style="margin-top:4px">
      <button class="s-btn" @click="copy(result.msg)">{{ copied ? t('已复制', 'Copied') : t('复制说明', 'Copy details') }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { t } from "./i18n";

/** 与 src-tauri/src/local_setup.rs 的 SetupStatus / Outcome 对应。 */
interface SetupStatus {
  repo_path: string | null;
  python_path: string | null;
  target_dir: string;
  target_state: "missing" | "empty" | "repo" | "occupied";
  git: string | null;
  git_hint: { text: string; command: string | null };
  llm_optional: boolean;
  running: boolean;
  repo_url: string;
}
interface Outcome { repo_path: string; python_path: string; message: string }

const props = defineProps<{
  /** 父组件当前的仓库路径:它一变(手填、自动探测)这里的现状就要重查。 */
  repoPath: string;
  /** 环境体检没过。仓库在、解释器也在时,只有它为真才显示「重建环境」。 */
  envBroken: boolean;
}>();
const emit = defineEmits<{ done: [outcome: Outcome] }>();

const status = ref<SetupStatus | null>(null);
const dir = ref("");
const withLlm = ref(true);
const busy = ref(false);
const checking = ref(false);
const stage = ref("");
const line = ref("");
const result = ref<{ ok: boolean; msg: string } | null>(null);
const copied = ref(false);

/** 还有事可做:没有仓库、没有解释器,或者体检说环境有问题。 */
const needed = computed(() => {
  const s = status.value;
  return !!s && (!s.repo_path || !s.python_path || props.envBroken);
});
/** 没事可做时整块不显示;跑着 / 刚跑完(要留着那句结果)时留着。 */
const visible = computed(() => busy.value || !!result.value || needed.value);
const canRun = computed(() => {
  const s = status.value;
  if (!s || busy.value || s.running) return false;
  if (s.repo_path) return true;
  return !!s.git && s.target_state !== "occupied";
});

const STAGE_LABEL: Record<string, () => string> = {
  checking: () => t("检查中…", "Checking…"),
  cloning: () => t("下载代码中…", "Downloading code…"),
  setup: () => t("安装依赖中…", "Installing dependencies…"),
  restarting: () => t("重启服务中…", "Restarting services…"),
};
const stageText = computed(() => STAGE_LABEL[stage.value]?.() ?? t("进行中…", "Working…"));

async function refresh() {
  checking.value = true;
  try { status.value = await invoke<SetupStatus>("get_local_setup_status", { dir: dir.value.trim() || null }); }
  catch (e) { console.error("get_local_setup_status:", e); }
  checking.value = false;
}

async function run() {
  busy.value = true;
  result.value = null;
  stage.value = "checking";
  line.value = "";
  try {
    const outcome = await invoke<Outcome>("run_local_setup", { dir: dir.value.trim() || null, llm: withLlm.value });
    result.value = { ok: true, msg: outcome.message };
    emit("done", outcome);
  } catch (e) {
    result.value = { ok: false, msg: `${e}` };
  }
  busy.value = false;
  await refresh();
}

async function copy(text: string | null) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    copied.value = true;
    setTimeout(() => { copied.value = false; }, 2000);
  } catch (e) { console.error("clipboard:", e); }
}

watch(() => props.repoPath, () => { if (!busy.value) void refresh(); });

let unlisten: UnlistenFn | null = null;
onMounted(async () => {
  await refresh();
  // 进度:阶段切换,或者某一阶段的一行输出(完整输出在客户端日志里)。
  unlisten = await listen<{ stage: string; line: string | null }>("local-setup", e => {
    const p = e.payload;
    if (p.stage === "progress") { if (p.line) line.value = p.line; return; }
    if (p.stage === "done" || p.stage === "failed") return;
    stage.value = p.stage;
    line.value = p.line ?? "";
  });
});
onUnmounted(() => { unlisten?.(); });
</script>

<style scoped>
.ls-box { margin-top: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.ls-primary { background: rgba(96, 165, 250, 0.15); color: var(--blue); border-color: rgba(96, 165, 250, 0.4); }
</style>
