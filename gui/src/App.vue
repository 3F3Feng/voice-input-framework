<template>
  <div class="app">
    <!-- Header -->
    <header class="header">
      <div class="header-left">
        <span class="app-icon">🎙️</span>
        <!-- 正在等服务器起来的时候别断言「未连接」：本地模式下服务刚拉起，
             模型要加载十几秒，这段时间说「未连接」看着就像服务坏了。 -->
        <span :class="['conn-dot', connView.cls]"></span>
        <span class="conn-text" :title="connView.title">{{ connView.text }}</span>
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
        <nav class="tabs">
          <button v-for="t in visibleTabs" :key="t.id"
            :class="['tab', { active: tab === t.id }]" @click="tab = t.id">{{ t.label }}</button>
        </nav>
        <div class="settings-scroll">
          <!-- 服务 -->
          <template v-if="tab === 'service'">
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
                <input class="s-input s-port" v-model.number="serverPort" type="number" min="1" max="65535"
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
                <input class="s-input s-port" v-model.number="sttPort" type="number" min="1024" max="65535"
                  title="STT 端口" @change="saveLocal" />
                <input class="s-input s-port" v-model.number="llmPort" type="number" min="1024" max="65535"
                  title="LLM 端口" @change="saveLocal" />
                <label class="toggle"><input type="checkbox" v-model="localAutoStart" @change="saveLocal" /><span class="slider"></span></label>
                <span class="s-label">随应用启动</span>
              </div>

              <div v-if="pathProblem" class="s-tip srv-problem">⚠ {{ pathProblem }}</div>

              <!-- 子进程输出搬到「日志」标签页去了：两个日志框并排摆在设置里，
                   谁也分不清哪个是客户端自己的、哪个是 Python 服务打出来的。 -->
              <div class="s-row" style="margin-top:6px">
                <span class="s-tip" style="margin:0;flex:1">服务起不来时，输出在「日志」里按 STT / LLM 分开看。</span>
                <button class="s-btn" @click="openLog('stt')">查看日志</button>
              </div>
              <details class="s-help">
                <summary>进程归属是怎么判断的？</summary>
                <div class="s-tip">端口上已经有服务就直接连接，不会重复启动。你自己在终端里跑的服务，只要工作目录就是上面这个仓库，会标成「外部（本项目）」，照样可以从这里停止和重启；认不出来源的进程标成「外部（未识别）」，本应用只连接、绝不停它。</div>
              </details>
            </template>
          </div>

          <!-- Models -->
          <div class="s-section">
            <div class="s-title">STT 模型</div>
            <select class="s-select" v-model="sttModel" @change="switchStt">
              <!-- 以前只显示内部名(qwen_asr_mlx_native_small),本机跑不了的模型也照样能选。 -->
              <option v-for="m in sortedSttModels" :key="m.name" :value="m.name"
                :disabled="!m.available && !m.is_loaded" :title="m.name">
                {{ sttModelLabel(m) }}
              </option>
            </select>
            <div v-if="sttLoading" class="s-loading">{{ sttSwitchNote || '切换中...' }}</div>
          </div>

          <div class="s-section">
            <div class="s-title">LLM 后处理</div>
            <div class="s-row">
              <label class="toggle"><input type="checkbox" v-model="llmEnabled" @change="toggleLlm" :disabled="llmToggling || !llmSupported" /><span class="slider"></span></label>
              <span class="s-label" :class="{ 'llm-busy': llmToggling }">{{ llmSupported ? llmToggleText : '不可用' }}</span>
            </div>
            <!-- 不支持的平台(目前只有 Apple Silicon 能跑 mlx-lm)把开关置灰并说明原因。
                 以前照样能拨,拨了要等满 30 秒才说「还在加载模型」(F17)。 -->
            <div v-if="!llmSupported" class="s-tip" style="margin-top:4px">
              {{ llmUnsupportedReason || '这台机器不支持 LLM 后处理' }}
            </div>
            <!-- 本地管理模式下这个开关不只是个标志位:LLM 服务跟着它起停。
                 加载模型要几秒,开关这几秒是锁着的——得让用户知道那不是卡死。 -->
            <div v-else-if="serverMode === 'local'" class="s-tip" style="margin-top:4px">
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

          <!-- LLM Prompt -->
          <div v-if="llmEnabled" class="s-section">
            <div class="s-title">提示词</div>
            <textarea class="s-textarea" v-model="promptText" rows="3" placeholder="LLM 后处理提示词..." />
            <div class="s-row" style="margin-top:4px">
              <button class="s-btn" @click="loadPrompt" :disabled="promptLoading">重新读取</button>
              <button class="s-btn" @click="savePrompt" :disabled="promptLoading || !promptLoaded">保存</button>
              <button class="s-btn" @click="resetPrompt" :disabled="promptLoading">恢复默认</button>
              <span v-if="promptStatus" class="s-tip">{{ promptStatus }}</span>
            </div>
          </div>
          </template>

          <!-- 常规 -->
          <template v-else-if="tab === 'general'">
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

          <!-- 识别语言:配置里一直有 audio.language,每次转写也会发给服务端,
               但界面上没有入口,只能手改 config.json。 -->
          <div class="s-section">
            <div class="s-title">识别语言</div>
            <select class="s-select" v-model="language" @change="onLanguageChange">
              <option v-for="o in languageOptions" :key="o.code" :value="o.code">{{ o.label }}</option>
            </select>
            <div class="s-tip">
              明确只说一种语言时指定它,能少一些误判;中英混说选「自动」。
              粤语需要 Qwen3-ASR 或 Whisper large-v3 系列,较小的 Whisper 模型会按中文识别。
            </div>
          </div>

          <!-- Hotkey -->
          <div class="s-section">
            <div class="s-title">快捷键</div>
            <div class="s-row">
              <!-- 显示成人能读的形式(macOS 上 ⌃⌥⇧,其它平台 Ctrl+Alt),配置里存的串不变;
                   悬停能看到原串,排查问题时对得上 config.json。 -->
              <input class="s-input hotkey-field" :value="hotkeyFieldText" readonly
                :placeholder="hotkeyRecording ? '请按下快捷键…' : formatHotkey(defaultHotkey)"
                :title="hotkeyStr" :class="{ recording: hotkeyRecording }" @click="startHotkeyRecording" />
              <button class="s-btn" @click="startHotkeyRecording">{{ hotkeyRecording ? '取消' : '录制' }}</button>
              <button class="s-btn" @click="applyHotkey" :disabled="!hotkeyChanged">应用</button>
            </div>
            <div v-if="hotkeyRecording" class="s-tip">
              请按下快捷键组合…支持 {{ IS_MAC ? '⌃ ⌥ ⇧' : 'Ctrl / Alt / Shift' }} 加字母、空格、回车、Tab、Esc、F1–F12 等。
            </div>
            <div v-if="hotkeyMsg" :class="['s-tip', hotkeyMsgErr ? 's-err' : 'srv-problem']">{{ hotkeyMsg }}</div>
            <div class="s-row" style="margin-top:6px">
              <label class="toggle"><input type="checkbox" v-model="distinguishSides" @change="toggleDistinguishSides" /><span class="slider"></span></label>
              <span class="s-label">区分左右修饰键</span>
            </div>
            <div class="s-tip">
              关掉之后,录下来的 <code>left_ctrl</code> 左右两个 Ctrl 都能触发。
              不写左右的写法(如 <code>ctrl+alt</code>)本来就两边都认,不受这个开关影响。
            </div>
            <div class="s-row" style="margin-top:6px">
              <span class="s-label" style="flex:1">录音方式</span>
              <select class="s-select" style="width:auto" v-model="hotkeyToggle" @change="onHotkeyToggleChange">
                <option :value="false">按住说话,松开结束</option>
                <option :value="true">按一下开始,再按一下结束</option>
              </select>
            </div>
            <div class="s-tip">录音中按 Esc 可放弃这一段(不识别)。</div>
          </div>

          <!-- Toggles -->
          <div class="s-section">
            <div class="s-row">
              <label class="toggle"><input type="checkbox" v-model="autoInputEnabled" @change="onAutoInputToggle" /><span class="slider"></span></label>
              <span class="s-label">自动输入到窗口</span>
            </div>
            <div v-if="autoInputEnabled" class="s-row" style="margin-top:6px">
              <span class="s-label" style="flex:1">输入方式</span>
              <select class="s-select" style="width:auto" v-model="inputMethod" @change="onInputMethodChange">
                <option value="paste">粘贴(推荐)</option>
                <option value="type">模拟打字</option>
              </select>
            </div>
            <div v-if="autoInputEnabled" class="s-tip">
              {{ inputMethod === 'paste'
                ? '借用剪贴板一次性贴进去,贴完把原来的剪贴板内容还回去。长文本快,不受输入法影响。'
                : '逐字模拟键盘。文本里的换行会变成回车 —— 在聊天软件里等于直接发送。只在某个输入框不接受粘贴时用。' }}
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
          </template>

          <!-- 权限（仅 macOS 显示，这一页也只在 macOS 下出现在标签栏里） -->
          <template v-else-if="tab === 'perm'">
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
          </template>

          <!-- 日志 -->
          <template v-else-if="tab === 'logs'">
          <!-- 合并后的日志：客户端自己的日志和两个 Python 服务的输出共用一个框，
               用上面的来源按钮切。不做时间线交织——三路日志的时间戳格式各不相同，
               按猜测把它们排在一起只会造出一条看着可信、其实是编的时间线。 -->
          <div class="s-section">
            <div class="s-title" style="display:flex;justify-content:space-between;align-items:center">
              <span>日志</span>
              <span style="color:var(--muted);font-size:0.65rem">{{ activeLog.length }} 行</span>
            </div>
            <div class="s-row mode-switch">
              <button v-for="s in logSources" :key="s.id"
                :class="['s-btn', 'mode-btn', { active: logSource === s.id }]"
                @click="logSource = s.id">{{ s.label }}</button>
            </div>
            <div class="log-box" ref="logBoxRef">
              <div v-for="(entry, i) in activeLog" :key="i" :class="['log-entry', entry.level]">
                {{ entry.msg }}
              </div>
              <div v-if="activeLog.length === 0" class="log-empty">{{ logEmptyText }}</div>
            </div>
            <div class="s-row" style="margin-top:4px">
              <template v-if="logSource === 'client'">
                <button class="s-btn" @click="guiLogs = []">清空</button>
                <!-- 界面上只留最近 500 行，完整的在日志文件里；报问题时一键带上版本和系统。 -->
                <button class="s-btn" @click="openLogDir" :disabled="!guiLogFile">打开日志目录</button>
                <button class="s-btn" @click="copyDiagnostics">复制诊断信息</button>
              </template>
              <button v-else class="s-btn" @click="refreshServers" :disabled="serversLoading">
                {{ serversLoading ? '...' : '刷新' }}
              </button>
            </div>
            <div v-if="activeLogPath" class="s-tip" style="margin-top:4px">文件：{{ activeLogPath }}</div>
          </div>
          </template>

          <!-- 关于 -->
          <template v-else>
          <!-- 关于：版本号只有一个来源(gui/src-tauri/Cargo.toml)，构建 ID 每次构建都换，
               本地反复构建时全靠它认出手里跑的是哪个产物。 -->
          <div class="s-section">
            <div class="s-title">关于</div>
            <div class="about-row"><span class="s-label">版本</span><span class="about-val">v{{ build.version }}</span></div>
            <div class="about-row">
              <span class="s-label">构建</span>
              <span class="about-val mono" :title="build.build_id">{{ buildShort }}</span>
            </div>
            <div class="about-row"><span class="s-label">构建时间</span><span class="about-val mono">{{ build.built_at }}</span></div>
            <div class="s-row" style="margin-top:6px">
              <button class="s-btn" @click="copyBuildId">复制完整构建 ID</button>
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

          <!-- 托盘建不成（Linux 缺 AppIndicator 等）时，托盘里的「退出」不存在，
               关闭按钮又只是最小化——这里是唯一的退出口。 -->
          <div class="s-section">
            <div class="s-title">退出</div>
            <div v-if="!trayOk" class="s-tip srv-problem">⚠ 系统托盘没能创建（原因见「日志」），关闭按钮只会最小化窗口；要退出请点下面的按钮。</div>
            <div v-else class="s-tip">关闭按钮只是收起窗口，快捷键仍在后台工作；托盘菜单里也能退出。</div>
            <div class="s-row" style="margin-top:4px">
              <button class="s-btn" @click="quitApp">退出应用</button>
            </div>
          </div>
          </template>
        </div>
      </div>
    </transition>

    <!-- Main Content -->
    <div class="main" v-show="!showSettings">
      <!-- 缺权限提示(仅 macOS) -->
      <div v-if="missingPermLabels.length" class="perm-banner" @click="showSettings = true">
        ⚠️ {{ missingPermLabels.join('、') }}未授权，相关功能不可用 · 点击前往授权
      </div>

      <!-- 首次使用问一次输出方式。「自动输入」默认关，新用户说完话目标窗口里什么都
           没出现，只会以为坏了；可默认打开又会在没授权辅助功能时直接报错。所以问。 -->
      <div v-if="showOutputChoice" class="choice-banner">
        <div class="choice-q">说完的文字要自动输入到当前光标处吗？</div>
        <div class="choice-actions">
          <button class="s-btn" @click="chooseOutput(true)">自动输入</button>
          <button class="s-btn" @click="chooseOutput(false)">只显示在这里</button>
        </div>
        <div class="s-tip">之后随时可以在 ⚙ → 常规 →「自动输入到窗口」里改。</div>
      </div>

      <!-- Record Button -->
      <div class="record-area">
        <button
          @mousedown="startRecord"
          @mouseup="stopRecord"
          @mouseleave="stopRecord"
          :class="['record-btn', { active: recording, processing: loading }]"
          :disabled="!canRecord || loading"
        >
          <div class="record-ring"></div>
          <span class="record-icon">{{ recording ? '⏹' : '🎤' }}</span>
        </button>
        <div class="record-status">
          <span v-if="recording" class="status-rec">录音中 {{ timerText }}</span>
          <span v-else-if="loading" class="status-proc">{{ llmProcessing ? 'LLM 处理中' : '识别中' }} {{ processingTimerText }}</span>
          <!-- 模型没就绪时录音按钮是灰的,得说清楚在等什么(R11) -->
          <span v-else-if="healthState === 'loading'" class="status-proc">模型加载中，稍等再说…</span>
          <span v-else-if="healthState === 'error'" class="status-off" :title="sttHealth?.error ?? ''">模型加载失败，请在设置里换一个模型或重启服务</span>
          <span v-else-if="canRecord" class="status-ready">{{ hotkeyToggle ? '按一下开始' : '按住说话' }} · {{ displayHotkey }}</span>
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
      <span v-if="build.version" class="footer-text" :title="`build ${build.build_id} · ${build.built_at}`">v{{ build.version }} · {{ buildShort }}</span>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// ── Types ──
