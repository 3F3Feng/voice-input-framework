<template>
  <!-- 首次启动向导(F1)。以前新用户得自己摸到 ⚙ → 服务 → 本地管理 → 填路径 → 启动,
       还要自己发现权限、输出方式和快捷键;这里按顺序带一遍,每一步都能跳过。
       逻辑全在本组件里(直接 invoke),App.vue 只管显示 / 收尾,不往这边灌状态。 -->
  <div class="ob">
    <header class="ob-head">
      <div class="ob-dots" :title="`第 ${stepIndex + 1} 步,共 ${steps.length} 步`">
        <span v-for="(s, i) in steps" :key="s" :class="['ob-dot', { on: i === stepIndex, past: i < stepIndex }]"></span>
      </div>
      <!-- 从设置里重新打开时叫「关闭」:用户是回来改东西的,不是在「跳过」什么。 -->
      <button v-if="step !== 'finish'" class="ob-skip" @click="finish" :disabled="finishing">{{ manual ? '关闭' : '跳过' }}</button>
    </header>

    <div class="ob-body">
      <!-- ① 欢迎 + 选模式 -->
      <template v-if="step === 'welcome'">
        <div class="ob-hero">🎙️</div>
        <h2 class="ob-title">欢迎使用语音输入</h2>
        <p class="ob-lead">按下快捷键说话,文字出现在光标处。先花一两分钟把它配好。</p>
        <div class="ob-q">语音识别在哪儿跑?</div>
        <button :class="['ob-choice', { sel: mode === 'local' }]" @click="chooseMode('local')" :disabled="modeBusy">
          <span class="ob-choice-title">在本机跑模型(推荐)</span>
          <span class="ob-choice-desc">本机有这个项目的仓库和 Python 环境时选它,由本应用负责启动服务。</span>
        </button>
        <button :class="['ob-choice', { sel: mode === 'remote' }]" @click="chooseMode('remote')" :disabled="modeBusy">
          <span class="ob-choice-title">连接已有的服务器</span>
          <span class="ob-choice-desc">服务已经在另一台电脑(或你自己的终端)里跑着,只需要填地址。</span>
        </button>
        <div v-if="modeError" class="s-tip s-err">{{ modeError }}</div>
      </template>

      <!-- ② 本机:路径 → 体检 → 启动 -->
      <template v-else-if="step === 'server' && mode === 'local'">
        <h2 class="ob-title">准备本机服务</h2>
        <div class="ob-label">项目仓库</div>
        <div class="s-row">
          <input class="s-input" v-model="repoPath" placeholder="含 services/stt_server.py 的目录" @change="saveLocal" />
          <button class="s-btn" @click="detectPaths" :disabled="detecting">{{ detecting ? '…' : '自动探测' }}</button>
        </div>
        <div class="ob-label">Python 解释器</div>
        <div class="s-row">
          <input class="s-input" v-model="pythonPath" placeholder="如 仓库/.venv/bin/python" @change="saveLocal" />
        </div>
        <div v-if="pathProblem" class="s-tip srv-problem">⚠ {{ pathProblem }}</div>

        <div class="ob-block">
          <div class="perm-head">
            <span class="s-label">环境检查</span>
            <span v-if="envChecking" class="perm-state warn">检查中…</span>
            <span v-else-if="envReport" :class="['perm-state', envReport.ok ? 'ok' : 'bad']">{{ envReport.ok ? '通过' : '有问题' }}</span>
            <span class="ob-spacer"></span>
            <button class="s-btn" @click="runEnvCheck" :disabled="envChecking || !!pathProblem">
              {{ envReport || envError ? '重新检查' : '检查' }}
            </button>
          </div>
          <div v-if="envChecking" class="s-tip">要试着 import torch 等依赖,第一次可能要十几秒。</div>
          <div v-else-if="envError" class="s-tip s-err">{{ envError }}</div>
          <template v-else-if="envReport">
            <!-- 只列有问题的几项:全列出来 400×500 的窗口放不下,通过的也没什么好看。 -->
            <div v-for="it in envIssues" :key="it.id" class="s-tip">
              <span :class="it.status === 'fail' ? 'ob-bad' : 'srv-problem'">{{ it.status === 'fail' ? '✕' : '!' }} {{ it.label }}</span>:{{ it.detail }}
              <span v-if="it.fix" class="srv-problem"> → {{ it.fix }}</span>
            </div>
            <template v-if="!envReport.ok">
              <div class="s-tip">在终端里运行这条命令建好环境,再点「重新检查」:</div>
              <div class="s-row" style="margin-top:4px">
                <code class="env-cmd">{{ envReport.setup_command }}</code>
                <button class="s-btn" @click="copySetup">{{ copied ? '已复制' : '复制' }}</button>
              </div>
            </template>
            <div v-else-if="!envIssues.length" class="s-tip">Python 环境和依赖都没问题。</div>
          </template>
          <div v-else-if="pathProblem" class="s-tip">先把上面的路径填对。</div>
        </div>

        <div class="ob-block">
          <div class="perm-head">
            <span class="s-label">STT 服务</span>
            <span :class="['perm-state', sttChip.cls]">{{ sttChip.text }}</span>
            <span class="ob-spacer"></span>
            <button v-if="canStart" class="s-btn ob-primary" @click="startService" :disabled="startBusy">
              {{ startBusy ? '启动中…' : '启动服务' }}
            </button>
          </div>
          <!-- detail 里带着 /health.loading 的下载进度(「已下载 300 MB(42 秒)」),照原样显示。 -->
          <div v-if="sttDetail" :class="['s-tip', sttState === 'failed' ? 's-err' : '']">{{ sttDetail }}</div>
          <div v-if="startMsg" :class="['s-tip', startErr ? 's-err' : '']">{{ startMsg }}</div>
          <div v-if="llmNote" class="s-tip">{{ llmNote }}</div>
          <div v-if="sttState === 'starting'" class="s-tip">
            第一次要下载模型(几百 MB 到几 GB),可以先去下一步,模型会在后台接着加载。
          </div>
          <div class="s-row" style="margin-top:6px">
            <label class="toggle"><input type="checkbox" v-model="autoStart" @change="autoStartTouched = true; saveLocal()" /><span class="slider"></span></label>
            <span class="s-label">以后随应用自动启动服务</span>
          </div>
          <!-- huggingface.co 在大陆常连不上:第一次下载模型会一直卡在「正在加载」。
               只在服务启动时生效,所以放在「启动服务」旁边。 -->
          <div class="s-row" style="margin-top:6px">
            <span class="s-tip" style="margin:0;flex:1">模型下载源</span>
            <select class="s-select" style="width:auto" v-model="hfEndpoint" @change="saveLocal">
              <option value="">HuggingFace 官方</option>
              <option value="https://hf-mirror.com">hf-mirror.com(国内镜像)</option>
            </select>
          </div>
        </div>
      </template>

      <!-- ② 远程:地址 → 连接 -->
      <template v-else-if="step === 'server'">
        <h2 class="ob-title">连接服务器</h2>
        <p class="ob-lead">填 STT 服务的地址。可以是主机名,也可以是完整 URL(如 http://192.168.1.9:6544)。</p>
        <div class="s-row">
          <input class="s-input" v-model="remoteHost" placeholder="localhost 或 http://1.2.3.4:6544" @keyup.enter="connectRemote" />
          <input class="s-input s-port" v-model.number="remotePort" type="number" min="1" max="65535" @keyup.enter="connectRemote" />
          <button class="s-btn" @click="connectRemote" :disabled="remoteBusy">{{ remoteBusy ? '…' : '连接' }}</button>
        </div>
        <!-- 服务端设了 VIF_API_TOKEN 才需要(F20);空着 = 不带令牌。和设置页那一格存的是同一个值。 -->
        <div class="s-row" style="margin-top:4px">
          <input class="s-input" v-model="remoteToken" type="password" autocomplete="off"
            placeholder="访问令牌(可选,服务端设了 VIF_API_TOKEN 才需要)" @keyup.enter="connectRemote" />
        </div>
        <div v-if="remoteMsg" :class="['s-tip', remoteOk ? 'ob-good' : 's-err']">{{ remoteMsg }}</div>
        <div v-if="remoteOk && healthLine" class="s-tip">{{ healthLine }}</div>
        <div class="s-tip" style="margin-top:8px">本应用只连接,不管对端的进程;服务要在那边自己启动。</div>
      </template>

      <!-- ③ 权限(仅 macOS) -->
      <template v-else-if="step === 'perm'">
        <h2 class="ob-title">系统权限</h2>
        <p class="ob-lead">macOS 要你逐项点头。每一项都只做下面写的那件事。</p>
        <div v-for="row in permRows" :key="row.key" class="perm-row">
          <div class="perm-info">
            <div class="perm-head">
              <span class="s-label">{{ row.label }}</span>
              <span :class="['perm-state', permClass(row.status)]">{{ permText(row.status) }}</span>
            </div>
            <div class="s-tip" style="margin-top:2px">{{ row.why }}</div>
          </div>
          <div class="perm-actions">
            <button v-if="row.canRequest" class="s-btn" @click="requestPerm(row.key)" :disabled="permBusy === row.key">
              {{ permBusy === row.key ? '…' : '授权' }}
            </button>
            <button v-if="row.status !== 'granted'" class="s-btn" @click="openPermSettings(row.key)">打开设置</button>
          </div>
        </div>
        <div v-if="permMsg" class="s-tip srv-problem">{{ permMsg }}</div>
        <div class="s-tip" style="margin-top:6px">已拒绝的项系统不会再弹窗,要在「系统设置 → 隐私与安全性」里手动勾上;回到这里会自动刷新。</div>
      </template>

      <!-- ④ 输出方式 + 快捷键 + 试一下 -->
      <template v-else-if="step === 'output'">
        <h2 class="ob-title">说完之后</h2>
        <div class="s-row mode-switch">
          <button :class="['s-btn', 'mode-btn', { active: autoInput }]" @click="setAutoInput(true)">自动输入到光标处</button>
          <button :class="['s-btn', 'mode-btn', { active: !autoInput }]" @click="setAutoInput(false)">只显示在窗口里</button>
        </div>
        <div v-if="autoInput" class="s-row" style="margin-top:6px">
          <span class="s-label" style="flex:1">输入方式</span>
          <select class="s-select" style="width:auto" v-model="inputMethod" @change="saveOutput">
            <option value="paste">粘贴(推荐)</option>
            <option value="type">模拟打字</option>
            <option value="copy">只复制到剪贴板</option>
          </select>
        </div>
        <div class="s-tip">{{ outputTip }}</div>
        <div v-if="outputMsg" class="s-tip s-err">{{ outputMsg }}</div>
        <div v-if="needsAccessibility" class="s-tip srv-problem">⚠ 还没授权「辅助功能」,自动输入会失败;可以回上一步授权,或改成「只复制到剪贴板」。</div>

        <div class="ob-block">
          <div class="perm-head">
            <span class="s-label">快捷键</span>
            <span class="ob-key">{{ hotkeyLabel }}</span>
            <span class="s-tip" style="margin:0">{{ toggleMode ? '按一下开始,再按一下结束' : '按住说话,松开结束' }}</span>
          </div>
          <div class="s-tip">以后可以在 ⚙ 设置 → 常规 里换。</div>
        </div>

        <div class="ob-try">
          <div class="ob-try-title">试一下</div>
          <div v-if="tryBlocker" class="s-tip srv-problem">{{ tryBlocker }}</div>
          <div v-else-if="tryState === 'recording'" class="ob-try-status ob-rec">● 录音中… {{ toggleMode ? '说完再按一下' : '说完松开' }}</div>
          <div v-else-if="tryState === 'processing'" class="ob-try-status ob-wait">识别中…</div>
          <div v-else class="ob-try-status">
            {{ toggleMode ? `按一下 ${hotkeyLabel},说一句话,再按一下` : `按住 ${hotkeyLabel} 说一句话` }}
          </div>
          <div v-if="tryState === 'done'" class="ob-try-result">{{ tryText || '(没识别出内容)' }}</div>
          <div v-if="tryState === 'error'" class="s-tip s-err">{{ tryError }}</div>
          <div v-if="tryState === 'done' && autoInput" class="s-tip">
            这次结果留在这里;平时在别的窗口里说完,{{ inputMethod === 'copy' ? '文字会放进剪贴板' : '文字会直接出现在光标处' }}。
          </div>
          <div v-if="hotkeyProblem" class="s-tip srv-problem">⚠ 快捷键现在不可用:{{ hotkeyProblem }}</div>
          <!-- 快捷键没起来(比如刚授权「输入监控」、要重启才生效)时也得能试:
               按住这个按钮录音,走的是和主界面录音按钮同一条路。 -->
          <button class="s-btn ob-hold" @mousedown="holdStart" @mouseup="holdStop" @mouseleave="holdStop"
            :disabled="tryState === 'processing' || (tryState === 'recording' && !holding)">
            {{ holding ? '松开结束' : '或者按住这里说' }}
          </button>
        </div>
      </template>

      <!-- ⑤ 完成 -->
      <template v-else>
        <div class="ob-hero">✅</div>
        <h2 class="ob-title">都好了</h2>
        <div class="ob-summary">
          <div><span class="s-tip">服务</span><span>{{ summaryServer }}</span></div>
          <div><span class="s-tip">说完后</span><span>{{ autoInput ? { paste: '粘贴到光标处', type: '模拟打字输入', copy: '放进剪贴板' }[inputMethod] : '只显示在窗口里' }}</span></div>
          <div><span class="s-tip">快捷键</span><span class="ob-key">{{ hotkeyLabel }}</span></div>
        </div>
        <p class="ob-lead">平时窗口可以关掉,快捷键在后台一直能用;托盘里能重新打开它。</p>
      </template>
    </div>

    <!-- 每一步都能退回上一步,包括最后的「完成」;第一步也给「下一步」,
         重新打开向导时不必为了往下走而重选一遍模式(沿用当前模式)。 -->
    <footer class="ob-foot">
      <button v-if="stepIndex > 0" class="s-btn" @click="go(-1)">上一步</button>
      <span class="ob-spacer"></span>
      <span v-if="step === 'server' && !serverReady" class="s-tip ob-foot-tip">没就绪也可以先往下走</span>
      <button v-if="step === 'finish'" class="s-btn ob-primary" @click="finish" :disabled="finishing">开始使用</button>
      <button v-else class="s-btn ob-primary" @click="go(1)">下一步</button>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// 主界面算好的、给人看的快捷键(⌃⌥ / Ctrl+Alt)。只要这一个 prop:格式化规则
// 留在 App.vue 一处,别在这里再抄一份。
/** `manual`:用户从设置 / 托盘里主动打开的(不是首次启动自动弹的)。 */
defineProps<{ hotkeyLabel: string; manual?: boolean }>();
const emit = defineEmits<{ (e: "done"): void }>();

// ── 类型:只声明向导用到的字段;配置读出来整份再原样写回,别的字段不会丢 ──
type ServerMode = "local" | "remote";
type InputMethod = "paste" | "type" | "copy";
interface LocalCfg {
  repo_path: string | null; python_path: string | null; stt_port: number; llm_port: number;
  stt_model: string | null; llm_model: string | null; auto_start: boolean; hf_endpoint?: string | null;
}
interface Cfg {
  server: { host: string; port: number; mode: ServerMode; local: LocalCfg; token?: string | null };
  hotkey: { key: string; toggle?: boolean };
  ui: { auto_input?: boolean; input_method?: InputMethod; output_choice_made?: boolean; onboarding_done?: boolean };
  [k: string]: unknown;
}
type ServerState = "not_configured" | "stopped" | "starting" | "running" | "failed";
interface ServerStatus { state: ServerState; detail: string | null; current_model: string | null }
interface ServerReport { stt: ServerStatus; llm: ServerStatus; local_paths: { problem: string | null } }
type EnvStatus = "ok" | "warn" | "fail";
interface EnvItem { id: string; label: string; status: EnvStatus; detail: string; fix: string | null }
interface EnvReport { items: EnvItem[]; ok: boolean; setup_command: string }
type HealthState = "unknown" | "unreachable" | "loading" | "error" | "ready";
interface SttHealth { state: HealthState; current_model: string | null; error: string | null }
type PermStatus = "granted" | "denied" | "not_determined" | "restricted";
type PermKey = "microphone" | "input_monitoring" | "accessibility";
interface PermReport { is_macos: boolean; microphone: PermStatus; input_monitoring: PermStatus; accessibility: PermStatus }

async function getConfig(): Promise<Cfg> { return await invoke<Cfg>("get_config"); }
/** 读—改—写整份配置。别的设置页也是这么存的(update_config 是整份替换)。 */
async function patchConfig(fn: (c: Cfg) => void) {
  const c = await getConfig();
  fn(c);
  await invoke("update_config", { newConfig: c });
}

// ── 步骤 ──
type Step = "welcome" | "server" | "perm" | "output" | "finish";
const isMac = ref(navigator.userAgent.includes("Mac"));
// 权限这一步只有 macOS 有:别的平台没有逐项授权这回事。
const steps = computed<Step[]>(() =>
  ["welcome", "server", ...(isMac.value ? ["perm" as const] : []), "output", "finish"]);
const step = ref<Step>("welcome");
const stepIndex = computed(() => steps.value.indexOf(step.value));
function go(delta: number) {
  const next = steps.value[stepIndex.value + delta];
  if (next) step.value = next;
}

// ── ① 模式 ──
const mode = ref<ServerMode>("local");
const modeBusy = ref(false);
const modeError = ref("");
async function chooseMode(m: ServerMode) {
  modeBusy.value = true;
  modeError.value = "";
  try {
    // 和设置页的「本地管理 / 远程连接」同一个命令:存配置并立刻把客户端指过去,
    // 心跳也就跟着去看新地址。
    await invoke("set_server_mode", { mode: m });
    mode.value = m;
    step.value = "server";
  } catch (e) { modeError.value = `切换失败:${e}`; }
  modeBusy.value = false;
}

// ── ② 本机 ──
const repoPath = ref("");
const pythonPath = ref("");
/** 显示的是配置里的值;点「启动服务」时顺手勾上(除非用户在这里亲手取消过):
 *  走到这一步的人就是想让它在本机跑,下次打开还得再点一次「启动」只会让人以为坏了。
 *  不在一进来就勾:环境还没建好时勾上,每次启动都会去拉一个注定失败的进程。 */
const autoStart = ref(false);
const autoStartTouched = ref(false);
const hfEndpoint = ref("");
const pathProblem = ref<string | null>(null);
const detecting = ref(false);
const report = ref<ServerReport | null>(null);
const envReport = ref<EnvReport | null>(null);
const envChecking = ref(false);
const envError = ref("");
const copied = ref(false);
const startBusy = ref(false);
const startMsg = ref("");
const startErr = ref(false);
/** 这次是不是向导自己拉起的 STT。是的话 LLM 也得由这里补起(见 maybeStartLlm)。 */
let startedByWizard = false;
let llmHandled = false;
const llmNote = ref("");

/** 进入本机这一步:读配置里的路径;没有就先自动探测一次,再体检。 */
async function enterLocal() {
  try {
    const c = await getConfig();
    repoPath.value = c.server.local.repo_path ?? "";
    pythonPath.value = c.server.local.python_path ?? "";
    hfEndpoint.value = c.server.local.hf_endpoint ?? "";
    if (!autoStartTouched.value) autoStart.value = c.server.local.auto_start;
  } catch (e) { console.error("get_config:", e); }
  if (!repoPath.value) await detectPaths();
  else await saveLocal();
}

async function detectPaths() {
  detecting.value = true;
  try {
    const d = await invoke<{ repo_path: string | null; python_path: string | null; problem: string | null }>("detect_local_server");
    if (d.repo_path) {
      repoPath.value = d.repo_path;
      pythonPath.value = d.python_path ?? "";
    }
    await saveLocal();
    // 路径本身没问题时 pathProblem 是空的;探测的说明(比如「没有 .venv」)更具体,用它。
    if (d.problem && (!d.repo_path || pathProblem.value)) pathProblem.value = d.problem;
  } catch (e) { pathProblem.value = `探测失败:${e}`; }
  detecting.value = false;
}

/**
 * 保存路径 / 随应用启动 / 下载源。`set_local_server_config` 是整段替换 `server.local`,
 * 所以先读回当前那段,只改这几项(端口、固定的模型名原样留着)。
 */
async function saveLocal() {
  try {
    const cur = (await getConfig()).server.local;
    const local: LocalCfg = {
      ...cur,
      repo_path: repoPath.value.trim() || null,
      python_path: pythonPath.value.trim() || null,
      auto_start: autoStart.value,
      hf_endpoint: hfEndpoint.value || null,
    };
    const changedPaths = local.repo_path !== cur.repo_path || local.python_path !== cur.python_path;
    const r = await invoke<{ problem: string | null }>("set_local_server_config", { local });
    pathProblem.value = r.problem;
    // 路径一改,旧的体检结论说的就是另一套环境了。
    if (changedPaths) { envReport.value = null; envError.value = ""; }
    await refreshReport();
    if (!r.problem && !envReport.value && !envChecking.value) void runEnvCheck();
  } catch (e) { pathProblem.value = `保存失败:${e}`; }
}

async function runEnvCheck() {
  envChecking.value = true;
  envError.value = "";
  try { envReport.value = await invoke<EnvReport>("check_environment"); }
  catch (e) { envReport.value = null; envError.value = `检查失败:${e}`; }
  envChecking.value = false;
}
const envIssues = computed(() => envReport.value?.items.filter(i => i.status !== "ok") ?? []);
async function copySetup() {
  if (!envReport.value) return;
  try {
    await navigator.clipboard.writeText(envReport.value.setup_command);
    copied.value = true;
    setTimeout(() => { copied.value = false; }, 2000);
  } catch (e) { envError.value = `复制失败:${e}`; }
}

async function refreshReport() {
  try { report.value = await invoke<ServerReport>("get_server_report"); }
  catch (e) { console.error("get_server_report:", e); }
}
const sttState = computed<ServerState | null>(() => report.value?.stt.state ?? null);
const sttDetail = computed(() => report.value?.stt.detail ?? "");
const sttChip = computed(() => {
  const s = sttState.value;
  if (s === "running") return { cls: "ok", text: "运行中" };
  if (s === "starting") return { cls: "warn", text: "启动中" };
  if (s === "failed") return { cls: "bad", text: "失败" };
  if (s === "not_configured") return { cls: "", text: "未配置" };
  return { cls: "", text: s ? "未运行" : "…" };
});
/** 体检通过才给「启动服务」:环境是坏的,起了也只会在日志里报一串 ImportError。 */
const canStart = computed(() =>
  !!envReport.value?.ok && !pathProblem.value &&
  (sttState.value === "stopped" || sttState.value === "failed" || sttState.value === "not_configured"));

async function startService() {
  startBusy.value = true;
  startMsg.value = "";
  startErr.value = false;
  try {
    if (!autoStartTouched.value) autoStart.value = true;
    await saveLocal();
    startMsg.value = await invoke<string>("start_server", { kind: "stt" });
    startedByWizard = true;
    llmHandled = false;
  } catch (e) { startMsg.value = `启动失败:${e}`; startErr.value = true; }
  startBusy.value = false;
  await refreshReport();
}

/**
 * STT 起来之后,后处理开着(而且本机支持)就把 LLM 也拉起来。
 *
 * 设置页的「启动」只起被点的那一个;「随应用启动」会起两个并对一次账。向导拉起的
 * 是新用户的第一次:只起 STT 的话服务端每句话都去反代一个空端口,第一次试说就
 * 弹「LLM 后处理没做成」。服务不是向导起的(随应用启动 / 用户自己的终端)时不管,
 * 那边自有对账。
 */
async function maybeStartLlm() {
  if (!startedByWizard || llmHandled) return;
  llmHandled = true;
  try {
    const st = await invoke<{ enabled: boolean; supported: boolean }>("get_llm_status");
    const llm = report.value?.llm.state;
    if (!st.enabled || !st.supported || llm === "running" || llm === "starting") return;
    llmNote.value = "LLM 后处理是开着的,正在一起启动它…";
    await invoke<string>("start_server", { kind: "llm" });
    llmNote.value = "LLM 后处理服务已启动(加载模型要几秒)。";
  } catch (e) { llmNote.value = `LLM 后处理服务没起来:${e}(不影响识别,可以在设置里关掉后处理)`; }
}
watch(sttState, s => {
  if (s !== "running") return;
  // 「已启动,正在加载模型...」是点下去那一刻的话,跑起来之后留着就是在说反话。
  if (!startErr.value) startMsg.value = "";
  void maybeStartLlm();
});

// 本机这一步开着时轮询服务状态:「启动中 → 运行中」和下载进度要看得见。
let pollTimer: ReturnType<typeof setInterval> | null = null;
function syncPolling() {
  const want = step.value === "server" && mode.value === "local";
  if (want && !pollTimer) pollTimer = setInterval(refreshReport, 1500);
  else if (!want && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ── ② 远程 ──
const remoteHost = ref("");
const remotePort = ref(6544);
const remoteToken = ref("");
const remoteBusy = ref(false);
const remoteMsg = ref("");
const remoteOk = ref(false);
async function enterRemote() {
  try {
    const c = await getConfig();
    remoteHost.value = c.server.host;
    remotePort.value = c.server.port;
    remoteToken.value = c.server.token ?? "";
  } catch (e) { console.error("get_config:", e); }
  // 已经连着一个好的服务(老用户服务刚好断过一下又回来了)就直接说,不逼他再点一次。
  if (!remoteOk.value && health.value?.state === "ready") {
    remoteOk.value = true;
    remoteMsg.value = "已连上";
  }
}
async function connectRemote() {
  const port = remotePort.value;
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    remoteOk.value = false;
    remoteMsg.value = "端口要在 1–65535 之间";
    return;
  }
  remoteBusy.value = true;
  remoteMsg.value = "";
  try {
    // 令牌照原样传(空串 = 清掉),和设置页的「连接」一致。
    await invoke("set_server_host", { host: remoteHost.value.trim() || "localhost", port, token: remoteToken.value.trim() });
    const url = await invoke<string>("connect_effective_server");
    // 能拉到模型列表才算连上,和主界面的判断一致。
    const models = await invoke<unknown[]>("get_models");
    remoteOk.value = true;
    remoteMsg.value = `已连上 ${url}(${models.length} 个模型可用)`;
  } catch (e) {
    remoteOk.value = false;
    remoteMsg.value = `连不上:${e}`;
  }
  remoteBusy.value = false;
}

// ── 服务健康(心跳)──
const health = ref<SttHealth | null>(null);
const healthLine = computed(() => {
  const h = health.value;
  if (!h) return "";
  if (h.state === "ready") return `模型已就绪${h.current_model ? ` · ${h.current_model}` : ""}`;
  if (h.state === "loading") return "服务连上了,模型还在加载…";
  if (h.state === "error") return `模型加载失败:${h.error ?? "原因未知"}`;
  return "";
});
const serverReady = computed(() =>
  mode.value === "local" ? sttState.value === "running" : remoteOk.value && health.value?.state !== "error");
const summaryServer = computed(() => {
  const h = health.value?.state;
  const where = mode.value === "local" ? "本机" : "远程服务器";
  if (h === "ready") return `${where} · 已就绪`;
  if (h === "loading") return `${where} · 模型加载中`;
  if (h === "error") return `${where} · 模型加载失败`;
  return `${where} · 还没连上`;
});

// ── ③ 权限 ──
const perms = ref<PermReport | null>(null);
const permBusy = ref<PermKey | "">("");
const permMsg = ref("");
const PERM_META: { key: PermKey; label: string; why: string }[] = [
  { key: "microphone", label: "麦克风", why: "录下你说的话。没有它录到的全是静音。" },
  { key: "input_monitoring", label: "输入监控", why: "在任何应用里都能用快捷键开始 / 结束录音。" },
  { key: "accessibility", label: "辅助功能", why: "把识别结果粘贴或打字到光标处。只复制到剪贴板的话用不着。" },
];
const permRows = computed(() => PERM_META.map(m => {
  const status: PermStatus = perms.value ? perms.value[m.key] : "granted";
  // 与 App.vue 同一条规则:麦克风 / 输入监控只有「未询问」时系统才弹窗;辅助功能
  // 分不出「未询问」,没授权就一直给「授权」(它会把本应用加进系统设置的列表)。
  const canRequest = m.key === "accessibility" ? status !== "granted" : status === "not_determined";
  return { ...m, status, canRequest };
}));
function permText(s: PermStatus) {
  return { granted: "已授权", denied: "已拒绝", not_determined: "未询问", restricted: "受限" }[s] || s;
}
function permClass(s: PermStatus) { return s === "granted" ? "ok" : s === "not_determined" ? "warn" : "bad"; }

async function refreshPerms() {
  try {
    const prevIm = perms.value?.input_monitoring;
    perms.value = await invoke<PermReport>("get_permissions");
    isMac.value = perms.value.is_macos;
    // 「输入监控」刚授权:快捷键监听器是启动时建的,那时没权限就建失败了,得重建一次。
    if (prevIm && prevIm !== "granted" && perms.value.input_monitoring === "granted") await reviveHotkey();
  } catch (e) { console.error("get_permissions:", e); }
}
async function reviveHotkey() {
  try {
    const key = (await getConfig()).hotkey.key;
    await invoke("register_hotkey", { shortcut: key });
    hotkeyProblem.value = "";
    permMsg.value = "";
  } catch (e) {
    hotkeyProblem.value = `${e}`;
    permMsg.value = `输入监控已授权,但快捷键还没生效:${e}`;
  }
}
async function requestPerm(key: PermKey) {
  permBusy.value = key;
  permMsg.value = "";
  try {
    const next = await invoke<PermStatus>("request_permission", { permission: key });
    if (next !== "granted") permMsg.value = "还没授权。系统没弹窗的话,点「打开设置」手动勾上。";
  } catch (e) { permMsg.value = `申请失败:${e}`; }
  permBusy.value = "";
  await refreshPerms();
}
async function openPermSettings(key: PermKey) {
  try { await invoke("open_permission_settings", { permission: key }); }
  catch (e) { permMsg.value = `打开系统设置失败:${e}`; }
}
// 从系统设置勾完回来,窗口会重新拿到焦点:顺手刷新,不用再找「刷新」按钮。
function onFocus() { if (step.value === "perm" || step.value === "output") void refreshPerms(); }

// ── ④ 输出方式 + 试一下 ──
const autoInput = ref(true);
const inputMethod = ref<InputMethod>("paste");
const toggleMode = ref(false);
const outputTip = computed(() => !autoInput.value
  ? "结果只显示在本应用窗口里,自己点「复制」或「输入」。"
  : inputMethod.value === "paste"
    ? "借剪贴板一次性贴进去,贴完把原来的剪贴板内容还回去。长文本快,不受输入法影响。"
    : inputMethod.value === "type"
      ? "逐字模拟键盘。换行会变成回车——在聊天软件里等于直接发送。"
      : "只放进剪贴板,不碰当前窗口,自己按粘贴。");
const needsAccessibility = computed(() =>
  autoInput.value && inputMethod.value !== "copy" && !!perms.value?.is_macos && perms.value.accessibility !== "granted");
const outputMsg = ref("");

/**
 * 进这一步就把看到的选择存下来(并记下「选过了」)。
 *
 * 新用户默认「自动输入 + 粘贴」:自动输入关着时说完话目标窗口里什么都没有,只会
 * 以为坏了(这正是主界面那条一次性横幅要解决的问题,向导取代了它)。
 * 已经选过的(output_choice_made)照他原来的选择显示。
 */
async function enterOutput() {
  try {
    const c = await getConfig();
    toggleMode.value = c.hotkey.toggle ?? false;
    inputMethod.value = c.ui.input_method ?? "paste";
    autoInput.value = c.ui.output_choice_made ? (c.ui.auto_input ?? false) : true;
  } catch (e) { console.error("get_config:", e); }
  await saveOutput();
  await checkHotkey();
  try { health.value = await invoke<SttHealth>("get_stt_health"); } catch {}
}
async function setAutoInput(on: boolean) { autoInput.value = on; await saveOutput(); }
async function saveOutput() {
  try {
    await patchConfig(c => {
      c.ui.auto_input = autoInput.value;
      c.ui.input_method = inputMethod.value;
      c.ui.output_choice_made = true;
    });
    outputMsg.value = "";
  } catch (e) { outputMsg.value = `保存失败:${e}`; }
}

const hotkeyProblem = ref("");
async function checkHotkey() {
  try { await invoke("get_hotkey_status"); hotkeyProblem.value = ""; }
  catch (e) { hotkeyProblem.value = `${e}`; }
}

type TryState = "idle" | "recording" | "processing" | "done" | "error";
const tryState = ref<TryState>("idle");
const tryText = ref("");
const tryError = ref("");
const holding = ref(false);
/** 现在试不了的原因(服务没连上 / 模型没好),说清楚在等什么。 */
const tryBlocker = computed(() => {
  if (tryState.value === "recording" || tryState.value === "processing") return "";
  const h = health.value?.state;
  if (h === "loading") return "模型还在加载,好了就能试(这里会自动更新)。";
  if (h === "error") return `模型加载失败:${health.value?.error ?? "原因未知"}。回上一步看看,或者在设置里换个模型。`;
  if (h === "unreachable") return "还没连上服务,回上一步把服务启动 / 连上再来试。";
  return "";
});

async function holdStart() {
  if (tryState.value === "recording" || tryState.value === "processing") return;
  holding.value = true;
  tryState.value = "recording";
  tryError.value = "";
  try { await invoke("start_recording"); }
  catch (e) { holding.value = false; tryState.value = "error"; tryError.value = `${e}`; }
}
async function holdStop() {
  if (!holding.value) return;
  holding.value = false;
  tryState.value = "processing";
  // 结果经 transcribe-done / transcribe-error 回来,和快捷键同一条路。
  try { await invoke("stop_recording"); }
  catch (e) { tryState.value = "error"; tryError.value = `${e}`; }
}

// 与 stt.rs 的 NO_SPEECH 逐字一致:没录到声音不算故障,换个说法。
const NO_SPEECH = "没有录到声音";

// ── 生命周期 ──
const unlisteners: UnlistenFn[] = [];
async function on<T>(event: string, fn: (payload: T) => void) {
  unlisteners.push(await listen<T>(event, e => fn(e.payload)));
}

watch(step, async s => {
  syncPolling();
  if (s === "server") { if (mode.value === "local") await enterLocal(); else await enterRemote(); }
  else if (s === "perm") await refreshPerms();
  else if (s === "output") { tryState.value = "idle"; await enterOutput(); }
});

onMounted(async () => {
  try { mode.value = (await getConfig()).server.mode ?? "local"; } catch {}
  await refreshPerms();
  try { health.value = await invoke<SttHealth>("get_stt_health"); } catch {}
  await on<SttHealth>("stt-health", h => { health.value = h; });
  // 试说的反馈只在「试一下」那一步接;主界面自己也在听这些事件,照常处理(进历史等)。
  await on<null>("hotkey-press", () => {
    if (step.value !== "output") return;
    tryState.value = "recording";
    tryError.value = "";
  });
  await on<null>("hotkey-release", () => {
    if (step.value === "output" && tryState.value === "recording" && !holding.value) tryState.value = "processing";
  });
  await on<null>("recording-cancelled", () => {
    if (step.value === "output") { tryState.value = "idle"; holding.value = false; }
  });
  await on<string>("transcribe-done", text => {
    if (step.value !== "output") return;
    tryText.value = (text ?? "").trim();
    tryState.value = "done";
  });
  await on<string>("transcribe-error", msg => {
    if (step.value !== "output") return;
    holding.value = false;
    tryState.value = "error";
    tryError.value = msg === NO_SPEECH ? "没听到声音,靠近麦克风再说一次。" : msg;
  });
  window.addEventListener("focus", onFocus);
});

onUnmounted(() => {
  unlisteners.forEach(u => u());
  window.removeEventListener("focus", onFocus);
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
});

// ── 结束(「开始使用」和每一步的「跳过」都走这里)──
const finishing = ref(false);
/** 跳过也算走过:再弹一次只会烦人,要改的东西设置页里都有。 */
async function finish() {
  finishing.value = true;
  try { await patchConfig(c => { c.ui.onboarding_done = true; }); }
  catch (e) { console.error("onboarding_done 没存上:", e); }
  // 存失败也放行:把人困在向导里比下次启动再弹一次更糟。
  emit("done");
}
</script>

<style scoped>
/* 盖住整个窗口,但在 toast(z-index 100)下面:主界面在试说时弹的提示还看得见。 */
.ob { position: fixed; inset: 0; z-index: 90; background: var(--bg); display: flex; flex-direction: column; }
.ob-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.ob-dots { display: flex; gap: 6px; }
.ob-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); transition: background 0.2s; }
.ob-dot.past { background: rgba(96, 165, 250, 0.45); }
.ob-dot.on { background: var(--blue); }
.ob-skip { background: none; border: none; color: var(--muted); font-size: 0.72rem; cursor: pointer; padding: 2px 4px; }
.ob-skip:hover:not(:disabled) { color: var(--text); }
.ob-body { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; }
.ob-foot { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-top: 1px solid var(--border); flex-shrink: 0; }
.ob-foot-tip { margin: 0; }
.ob-spacer { flex: 1; }
.ob-hero { font-size: 2.2rem; text-align: center; margin: 4px 0 6px; }
.ob-title { font-size: 1rem; font-weight: 600; margin-bottom: 6px; color: var(--text); }
.ob-hero + .ob-title { text-align: center; }
.ob-lead { font-size: 0.74rem; color: var(--muted); line-height: 1.5; margin-bottom: 10px; }
.ob-q { font-size: 0.72rem; color: var(--muted); margin: 6px 0; }
.ob-label { font-size: 0.66rem; color: var(--muted); margin: 8px 0 4px; }
.ob-choice { display: flex; flex-direction: column; gap: 3px; width: 100%; text-align: left; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; cursor: pointer; color: var(--text); font-family: inherit; }
.ob-choice:hover:not(:disabled) { border-color: var(--blue); }
.ob-choice.sel { border-color: rgba(96, 165, 250, 0.6); }
.ob-choice:disabled { opacity: 0.5; cursor: default; }
.ob-choice-title { font-size: 0.82rem; }
.ob-choice-desc { font-size: 0.68rem; color: var(--muted); line-height: 1.4; }
.ob-block { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
.ob-primary { background: rgba(96, 165, 250, 0.15); color: var(--blue); border-color: rgba(96, 165, 250, 0.4); }
.ob-good { color: var(--green); }
.ob-bad { color: var(--red); }
.ob-key { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.74rem; padding: 1px 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--surface); }
.ob-try { margin-top: 12px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; text-align: center; }
.ob-try-title { font-size: 0.66rem; color: var(--muted); margin-bottom: 4px; }
.ob-try-status { font-size: 0.8rem; color: var(--text); margin: 4px 0; }
.ob-rec { color: var(--red); }
.ob-wait { color: var(--yellow); }
.ob-try-result { font-size: 0.86rem; line-height: 1.5; margin: 6px 0; padding: 6px 8px; background: var(--surface); border-radius: 6px; text-align: left; user-select: text; -webkit-user-select: text; word-break: break-word; }
.ob-hold { margin-top: 8px; }
.ob-summary { display: flex; flex-direction: column; gap: 6px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin: 8px 0 12px; font-size: 0.78rem; }
.ob-summary > div { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.ob-summary .s-tip { margin: 0; }
</style>