interface ModelInfo {
  name: string;
  is_loaded: boolean;
  // STT 模型才有下面这些(服务端 services/model_catalog.py);老服务端没有时 Rust 给默认值。
  description?: string;
  memory_gb?: number | null;
  available?: boolean;
  unavailable_reason?: string | null;
  downloaded?: boolean | null;
  recommended?: boolean;
}
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
  hotkey: { key: string; distinguish_left_right: boolean; toggle?: boolean };
  // use_floating_indicator / use_tray / opacity 还在 config.json 里（降级兼容，见 config.rs），
  // 但没有任何地方读，这里不再声明；整份对象读出来再原样写回，它们照样保留。
  // output_choice_made 是后加的，老配置里没有。
  ui: { start_minimized: boolean; auto_input?: boolean; output_choice_made?: boolean; input_method?: InputMethod };
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
/** Rust 后台心跳看到的 STT 服务状态(`stt-health` 事件)。`null` = 还没拿到。 */
type SttHealthState = "unknown" | "unreachable" | "loading" | "error" | "ready";
interface SttHealth {
  state: SttHealthState;
  reachable: boolean;
  status: string | null;
  current_model: string | null;
  error: string | null;
  url: string;
}
const sttHealth = ref<SttHealth | null>(null);
const loading = ref(false);
const result = ref("");
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
/** 提示词框里的内容是不是从服务端读来的。没读到就不许保存:空框直接保存会
 *  把服务端那份覆盖掉(以前打开设置时框是空的,要手点「加载」)。 */
const promptLoaded = ref(false);
/** 切换 STT 模型时的进度说明(服务端是后台加载,可能要下载几分钟)。 */
const sttSwitchNote = ref("");

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
/** 服务端能不能做 LLM 后处理(`GET /llm/enabled` 的 supported / reason,F17)。 */
interface LlmStatus { enabled: boolean; supported: boolean; reason: string | null; }
const llmSupported = ref(true);
const llmUnsupportedReason = ref("");
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
const distinguishSides = ref(true);
const hotkeyChanged = ref(false);
const hotkeyMsg = ref("");
/** hotkeyMsg 是错误(红字)还是提醒(黄字)。 */
const hotkeyMsgErr = ref(false);
/** 已经注册生效并存进配置的快捷键。录制出来还没「应用」的在 hotkeyStr 里。 */
const savedHotkey = ref("");
const defaultHotkey = "left_ctrl+left_alt";
/** 录制中已经按住的修饰键,实时显示在输入框里,用户知道自己按到了哪一步。 */
const hotkeyPreview = ref("");
const language = ref("auto");
/** 上一次成功存进配置的识别语言;存失败时下拉框退回它。 */
let savedLanguage = "auto";

const IS_MAC = navigator.userAgent.includes("Mac");
const MOD_LABEL: Record<string, [mac: string, other: string]> = {
  ctrl: ["⌃", "Ctrl"], control: ["⌃", "Ctrl"], alt: ["⌥", "Alt"], shift: ["⇧", "Shift"],
};
const KEY_LABEL: Record<string, [mac: string, other: string]> = {
  space: ["Space", "Space"], enter: ["↩", "Enter"], return: ["↩", "Enter"], tab: ["⇥", "Tab"],
  esc: ["Esc", "Esc"], escape: ["Esc", "Esc"], backspace: ["⌫", "Backspace"],
  delete: ["⌦", "Delete"], del: ["⌦", "Delete"], capslock: ["⇪", "CapsLock"], caps: ["⇪", "CapsLock"],
};

/**
 * 把存储用的 `left_ctrl+left_alt` 变成给人看的样子:macOS 上 `⌃⌥`,其它平台
 * `Ctrl+Alt`。只在开了「区分左右」时才标出左 / 右 —— 关掉时两边都能触发,
 * 标出来反而误导。配置里存的串不变。
 */
function formatHotkey(s: string): string {
  let sided = false;
  const labels = s.split("+").map(raw => {
    const t = raw.trim().toLowerCase();
    if (!t) return "?";  // 空段(旧 bug 录出来的 `left_ctrl+ `)要看得见,别显示成完整的样子
    const m = /^(left|right)_(\w+)$/.exec(t) || /^([lr])(ctrl|alt|shift)$/.exec(t);
    const base = m ? m[2] : t;
    const mod = MOD_LABEL[base];
    if (mod) {
      const side = m && distinguishSides.value ? (m[1].startsWith("l") ? "左" : "右") : "";
      if (side) sided = true;
      return side + (IS_MAC ? mod[0] : mod[1]);
    }
    const key = KEY_LABEL[base];
    if (key) return IS_MAC ? key[0] : key[1];
    return /^([a-z]|f\d+)$/.test(base) ? base.toUpperCase() : raw.trim();
  });
  // macOS 惯例是符号连写(⌃⌥Space);带了「左 / 右」时连写就读不清了,用空格隔开。
  return labels.join(IS_MAC ? (sided ? " " : "") : "+");
}
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
let installStallTimer: ReturnType<typeof setInterval> | null = null;
const updateStatus = ref("");
const updateStatusType = ref<"info" | "ok">("info");
const updateChecking = ref(false);
const updateInstalling = ref(false);

// Logs
/** `seq` 只有 Rust 侧的日志才有（前端 toast 没有），用来和补拉的缓冲去重。 */
interface GuiLogEntry { msg: string; level: string; seq?: number }
/** Rust `log.rs` 的 `LogLine`。 */
interface RustLogLine { seq: number; level: string; text: string }
const GUI_LOG_CAP = 500;
const guiLogs = ref<GuiLogEntry[]>([]);
/** 客户端日志文件路径。日志目录建不出来时为 null。 */
const guiLogFile = ref<string | null>(null);
/** 托盘建成了没有。建不成时关闭按钮只是最小化，「关于」里要给出退出口。 */
const trayOk = ref(true);
/** 用户选过输出方式没有。读到配置之前当作选过，免得横幅在启动时闪一下。 */
const outputChoiceMade = ref(true);

// ── 设置面板的标签页 ──
// 以前是十个 s-section 在一个 400×500 的窗口里一路往下堆，找一个开关要滚三屏。
// 按「改什么」分页：服务（连哪儿、起停、模型）／常规（日常开关）／权限（macOS）／
// 日志／关于。权限页只在 macOS 下出现，别的平台连标签都不显示。
type SettingsTab = "service" | "general" | "perm" | "logs" | "about";
const tab = ref<SettingsTab>("service");
const visibleTabs = computed(() => [
  { id: "service" as const, label: "服务" },
  { id: "general" as const, label: "常规" },
  ...(perms.value?.is_macos ? [{ id: "perm" as const, label: "权限" }] : []),
  { id: "logs" as const, label: "日志" },
  { id: "about" as const, label: "关于" },
]);

// ── 日志来源 ──
// 客户端自己的日志和两个 Python 服务的输出原本是两个独立的日志框（一个在
// 「服务器」一段里，一个在面板最底下），并排摆着没人分得清哪个是哪个。
// 现在共用一个框，用来源按钮切。远程模式下没有本地子进程，只剩「客户端」。
//
// 不做时间线交织：三路日志的时间戳格式各不相同，按猜测把它们排到一起只会造出
// 一条看着可信、其实是编的时间线。
type LogSource = "client" | "stt" | "llm";
const logSource = ref<LogSource>("client");
const logSources = computed(() => [
  { id: "client" as const, label: "客户端" },
  ...(serverMode.value === "local"
    ? [{ id: "stt" as const, label: "STT" }, { id: "llm" as const, label: "LLM" }]
    : []),
]);
const activeLog = computed<GuiLogEntry[]>(() => {
  if (logSource.value === "client") return guiLogs.value;
  const st = serverReport.value?.[logSource.value];
  // 子进程的 stdout 没有分级，全按 info 渲染，不去猜哪行是错误。
  return (st?.recent_logs ?? []).map(msg => ({ msg, level: "info" }));
});
const activeLogPath = computed(() => {
  if (logSource.value === "client") return guiLogFile.value;
  return serverReport.value?.[logSource.value]?.log_path ?? null;
});
const logEmptyText = computed(() => {
  if (logSource.value === "client") return "暂无日志";
  if (serverMode.value !== "local") return "远程模式下没有本地服务日志";
  return "这个服务还没被本应用启动过";
});
/** 从别处跳到日志页并选好来源（「服务器」一段里的「查看日志」用）。 */
function openLog(src: LogSource) { logSource.value = src; tab.value = "logs"; }

// ── 构建信息 ──
// 版本号只有一个来源：gui/src-tauri/Cargo.toml。以前底栏显示的是配置文件的
// schema 版本（"2.0"），tauri.conf.json 和 gui/package.json 里还各有一个，
// 三个数字互相打架。现在全部由后端的 get_build_info 供给。
const build = ref({ version: "", build_id: "", built_at: "" });
const buildShort = computed(() => build.value.build_id.slice(0, 8) || "—");
async function copyBuildId() {
  try { await navigator.clipboard.writeText(build.value.build_id); toast("构建 ID 已复制", "ok"); }
  catch (e) { toast(`复制失败: ${e}`, "err"); }
}
const logBoxRef = ref<HTMLElement | null>(null);
// 日志框以前绑了 ref 却没人用：刷过一屏之后最新的一行就落在可视区外，
// 而看日志的时候要的恰恰是最后几行。切来源时也要回到底部。
watch([activeLog, logSource], async () => {
  await nextTick();
  const el = logBoxRef.value;
  if (el) el.scrollTop = el.scrollHeight;
}, { flush: "post" });

let timerInterval: ReturnType<typeof setInterval> | null = null;
let levelInterval: ReturnType<typeof setInterval> | null = null;
let processingTimerInterval: ReturnType<typeof setInterval> | null = null;
let toastId = 0;

// ── Computed ──
const currentModelName = computed(() => {
  const loaded = sttModels.value.find(m => m.is_loaded);
  return loaded?.name || sttModel.value || "";
});
const healthState = computed<SttHealthState>(() => sttHealth.value?.state ?? "unknown");
/** 能不能开始录音。以前只看 `connected`——能拉到 `/models` 就算连上,模型还在
 *  加载或已经加载失败时按钮照样亮着(R11)。心跳还没结论(unknown)时不拦,
 *  和 Rust 那边开始录音前的闸门一致。 */
const canRecord = computed(() =>
  connected.value && (healthState.value === "ready" || healthState.value === "unknown"));
/** 头部那一行:连接中 / 未连接 / 模型加载中 / 模型加载失败(原因)/ 已就绪。 */
const connView = computed<{ cls: string; text: string; title: string }>(() => {
  const h = sttHealth.value;
  const st = healthState.value;
  // 服务能答话时,模型的状态比「连接中」更有信息量:本地刚拉起的服务正在加载
  // 模型,说「连接中」看着像连不上。
  if (st === "loading") {
    const m = h?.current_model ? ` · ${h.current_model}` : "";
    return { cls: "wait", text: `模型加载中…${m}`, title: "" };
  }
  if (st === "error") {
    const why = h?.error || "原因未知";
    return { cls: "off", text: `模型加载失败（${why}）`, title: why };
  }
  if (connecting.value) return { cls: "wait", text: "连接中…", title: "" };
  if (st === "unreachable" || !connected.value) return { cls: "off", text: "未连接", title: h?.url ?? "" };
  const model = h?.current_model || currentModelName.value;
  return { cls: "on", text: model ? `已就绪 · ${model}` : "已就绪", title: model };
});

/**
 * 心跳报来的新状态(R12)。以前连接状态只在设置面板开着 + 本地模式时才会更新,
 * 远程模式从不更新:服务挂了头部照样是绿的,要等说完一句话才失败。
 */
function applySttHealth(h: SttHealth) {
  const prev = sttHealth.value?.state ?? "unknown";
  sttHealth.value = h;
  if (h.state === "unreachable") {
    // 之前是连着的才提一句;一直连不上的时候别反复弹。
    if (connected.value && (prev === "ready" || prev === "loading" || prev === "error")) {
      toast("与 STT 服务的连接断开了", "err");
    }
    connected.value = false;
    return;
  }
  if (h.state === "ready" && prev !== "ready") {
    // 服务回来了 / 模型加载完了:没连着就连上;已经连着就刷新模型列表,
    // 让下拉框的 ✓ 和头部的模型名跟上。
    if (!connected.value) ensureConnected(CONNECT_BUDGET_RUNNING_MS);
    else loadModels();
  }
}
// 主界面上「按住说话 · …」显示的是**已生效**的快捷键,不是录了还没应用的那个。
/** 推荐的排最前,本机跑不了的沉底。 */
const sortedSttModels = computed(() => {
  const rank = (m: ModelInfo) => (m.recommended ? 0 : m.available === false ? 2 : 1);
  return [...sttModels.value].sort((a, b) => rank(a) - rank(b));
});
/** 下拉框里的一行:说人话的描述 + 状态。内部名放在悬停提示里。 */
function sttModelLabel(m: ModelInfo): string {
  const tags: string[] = [];
  if (m.is_loaded) tags.push("✓ 使用中");
  if (m.recommended) tags.push("推荐");
  if (m.available === false) tags.push(`不可用:${m.unavailable_reason || "本机不支持"}`);
  else if (m.downloaded === false) tags.push(m.memory_gb ? `需下载 ~${m.memory_gb} GB` : "需下载");
  const name = m.description || m.name;
  return tags.length ? `${name}(${tags.join(" · ")})` : name;
}
const displayHotkey = computed(() => formatHotkey(savedHotkey.value || defaultHotkey));
const hotkeyFieldText = computed(() =>
  hotkeyRecording.value ? hotkeyPreview.value : (hotkeyStr.value ? formatHotkey(hotkeyStr.value) : "")
);
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
const missingPermLabels = computed(() =>
  perms.value?.is_macos
    ? permissionRows.value.filter(r => r.status !== "granted").map(r => r.label)
    : []
);

// ── Helpers ──
function pushGuiLog(entry: GuiLogEntry) {
  guiLogs.value.push(entry);
  if (guiLogs.value.length > GUI_LOG_CAP) guiLogs.value = guiLogs.value.slice(-GUI_LOG_CAP);
}
/** `log: false` 用于 Rust 那边已经记过日志的消息（如 app-warning），免得日志页里一条出现两遍。 */
function toast(msg: string, type = "info", log = true) {
  const id = ++toastId;
  toasts.value.push({ id, msg, type });
  setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id); }, 2500);
  if (!log) return;
  const prefix = type === "err" ? "[ERROR]" : type === "ok" ? "[OK]" : "[INFO]";
  // 和 Rust 侧的日志行同一个样子（HH:MM:SS [LEVEL] ...），两边混在一个框里才读得顺。
  const ts = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  pushGuiLog({ msg: `${ts} ${prefix} ${msg}`, level: type });
}

// ── 客户端日志 ──
// Rust 侧的 log_info! / log_error! 一直在发 `gui-log`，但以前前端从来没听过：
// 「快捷键创建失败（缺输入监控）」「Wayland 下快捷键不工作」「配置解析失败已备份」
// 「自动启动失败」全都到不了日志页。setup() 里打的那些发生在 webview 加载之前，
// 光听事件也收不到，所以挂载时先补拉 Rust 的缓冲。
const toGuiLog = (l: RustLogLine): GuiLogEntry =>
  ({ msg: l.text, level: l.level === "ERROR" ? "err" : l.level === "WARN" ? "warn" : "info", seq: l.seq });
/** 目前见过的最大 Rust 日志序号。用来判断「某个动作之后有没有冒出新的错误」。 */
const lastRustLogSeq = () => guiLogs.value.reduce((m, l) => Math.max(m, l.seq ?? 0), 0);

async function initGuiLogs() {
  try {
    // 先挂监听再拉缓冲：反过来的话，拉完到挂上之间打的日志两边都拿不到。
    // 两边都拿到的那几行按 seq 去重；只在缓冲里的一定比事件里的早，拼在前面。
    await listen<RustLogLine>("gui-log", e => pushGuiLog(toGuiLog(e.payload)));
    const snap = await invoke<{ lines: RustLogLine[]; file: string | null }>("get_gui_logs");
    guiLogFile.value = snap.file;
    const seen = new Set(guiLogs.value.map(l => l.seq));
    const backlog = snap.lines.filter(l => !seen.has(l.seq)).map(toGuiLog);
    guiLogs.value = [...backlog, ...guiLogs.value].slice(-GUI_LOG_CAP);
  } catch (e) { console.error("get_gui_logs failed:", e); }
}
async function openLogDir() {
  try { await invoke("open_log_dir"); }
  catch (e) { toast(`${e}`, "err"); }
}
/** 版本、构建、系统、连接方式和最近的客户端日志，报问题时直接粘过来。 */
async function copyDiagnostics() {
  try {
    const text = await invoke<string>("get_diagnostics");
    await navigator.clipboard.writeText(text);
    toast("诊断信息已复制，可以直接粘贴到问题反馈里", "ok");
  } catch (e) { toast(`复制失败: ${e}`, "err"); }
}
async function quitApp() {
  try { await invoke("quit_app"); } catch (e) { toast(`退出失败: ${e}`, "err"); }
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
/** 按钮录音的上限,和快捷键路径(hotkey.rs 的 MAX_RECORD_SECS)一致。 */
const BUTTON_RECORD_LIMIT_MS = 5 * 60 * 1000;
async function startRecord() {
  if (!canRecord.value || loading.value) return;
  if (recording.value) return;  // state lock: prevent double-trigger
  recording.value = true;       // set state BEFORE await to block bounces
  try {
    loading.value = false;
    result.value = "";
    await invoke("start_recording");
    elapsedMs.value = 0;
    // 快捷键路径的 5 分钟上限在 Rust 那边(hotkey.rs 的 record_limit_step),按钮路径
    // 以前没有任何上限:鼠标一直按着就一直录。和快捷键保持一致:提前 30 秒提醒,
    // 到点停止并照常识别。
    timerInterval = setInterval(() => {
      elapsedMs.value += 100;
      if (elapsedMs.value === BUTTON_RECORD_LIMIT_MS - 30000) toast("还剩 30 秒,满 5 分钟会自动停止并识别", "info");
      if (elapsedMs.value >= BUTTON_RECORD_LIMIT_MS) {
        toast("录音已满 5 分钟,已自动停止并开始识别", "info");
        stopRecord();
      }
    }, 100);
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

// 「输入监控」从未授权变成已授权时，把快捷键监听器重建一次。
//
// 监听器（CGEventTap）只在启动时建一次，那时没权限就直接建失败了；以前用户去系统
// 设置里勾上之后，快捷键照样不工作，界面上也没说要重启。盯的是状态本身而不是
// refreshPermissions 的调用点：「请求授权」按钮会先把新状态直接写进 perms 再刷新，
// 在刷新里比前后值会漏掉这一下。
watch(() => perms.value?.input_monitoring, async (next, prev) => {
  if (!perms.value?.is_macos || !prev || prev === "granted" || next !== "granted") return;
  await reviveHotkey();
});

async function reviveHotkey() {
  const mark = lastRustLogSeq();
  try {
    // 用已保存的那个快捷键：输入框里可能是录了还没点「应用」的新组合。
    const key = (await getConfig()).hotkey.key || defaultHotkey;
    await invoke("register_hotkey", { shortcut: key });
  } catch (e) { toast(`快捷键重新注册失败: ${e}`, "err"); return; }
  // 监听器在后台线程里建，建不成只会打一条 [hotkey] 错误日志，命令本身照样返回成功。
  // 等它一下再看日志里有没有冒出新的错误。
  await sleep(1500);
  const failed = guiLogs.value.some(l => (l.seq ?? 0) > mark && l.level === "err" && l.msg.includes("[hotkey]"));
  if (failed) toast("输入监控已授权，但快捷键监听还是没能建立；请退出并重新打开本应用", "err");
  else toast("输入监控已授权，快捷键已生效", "ok");
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

// 切到远程模式时 STT / LLM 两个来源没了，选中它们只会看到一个永远空的框。
watch(serverMode, m => { if (m !== "local" && logSource.value !== "client") logSource.value = "client"; });

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

/**
 * 端口输入的合法性。返回中文原因,合法时返回 null。
 *
 * 以前 0、70000、空着都能存进去:空的被 `|| 6544` 悄悄换成默认值,0 和越界的
 * 原样交给子进程,服务起不来,日志里只有一句 uvicorn 的英文报错。
 * `min` 是下限:本地服务用 1024 起步(更小的是系统保留端口,普通用户绑不上);
 * 远程服务可能就在 80 / 443 上,只要求是个合法端口。
 */
function portError(value: unknown, label: string, min = 1): string | null {
  if (typeof value !== "number" || !Number.isInteger(value)) return `${label}端口要填一个整数`;
  if (value < min || value > 65535) return `${label}端口要在 ${min}–65535 之间(现在是 ${value})`;
  return null;
}
/** 上一次成功保存的本地端口。输入非法时退回它们,别让界面和配置对不上。 */
let savedLocalPorts = { stt: 6544, llm: 6545 };

/** 保存本地模式的路径 / 端口 / 自启设置，并回显路径是否可用。 */
async function saveLocal() {
  // 端口不对就不存端口(退回上次的值),路径和开关照常保存 —— 这个函数也挂在
  // 路径框和「随应用启动」上,不能因为端口错了把它们也一起拦下。
  const portProblem =
    portError(sttPort.value, "STT ", 1024) ??
    portError(llmPort.value, "LLM ", 1024) ??
    (sttPort.value === llmPort.value ? "STT 和 LLM 不能用同一个端口" : null);
  if (portProblem) {
    toast(`${portProblem},已恢复为原来的端口`, "err");
    sttPort.value = savedLocalPorts.stt;
    llmPort.value = savedLocalPorts.llm;
  }
  // `set_local_server_config` 是整段替换 `cfg.server.local`，不是打补丁。所以凡是
  // 这个界面上没有对应输入框的字段，都得先读回来带上——以前这里把 stt_model /
  // llm_model 硬写成 null，用户手改 config.json 固定的模型，会在下一次改端口、
  // 点自动探测或拨「随应用启动」时被悄悄抹掉（这两个字段是 spawn 时的
  // VIF_STT_MODEL / VIF_LLM_MODEL）。读不回来就退回 null，和改动前一致。
  let keep: Pick<LocalServerConfig, "stt_model" | "llm_model"> = { stt_model: null, llm_model: null };
  try {
    const cur = (await getConfig()).server?.local;
    if (cur) keep = { stt_model: cur.stt_model ?? null, llm_model: cur.llm_model ?? null };
  } catch (e) { console.error("get_config before saveLocal failed:", e); }
  const local: LocalServerConfig = {
    repo_path: repoPath.value.trim() || null,
    python_path: pythonPath.value.trim() || null,
    stt_port: sttPort.value || 6544,
    llm_port: llmPort.value || 6545,
    stt_model: keep.stt_model,
    llm_model: keep.llm_model,
    auto_start: localAutoStart.value,
  };
  try {
    const report = await invoke<LocalPathReport>("set_local_server_config", { local });
    savedLocalPorts = { stt: local.stt_port, llm: local.llm_port };
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
    hotkeyStr.value = cfg.hotkey.key;
    savedHotkey.value = cfg.hotkey.key;
    distinguishSides.value = cfg.hotkey.distinguish_left_right ?? true;
    hotkeyToggle.value = cfg.hotkey.toggle ?? false;
    language.value = cfg.audio.language || "auto";
    savedLanguage = language.value;
    void checkSavedHotkey();
    startMinimized.value = cfg.ui.start_minimized;
    autoInputEnabled.value = cfg.ui.auto_input ?? false;
    outputChoiceMade.value = cfg.ui.output_choice_made ?? false;
    inputMethod.value = cfg.ui.input_method ?? "paste";
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
      savedLocalPorts = { stt: sttPort.value, llm: llmPort.value };
      localAutoStart.value = local.auto_start ?? false;
    }
  } catch {}
}
async function loadAutostart() {
  try { autoStart.value = await invoke<boolean>("get_autostart"); } catch {}
}
function onServerSettingChange() {
  const bad = portError(serverPort.value, "");
  if (bad) { toast(bad, "err"); return; }
  saveConfigPatch(cfg => { cfg.server.host = serverHost.value.trim() || "localhost"; cfg.server.port = serverPort.value; });
}
async function toggleAutoStart() {
  try { await invoke("set_autostart", { enabled: autoStart.value }); } catch { autoStart.value = !autoStart.value; }
}
function toggleStartMinimized() { saveConfigPatch(cfg => { cfg.ui.start_minimized = startMinimized.value; }); }
/**
 * 「区分左右修饰键」。配置里一直有 hotkey.distinguish_left_right，但此前
 * Rust 端从来没读过它，界面上也没有入口——存了个谁也够不着、也不起作用的值。
 *
 * 改完要重新注册监听器：解析成哪些候选键是在 parse_hotkey 那一刻定下来的，
 * 光存配置不会让正在跑的监听器改主意。
 */
async function toggleDistinguishSides() {
  const ok = await saveConfigPatch(cfg => { cfg.hotkey.distinguish_left_right = distinguishSides.value; });
  if (!ok) { distinguishSides.value = !distinguishSides.value; return; }
  try {
    // 注册已生效的那个。以前用的是输入框里的 hotkeyStr:录了新快捷键还没点
    // 「应用」时拨这个开关,新快捷键就被悄悄注册上了,配置里却还是旧的。
    await invoke("register_hotkey", { shortcut: savedHotkey.value || defaultHotkey });
    toast(distinguishSides.value ? "已改为区分左右" : "已改为左右通用", "ok");
  } catch (e) { toast(`快捷键重新注册失败: ${e}`, "err"); }
}
type InputMethod = "paste" | "type";
const inputMethod = ref<InputMethod>("paste");
async function onInputMethodChange() {
  const ok = await saveConfigPatch(cfg => { cfg.ui.input_method = inputMethod.value; });
  if (ok) toast(inputMethod.value === "paste" ? "改为粘贴方式输入" : "改为模拟打字输入", "ok");
}
function onAutoInputToggle() {
  // 在设置里拨过这个开关，就等于回答了横幅那个问题，不必再问。
  outputChoiceMade.value = true;
  saveConfigPatch(cfg => { cfg.ui.auto_input = autoInputEnabled.value; cfg.ui.output_choice_made = true; });
}

// ── 输出方式（首次使用问一次） ──
// 已经开着自动输入的老用户显然选过了，不给他们看。
const showOutputChoice = computed(() => !outputChoiceMade.value && !autoInputEnabled.value);
async function chooseOutput(auto: boolean) {
  const ok = await saveConfigPatch(cfg => { cfg.ui.auto_input = auto; cfg.ui.output_choice_made = true; });
  if (!ok) return;
  autoInputEnabled.value = auto;
  outputChoiceMade.value = true;
  if (auto && perms.value?.is_macos && perms.value.accessibility !== "granted") {
    toast("自动输入需要「辅助功能」权限：第一次输入时会弹出授权，也可以先到 ⚙ → 权限 里授权", "info");
  } else {
    toast(auto ? "说完会自动输入到光标处" : "结果只显示在这里，可以点「复制」或「输入」", "ok");
  }
}

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
  const bad = portError(serverPort.value, "");
  if (bad) { toast(bad, "err"); return; }
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
  try {
    const st = await invoke<LlmStatus>("get_llm_status");
    llmEnabled.value = st.enabled;
    llmSupported.value = st.supported;
    llmUnsupportedReason.value = st.reason ?? "";
  } catch {}
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

/**
 * 切换 STT 模型。
 *
 * 服务端的 `/models/select` 只是「开始切换」:立即返回,后台加载(首次用某个模型
 * 还要下载,可能几分钟)。以前这里一返回就弹「模型已切换」,头部也立刻换成新名字,
 * 而那一刻模型根本还没加载完;加载失败时同样弹「已切换」。现在轮询到真正加载
 * 完成才说成功,失败时把服务端的原因原样说出来(服务端会自动回退到原来的模型)。
 */
async function switchStt() {
  const name = sttModel.value;
  if (!name) return;
  sttLoading.value = true;
  sttSwitchNote.value = "";
  const started = Date.now();
  try {
    await invoke<string>("switch_model", { name });
    for (;;) {
      const st = await invoke<{
        is_loaded: boolean; is_loading: boolean; is_current: boolean; error: string | null;
        loading?: { downloaded_bytes: number } | null;
      }>("get_model_status", { name });
      if (st.is_loaded) { toast(`已切换到 ${name}`, "ok"); break; }
      if (st.error) throw `${st.error}(已回到原来的模型)`;
      if (!st.is_current && !st.is_loading) throw "切换被中断(可能又选了别的模型)";
      const secs = Math.round((Date.now() - started) / 1000);
      const mb = Math.round((st.loading?.downloaded_bytes ?? 0) / 1048576);
      sttSwitchNote.value = mb > 0
        ? `正在下载模型… 已下载 ${mb} MB(${secs} 秒)`
        : secs < 10
          ? "正在加载模型…"
          : `正在加载模型… ${secs} 秒(第一次用这个模型需要下载,可能要几分钟)`;
      // 等太久就不在这里干等了,服务端会继续加载,头部状态会跟着变。
      if (Date.now() - started > 15 * 60 * 1000) { toast("模型还在加载,完成后自动生效", "info"); break; }
      await sleep(1500);
    }
  } catch (e) { toast(`切换失败: ${e}`, "err"); }
  sttLoading.value = false;
  sttSwitchNote.value = "";
  // 列表里的 ✓ 和下拉框的选中项都以服务端为准刷新一次(失败时会回到原模型)。
  await loadModels();
}
async function switchLlm() {
  if (!llmModel.value) return;
  llmLoading.value = true;
  try { await invoke<string>("switch_llm_model", { name: llmModel.value }); toast("LLM 已切换", "ok"); }
  catch (e) {
    // 下载大模型常常超过转发的超时,服务端会说「还在加载,完成后自动生效」——
    // 那不是失败,别用红字吓人(R16)。
    const msg = `${e}`;
    if (msg.includes("还在加载")) toast(msg, "info");
    else toast(`LLM 切换失败: ${msg}`, "err");
  }
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

/**
 * 物理键(`e.code`)→ 后端 `parse_key`(hotkey.rs)认的名字。**只列后端真认的键**,
 * 这张表和 hotkey.rs 的 `every_token_the_recorder_emits_parses` 测试是一一对应的。
 *
 * 以前用的是 `e.key`,它是「这个键打出来的字符」而不是「哪个键」:空格是 `" "`,
 * 拼出 `left_ctrl+ `,后端把空段丢掉后注册成单独一个 Ctrl,之后每按一次 Ctrl 都
 * 开始录音;macOS 上 Option+A 的 `e.key` 是 `å`,直接非法。`e.code` 与键盘布局、
 * 修饰键状态都无关,正是全局监听那边看到的东西。
 */
function codeToToken(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  const f = /^F(\d{1,2})$/.exec(code);
  if (f) { const n = Number(f[1]); return n >= 1 && n <= 12 ? `f${n}` : null; }
  const named: Record<string, string> = {
    Space: "space", Enter: "enter", NumpadEnter: "enter", Tab: "tab", Escape: "esc",
    Backspace: "backspace", Delete: "delete", CapsLock: "capslock",
  };
  return named[code] ?? null;
}

/** 不支持的键叫什么,用于「暂不支持 X 键」。 */
function unsupportedKeyName(e: KeyboardEvent): string {
  if (/^(Digit|Numpad)\d$/.test(e.code)) return `数字 ${e.code.slice(-1)}`;
  if (e.code.startsWith("Arrow")) return "方向";
  if (/^F\d+$/.test(e.code)) return e.code;
  if (e.key === "Fn" || e.key === "FnLock") return "Fn";
  // 标点之类:e.key 可读就用它,否则退回物理键名
  if (e.key && e.key.trim() && e.key.length <= 2 && e.key !== "Unidentified") return e.key;
  return e.code || e.key || "这个";
}

/** 单独按下时和平时打字分不开的键:光一个它(或只加 Shift)当快捷键,每次打字都会误触发。 */
const TYPING_TOKENS = new Set(["space", "enter", "tab", "backspace", "delete"]);
const isTypingKey = (t: string) => TYPING_TOKENS.has(t) || /^[a-z]$/.test(t);

function setHotkeyMsg(msg: string, err = true) { hotkeyMsg.value = msg; hotkeyMsgErr.value = err; }

/** 已存的快捷键本身是不是坏的(比如旧版录出来的 `left_ctrl+ `)。坏的话启动时
 *  根本没注册上,而那条日志用户看不到,只能在这里说。 */
async function checkSavedHotkey() {
  if (!savedHotkey.value) return;
  try { await invoke("validate_hotkey", { shortcut: savedHotkey.value }); }
  catch (e) { setHotkeyMsg(`当前快捷键没有生效:${e}`); }
}

function stopHotkeyListening() {
  if (hotkeyHandler) {
    document.removeEventListener('keydown', hotkeyHandler);
    document.removeEventListener('keyup', hotkeyHandler);
    hotkeyHandler = null;
  }
}

// ── 录音方式:按住说话 / 按一下开始、再按一下结束 ──
const hotkeyToggle = ref(false);
async function onHotkeyToggleChange() {
  try {
    await invoke("set_hotkey_toggle", { toggle: hotkeyToggle.value });
    toast(hotkeyToggle.value ? "改为按一下开始、再按一下结束" : "改为按住说话", "ok");
  } catch (e) {
    hotkeyToggle.value = !hotkeyToggle.value;
    toast(`设置失败: ${e}`, "err");
  }
}

// 录制新快捷键期间暂停全局快捷键:否则按下旧组合的那一刻就开始录音了。
watch(hotkeyRecording, on => {
  invoke("set_hotkey_suspended", { suspended: on }).catch(e => console.error("set_hotkey_suspended:", e));
});
// 录到一半离开(关设置面板、切标签页、窗口被收起)就取消录制 —— 不然全局快捷键
// 会一直停在暂停状态,用户只会觉得快捷键坏了。
function cancelHotkeyRecordingIfActive() {
  if (hotkeyRecording.value) startHotkeyRecording();  // 录制中再调一次就是「取消」
}
watch([showSettings, tab], cancelHotkeyRecordingIfActive);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) cancelHotkeyRecordingIfActive();
});

function startHotkeyRecording() {
  hotkeyRecording.value = !hotkeyRecording.value;
  setHotkeyMsg("");
  hotkeyPreview.value = "";
  if (!hotkeyRecording.value) { stopHotkeyListening(); return; }  // 用户点「取消」
  // 修饰键跨事件累积(按 ctrl 再按 alt 不结束;主键按下或纯修饰键
  // 组合全部松开时才结束)。旧实现每次事件新建 parts,且按下 ctrl
  // 就因 parts 非空立即结束——只能录到单个键。
  let mods: string[] = [];
  const modName = (code: string): string | null => {
    switch (code) {
      case 'ControlLeft': return 'left_ctrl';
      case 'ControlRight': return 'right_ctrl';
      case 'AltLeft': return 'left_alt';
      case 'AltRight': return 'right_alt';
      case 'ShiftLeft': return 'left_shift';
      case 'ShiftRight': return 'right_shift';
      default: return null;
    }
  };
  const showPreview = () => {
    hotkeyPreview.value = mods.length ? `${formatHotkey(mods.join('+'))}${IS_MAC ? "" : "+"}…` : "";
  };
  /** 不能用的组合:当场说原因,清掉已按的修饰键,继续录。 */
  const reject = (msg: string) => { setHotkeyMsg(msg); mods = []; showPreview(); };
  const finish = (mainKey: string | null) => {
    const parts = mainKey ? [...mods, mainKey] : [...mods];
    if (parts.length === 0) return;  // 无内容不结束
    const previous = hotkeyStr.value;
    const candidate = parts.join('+');
    hotkeyStr.value = candidate;
    hotkeyChanged.value = candidate !== savedHotkey.value;
    hotkeyRecording.value = false;
    stopHotkeyListening();
    if (!mainKey && mods.length === 1) {
      setHotkeyMsg("只用一个修饰键时,平时每次按它(比如 Ctrl+C 里的 Ctrl)都会开始录音。", false);
    }
    // 用和注册时同一个解析器再验一遍:录得下来却注册不了的组合当场说出原因,
    // 而不是等点了「应用」才冒出一句「更新失败」。
    invoke("validate_hotkey", { shortcut: candidate }).catch(e => {
      if (hotkeyStr.value !== candidate) return;  // 用户已经又录了一次
      hotkeyStr.value = previous;
      hotkeyChanged.value = previous !== savedHotkey.value;
      setHotkeyMsg(`${e}`);
    });
  };
  const handler = (e: KeyboardEvent) => {
    e.preventDefault(); e.stopPropagation();
    const m = modName(e.code);
    if (e.type === 'keydown') {
      if (e.repeat) return;
      // Cmd / Win:后端的全局监听认不出它,录下来也用不了。以前会录成
      // `left_cmd`,点「应用」才失败。
      if (e.code.startsWith('Meta') || e.metaKey) {
        // 按着 Cmd 再按别的键时 e.code 是那个键,名字不能从它取。
        reject(`暂不支持 ${IS_MAC ? '⌘ Cmd' : 'Win'} 键,请用 ${IS_MAC ? '⌃ ⌥ ⇧' : 'Ctrl / Alt / Shift'} 组合`);
        return;
      }
      if (m) {
        if (!mods.includes(m)) mods.push(m);
        setHotkeyMsg("");
        showPreview();
        return;  // 只累积修饰键,等待主键
      }
      const token = codeToToken(e.code);
      if (!token) { reject(`暂不支持 ${unsupportedKeyName(e)} 键`); return; }
      // 修饰键在点「录制」之前就按下了的话,收不到它自己的 keydown,只能从
      // 主键事件的标志位上补回来。分不出左右,就用两边都认的写法。
      for (const [flag, bare] of [[e.ctrlKey, "ctrl"], [e.altKey, "alt"], [e.shiftKey, "shift"]] as const) {
        if (flag && !mods.some(x => x.endsWith(bare))) mods.push(bare);
      }
      if (isTypingKey(token) && mods.every(x => x.endsWith('shift'))) {
        const what = mods.length ? `${IS_MAC ? '⇧' : 'Shift+'}${formatHotkey(token)}` : `单独的 ${formatHotkey(token)} 键`;
        reject(`${what}平时打字就会按到,请配合 ${IS_MAC ? '⌃ 或 ⌥' : 'Ctrl 或 Alt'} 使用`);
        return;
      }
      finish(token);  // 主键按下 → 结束
    } else if (e.type === 'keyup') {
      // 纯修饰键组合:全部松开时结束(如 ctrl+alt 无主键)
      if (m && mods.length > 0) finish(null);
    }
  };
  hotkeyHandler = handler;
  document.addEventListener('keydown', handler);
  document.addEventListener('keyup', handler);
}
async function applyHotkey() {
  if (!hotkeyStr.value) return;
  const shortcut = hotkeyStr.value;
  try {
    await invoke("register_hotkey", { shortcut });
  } catch (e) {
    // 后端给的是中文原因(哪个键、为什么不行)。以前这里只有一句「更新失败」。
    setHotkeyMsg(`${e}`);
    toast(`快捷键没有更新:${e}`, "err");
    return;
  }
  // 注册成功之后才落盘:注册不了的快捷键存进配置,下次启动就是一个静默失效的快捷键。
  savedHotkey.value = shortcut;
  hotkeyChanged.value = false;
  setHotkeyMsg("");
  if (await saveConfigPatch(cfg => { cfg.hotkey.key = shortcut; })) {
    toast(`快捷键已更新为 ${formatHotkey(shortcut)}`, "ok");
  }
}

// ── 识别语言 ──
/** 客户端一律发代码;服务端按当前模型换成它要的写法(stt_engine.resolve_language)。 */
const LANGUAGE_OPTIONS = [
  { code: "auto", label: "自动" },
  { code: "zh", label: "中文" },
  { code: "en", label: "English" },
  { code: "yue", label: "粤语" },
  { code: "ja", label: "日本語" },
  { code: "ko", label: "한국어" },
];
// 手改过 config.json 填了别的语言时也照实显示,不让下拉框变成空白。
const languageOptions = computed(() =>
  LANGUAGE_OPTIONS.some(o => o.code === language.value)
    ? LANGUAGE_OPTIONS
    : [...LANGUAGE_OPTIONS, { code: language.value, label: language.value }]
);
async function onLanguageChange() {
  const next = language.value;
  if (await saveConfigPatch(cfg => { cfg.audio.language = next; })) savedLanguage = next;
  else language.value = savedLanguage;
}

// ── Prompt ──
async function loadPrompt() {
  promptLoading.value = true;
  try {
    promptText.value = await invoke<string>("get_llm_prompt");
    promptLoaded.value = true;
    promptStatus.value = "";
  } catch (e) {
    promptLoaded.value = false;
    promptStatus.value = `读取失败:${e}`;
  }
  promptLoading.value = false;
}
async function savePrompt() {
  if (!promptText.value.trim()) { toast("提示词不能为空;想用默认的请点「恢复默认」", "err"); return; }
  promptLoading.value = true;
  try {
    await invoke("save_llm_prompt", { text: promptText.value });
    promptStatus.value = "已保存";
    toast("提示词已保存", "ok");
  } catch (e) {
    promptStatus.value = "保存失败";
    toast(`${e}`, "err");
  }
  promptLoading.value = false;
}
async function resetPrompt() {
  promptLoading.value = true;
  try {
    promptText.value = await invoke<string>("reset_llm_prompt");
    promptLoaded.value = true;
    promptStatus.value = "已恢复默认";
    toast("已恢复默认提示词", "ok");
  } catch (e) { toast(`${e}`, "err"); }
  promptLoading.value = false;
}
// 进「服务」页、且后处理开着时自动读一次提示词,不用再手点「加载」。
watch([showSettings, tab, llmEnabled], ([open, t, on]) => {
  if (open && t === "service" && on && !promptLoaded.value && !promptLoading.value) loadPrompt();
});

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
  // 原来这里是 120 秒硬超时。Windows 的 NSIS 安装包三四十兆,网络慢一点就会在
  // 下载还好好地进行时弹「下载超时」,而后端其实还在下、下完照样会退出应用 ——
  // 用户看到的是「失败了,然后程序自己关了」。
  //
  // 改成:预算放宽到 10 分钟,并且**只要还在收到进度事件就不算超时**。真卡住了
  // (一个事件都没有)才认输。后端每收到一块数据就 emit 一次 update-progress。
  const INSTALL_STALL_MS = 600000;
  let lastProgress = Date.now();
  const stall = new Promise((_, reject) => {
    const t = setInterval(() => {
      if (Date.now() - lastProgress > INSTALL_STALL_MS) {
        clearInterval(t);
        reject(new Error("更新长时间没有进展"));
      }
    }, 5000);
    installStallTimer = t;
  });
  const un = await listen<string>("update-progress", e => {
    lastProgress = Date.now();
    updateStatus.value = e.payload;
  });
  try {
    const msg = await Promise.race([
      invoke<string>("install_update"),
      stall
    ]) as string;
    updateStatus.value = msg;
    updateStatusType.value = "ok";
    toast("更新已安装，重启后生效", "ok");
  } catch (e) { updateStatus.value = `安装失败: ${e}`; }
  finally {
    un();
    if (installStallTimer) { clearInterval(installStallTimer); installStallTimer = null; }
  }
  updateInstalling.value = false;
}

// ── Result ──
async function copyResult() {
  if (!result.value) return;
  // 以前不等结果,写剪贴板失败也显示「已复制」。
  try { await navigator.clipboard.writeText(result.value); }
  catch (e) { toast(`复制失败: ${e}`, "err"); return; }
  copyFeedback.value = true;
  toast("已复制", "ok");
  setTimeout(() => { copyFeedback.value = false; }, 2000);
}
async function doAutoInput() {
  if (!result.value) return;
  // 点这个按钮时焦点在本应用自己的窗口上:让后端先把前台交还给上一个应用再敲字,
  // 不然字全敲给了自己。
  try { await invoke("auto_input", { text: result.value, handBackFocus: true }); toast("已输入", "ok"); }
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

// 托盘里的状态行跟着头部走。窗口藏着的时候，托盘是用户唯一能看状态的地方。
// 托盘状态行和头部说同一件事(含「模型加载中」「模型加载失败」),都来自 connView。
const trayStatusText = computed(() =>
  `${connView.value.cls === "on" ? "●" : "○"} ${connView.value.text}`);
watch(trayStatusText, text => { invoke("set_tray_status", { text }).catch(() => {}); }, { immediate: true });

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
  // 最先做：后面每一步的 toast 都要排在启动日志后面，而不是被补拉的缓冲插到前头。
  await initGuiLogs();
  try { trayOk.value = await invoke<boolean>("tray_available"); } catch {}
  try { build.value = await invoke<{ version: string; build_id: string; built_at: string }>("get_build_info"); }
  catch (e) { console.error("get_build_info error:", e); }
  await loadConfig();
  await loadAutostart();
  await refreshDevices();
  await refreshPermissions();
  await refreshServers();

  // 后台心跳(R12):先拉一次当前结论——事件只在状态变化时才发,webview 起来之前
  // 发过的收不到——再订阅之后的变化。
  listen<SttHealth>("stt-health", (event) => applySttHealth(event.payload));
  try { applySttHealth(await invoke<SttHealth>("get_stt_health")); }
  catch (e) { console.error("get_stt_health error:", e); }

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
  // 录音中按了 Esc:这一段不要了。收起录音状态,不进入「识别中」。
  listen("recording-cancelled", () => {
    recording.value = false;
    loading.value = false;
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    if (levelInterval) { clearInterval(levelInterval); levelInterval = null; }
    toast("已取消这一段录音", "info");
  });
  // 托盘菜单里的「检查更新」「设置」。以前前端听着 tray-check-update，却没有任何地方发它。
  // 检查结果显示在「关于」页，得切过去，不然用户看到的是一个跟更新无关的页面。
  listen("tray-check-update", () => { showSettings.value = true; tab.value = "about"; doCheckUpdate(); });
  listen("tray-open-settings", () => { showSettings.value = true; });
  // 从系统设置授权回来时窗口会重新拿到焦点：顺手查一次权限，输入监控刚授权的话
  // 上面的 watcher 会把快捷键监听器重建起来，不用用户再去点「刷新」。
  window.addEventListener("focus", () => { if (perms.value?.is_macos) refreshPermissions(); });

  // 后台每 6 小时检查一次更新。启动时的那一次在本函数末尾(静默的那次),
  // 以前这里还有一个 30 秒后的,同一次启动要查两遍。
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
        // 主窗口正在前台时,焦点就在本应用自己身上,自动输入只会敲给自己。
        // 不输,提示用户用「输入」按钮(它会先把前台交还给上一个应用)。
        if (document.hasFocus()) {
          toast("主窗口在前台,结果没有自动输入;点「⌨️ 输入」发送到上一个窗口", "info");
        } else {
          invoke("auto_input", { text }).catch(e => { toast(`${e}`, "err"); refreshPermissions(); });
        }
      }
    }
  });

  // 与 stt.rs 的 `NO_SPEECH` 逐字一致。录到的是静音 / 什么都没识别出来时后端发的就是它：
  // 这不是故障，不该弹红色的「失败」。以前空结果走的是 transcribe-done("")，
  // 这里直接跳过，用户说完话什么反馈都没有。
  const NO_SPEECH = "没有录到声音";
  listen<string>("transcribe-error", (event) => {
    loading.value = false;
    llmProcessing.value = false;
    if (processingTimerInterval) { clearInterval(processingTimerInterval); processingTimerInterval = null; }
    if (event.payload === NO_SPEECH) toast("没听到声音，请靠近麦克风再说一次", "info");
    else toast(`失败: ${event.payload}`, "err");
  });

  // 录音相关的提醒：配置的麦克风不在、改用了默认麦克风；录音中麦克风断开；
  // 录满 5 分钟自动停止。以前这些要么静默，要么只打到终端。
  // Rust 的 emit_app_warning 已经 log_error! 过一次，日志页经 gui-log 就能看到，toast 不再重复记。
  listen<string>("app-warning", (event) => toast(event.payload, "info", false));

  // LLM 后处理没做成、退回了原文(R8)。结果照常出来,但得说一声这次没经过 LLM,
  // 不然用户只会觉得「后处理怎么没效果」。
  listen<string>("transcribe-warning", (event) =>
    toast(`LLM 后处理没做成，已使用原文：${event.payload}`, "err"));

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
.settings-panel { position: absolute; top: 44px; left: 0; right: 0; bottom: 24px; background: var(--bg); z-index: 50; overflow: hidden; display: flex; flex-direction: column; }
/* 标签栏固定在面板顶部，只有下面的内容区滚动 —— 滚到第三屏还能一键换页。 */
.tabs { display: flex; flex-shrink: 0; border-bottom: 1px solid var(--border); }
.tab { flex: 1; background: none; border: none; border-bottom: 2px solid transparent; color: var(--muted); font-size: 0.72rem; padding: 8px 0; cursor: pointer; font-family: inherit; }
.tab:hover { color: var(--fg); }
.tab.active { color: var(--blue); border-bottom-color: var(--blue); }
.settings-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 12px 14px; }
.about-row { display: flex; justify-content: space-between; align-items: baseline; padding: 3px 0; }
.about-val { font-size: 0.72rem; color: var(--fg); }
.about-val.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.66rem; }
/* 长说明收进折叠块：需要的时候点开，不需要的时候不占三行。 */
.s-help > summary { font-size: 0.65rem; color: var(--muted); cursor: pointer; margin-top: 6px; }
.s-help > summary:hover { color: var(--fg); }
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
.s-tip.s-err { color: var(--red); }
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
.choice-banner { width: 100%; max-width: 360px; background: rgba(96, 165, 250, 0.1); border: 1px solid rgba(96, 165, 250, 0.3); border-radius: 8px; padding: 8px 10px; text-align: center; }
.choice-q { font-size: 0.75rem; color: var(--text); }
.choice-actions { display: flex; gap: 8px; justify-content: center; margin-top: 6px; }

/* 服务器 */
.mode-switch { gap: 0; }
.mode-btn { flex: 1; border-radius: 0; }
.mode-btn:first-child { border-radius: 6px 0 0 6px; }
.mode-btn:last-child { border-radius: 0 6px 6px 0; border-left: none; }
.mode-btn.active { background: rgba(96, 165, 250, 0.15); color: var(--blue); border-color: rgba(96, 165, 250, 0.4); }
.srv-problem { color: var(--yellow); }

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
.log-entry.err { color: var(--red); }
.log-entry.warn { color: var(--yellow); }
.log-entry.ok { color: var(--green); }
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
