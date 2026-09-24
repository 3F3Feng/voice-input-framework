# Changelog

## [2.4.0] - 2026-09-24

> **升级须知**:边录边识别、服务版本号要新版服务端才有。更新应用后,主界面会提示
> 「本机服务比应用旧」,在「设置 → 服务」点「更新服务」即可(或者自己在仓库里
> `git pull --ff-only` 再跑 `scripts/setup-env.sh`)。服务端没更新时一切照旧能用,
> 只是还按老办法松手后整段上传、整段识别。

### Added

- **边录边传、录音期间分段识别**:按下快捷键就连上服务、边录边传,服务端在录音期间
  每攒够约 20–28 秒就在停顿处切一段先识别;松手时只剩最后一小段。实测(M3 Max,
  Qwen3-ASR)101 秒的口述松手后 0.8 秒出结果(以前 3.3 秒),217 秒的 0.8 秒(以前 7.6 秒);
  短句不变。去掉标点后和整段识别逐字一致,分段处偶尔一个逗号变句号。连不上、服务端
  太老、中途断线时,松手后照以前的办法整段上传,录音不会丢;按 Esc 放弃时服务端这段
  也作废。
- **本机服务比应用旧时提示,并可一键更新**:应用内更新只换客户端,服务跑的是仓库里的
  代码。现在服务会报项目版本,比应用旧(或太旧、报不出版本)时主界面有提示;本地模式下
  「更新服务」会 `git pull --ff-only`、重跑建环境脚本(按当前环境还原 `--llm` 等参数)、
  重启本应用拉起的服务。仓库有未提交的改动、分叉、合并进行中、服务不是本应用拉起的等
  情况一律不动,给出手动命令;从不 reset / stash / checkout。
- **上次异常退出时说明原因**:崩溃后下次启动,主界面提示「上次异常退出」和一句原因
  (如 `EXC_BREAKPOINT (SIGTRAP) · voice_input_lib::input::press_paste`),可以在 Finder /
  资源管理器里定位系统崩溃报告、复制诊断信息;Rust panic 连同调用栈写进日志。强制退出、
  断电之类没有崩溃报告的只记日志,不提示。

### Changed

- CI 的 Python 测试只跑 3.11 / 3.12,和 `requires-python` 一致。

## [2.3.2] - 2026-09-24

### Fixed

- **macOS:识别完贴字时偶尔闪退**。粘贴(2.3.0 起的默认输出方式)要模拟 ⌘V,enigo 为此
  按当前键盘布局查 V 的键码,调的系统接口只许在主线程上用,我们却在后台线程上调,macOS
  直接中止整个应用(SIGTRAP)。enigo 缓存了布局,切过输入法后才重新查,所以是偶尔崩。
  粘贴和逐字输入的模拟按键现在都在主线程上执行。
- **「聊天」预设整理出来的句子标点丢了**:换成 Gemma-4-E2B 后,聊天预设有时把句中的逗号、
  问号全部去掉(8 句里 3 句)。规则写的是「句末不加句号」,示例却又以句号结尾,模型拿不准,
  干脆不加标点。改为明说「照常加完整的标点,唯一的例外是整段末尾的句号省掉」,示例全部
  换成聊天口吻(句中有逗号、问句有问号、末尾无句号)。实测中英两份都是 16/16。
  自己写过提示词、里面有「句尾不要加句号」的,也建议照这个写法改:只说不加句号、示例又
  没有标点时,Gemma 会把所有标点都去掉(实测 0/8)。
- `tools/llm_prompt_eval.py` 给「聊天」预设加了 8 句标点检查。

## [2.3.1] - 2026-09-24

### Fixed

- **macOS:应用内更新后「输入监控」「辅助功能」失效**。2.3.0 的发布包只有 ad-hoc 签名,
  签名身份每次构建都变;macOS 按签名身份记授权,于是更新后系统设置里 Voice Input
  的开关还开着,应用里却显示「已拒绝」,快捷键和自动输入失效。从这一版起发布包用
  固定证书签名(身份是 bundle id + 证书名,跨版本不变),发版流水线缺证书时直接失败,
  并在构建后校验签名。
  - 原来装的是本地签名版(`scripts/build-macos.sh`)的:更新到 2.3.1 后原来的授权直接恢复。
  - 原来装的是 2.3.0 或更早的发布包的:需要最后再授权一次 —— 在系统设置对应列表里选中
    Voice Input 点「−」删掉、再重新添加;之后的更新不会再丢。
- 权限页和向导里说明了「开关开着却显示已拒绝」该怎么处理。

## [2.3.0] - 2026-09-23

> **升级须知**:应用内更新只更新桌面客户端。STT / LLM 服务跑的是你本机仓库里的代码,
> 请同时 `git pull` 并重跑 `scripts/setup-env.sh`(Windows 用 `setup-env.ps1`)——
> 新版客户端用到的词库、文件转写、中英文提示等接口都在新版服务端里。
> 默认 LLM 换成了 Gemma-4-E2B,首次启动会下载约 4 GB;下载完成前识别照常可用,
> LLM 后处理就绪后自动接上。新模型加载失败(依赖太旧、没网)时自动退回旧默认模型。

这一版是一次从用户视角出发的体验大修(完整清单与实测记录见 `docs/UX_REVIEW.md`)。

### Added

- **首次启动向导**:本地 / 远程两条路、环境体检、服务启动与模型下载进度、macOS 权限、
  输出方式、「试一下」。只在全新安装或连不上可用服务时自动出现;随时可以从
  「设置 → 服务」顶部或托盘「设置向导…」重新打开,每一步都能退回上一步。
- **英文界面**:「设置 → 常规 → 界面语言 / Language」可选跟随系统、中文、English。
  主窗口、设置、向导、录音胶囊、托盘、错误提示、环境体检、服务端返回的提示都有
  中英两套;macOS 麦克风授权说明跟随系统语言;Windows 安装器带简体中文。
- **个人词库**:热词让识别偏向你的专有名词,`陶睿 => Tauri` 这样的规则在识别后替换;
  LLM 整理时也不会把它们「纠正」掉。
- **提示词预设**:聊天、邮件 / 文档、技术 / 编程,一键填入,可恢复默认。
- **输出方式**:粘贴(默认,借用剪贴板后还原)、模拟打字、只复制到剪贴板。
- **结果可编辑、可对照原文**;识别历史保存在本机(可关),改过的字写回历史。
- **录音方式**:按住说话,或按一下开始、再按一下结束;录音中按 Esc 放弃这一段。
- **更多快捷键**:Cmd / Win、Fn(macOS)、数字、F13–F20;快捷键起不来时主界面会说明原因。
- **转写音频文件**:任意常见格式(会先解码、重采样)。
- **环境体检**与 PowerShell 版建环境脚本 `setup-env.ps1`。
- **模型下载进度**与下载源选择(可切 hf-mirror.com)。
- **远程访问令牌**:服务端设 `VIF_API_TOKEN` 后,客户端在远程设置里填上令牌即可。
- **非 Apple 平台的 LLM 后处理**:LLM 服务加 llama.cpp 后端(GGUF)。
- **托盘菜单**:状态、复制最近一条结果、设置、设置向导、检查更新。
- 跟随系统的浅色 / 深色外观;更新装完自动重启。
- `tools/llm_prompt_eval.py`:改提示词、换模型前对真模型跑的自动检查。

### Changed

- **默认 LLM 换成 Google 的 Gemma-4-E2B QAT 4bit**(MLX 与 llama.cpp 两边)。
  对 9 个小模型跑同一套 64 例检查(中文、英文、中英混说、改口、夹术语……):
  它只有 1 例不合格,延迟约为旧默认的一半,内存相当;旧的 llama.cpp 默认
  Qwen3.5-2B 有 54 例不合格(基本原样照抄)。依赖要求随之提高:
  `mlx-lm >= 0.31.2`、`llama-cpp-python >= 0.3.25`。
- **提示词照顾中英混说**:删中英文填充词、改口只留改口后的、一个词都不翻译。
  以前英文口述会被整理成中文,混说里的 deploy / rollback 会被译成「部署」「回滚」。
- 识别语言可选;出厂默认值按机器挑选(Apple Silicon 用 Qwen3-ASR)。

### Fixed

- **假成功与丢数据**:长录音丢后半段、满 5 分钟不转写、LLM 输出被截断还报成功、
  「帮我写一首诗」被真的写成一首诗、静音被识别成「谢谢观看」一类的幻觉、
  上传 44.1 kHz WAV / m4a 返回空文本、`whisper_mlx*` 一句都转不出来、
  非 Apple 平台说话超过 30 秒必然失败。
- **卡死与无反馈**:推理阻塞服务的事件循环;服务卡住时客户端干等(现在转写期间有心跳,
  60 秒没动静即判定卡住);切换模型一直显示「正在加载」;LLM 切换失败后什么模型都没有
  (现在回退到原来的);后台时失败没有任何提示(胶囊会说明原因)。
- **Windows**:拉起服务时弹黑色控制台窗口、子进程日志写满后服务卡死、
  重启后把无关进程认成自己的服务并在退出时杀掉;英文版 Windows 上保存中文提示词失败。
- 「⌨️ 输入」按钮把字敲给了本应用自己;模拟打字时换行变回车(聊天软件里等于提前发送)。
- 再次打开应用没反应、双开导致按一次录两遍;端口输入不校验;启动时检查两次更新等。

## [2.2.0] - 2026-09-19

> 版本号从这一版起**只有一个来源**:`gui/src-tauri/Cargo.toml` 的 `package.version`。
> `tauri.conf.json` 不再写 `version`(Tauri 缺省回落到 Cargo.toml),`gui/package.json`
> 的 `version` 字段也删掉了 —— 那两处从来没人读,却总和真版本号对不上;界面底栏
> 一度显示的还是配置文件的 schema 版本「2.0」。

### Added

- **客户端内管理本地服务**:设置面板里直接启停本地 STT / LLM 服务,不用再开终端。
  保留「远程连接」模式,行为与改动前逐字相同。
  进程归属分三档:本应用启动的、外部但确认是本项目的(按模块名 + 工作目录认)、
  认不出来源的。**只有前两档允许从界面停止** —— 认不出来源的进程本应用只连接、绝不停它。
- **LLM 服务跟着「LLM 后处理」开关起停**:开关关着就不占着几个 G 的内存。
  开关的权威在 STT 服务的 `/llm/enabled`;客户端配置里的 `llm.enabled` 只是一份
  缓存,用来回答「启动那一刻 STT 还没起来、问不到」的那个问题,STT 健康后立刻对账。
- **每次构建打唯一 ID**:版本号只在发版时才动,两次发版之间可能有几十次本地构建。
  构建 ID 出现在启动日志、设置面板的「关于」和底栏(前 8 位,悬停看全串)。
  `scripts/build-macos.sh` 每次现生成一个,并在构建后校验它真的编进了产物。

### Changed

- **设置面板分页**:十个分段堆在 400×500 的窗口里一路往下滚,找一个开关要滚三屏。
  改为「服务 / 常规 / 权限(仅 macOS)/ 日志 / 关于」五页。
- **两个日志框合并成一个**:客户端自己的日志原本在面板最底下,STT / LLM 子进程的
  输出在「服务器」一段里,并排摆着没人分得清哪个是哪个。现在共用一个框,按来源切换。
  不做时间线交织 —— 三路日志的时间戳格式各不相同,按猜测排到一起只会造出一条
  看着可信、其实是编的时间线。

### Fixed

- **自动更新从来就没有能用过(发版流水线 + 客户端两处都坏)**。
  - 更新包的签名密钥只挂在 `build.yml`(那条流水线的产物 30 天后就删,没人安装),
    真正出 release 的 `build-release.yml` 反而没有,于是每次发版都没有 `.sig`;
    `latest.json` 里三个平台的 `signature` 全是空串(v2.0.10 的线上清单至今如此)。
  - `bundle.createUpdaterArtifacts` 没开,所以就算给了密钥也不会产出更新器产物。
  - `latest.json` 把 macOS 指向 `.dmg`。更新器要的是 `.app.tar.gz`,`.dmg` 装不了。
  - 客户端**根本没用更新器插件**:`update.rs` 自己用 reqwest 下载,按操作系统
    硬编码扩展名再 `open` 一下,`latest.json` 里的 `signature` 读进来就丢掉 ——
    等于从一个 URL 下载任意二进制直接执行,完全没有校验。
  - 现在:发版流水线传签名密钥、开更新器产物、清单指向正确的产物,并且
    **签名缺失或密钥与 pubkey 对不上时直接让发版失败**(tauri 原本只警告一句);
    客户端改用 `tauri-plugin-updater`,签名验不过就装不上。
- **Linux / Windows 的更新路径**(在 CI 上真跑了一遍三平台构建、把产物拉下来核对过):
  - Linux 的更新产物**不是** `.AppImage.tar.gz` —— tauri 2.10 直接给 AppImage 本体
    签名。清单原先指着一个根本不存在的文件。
  - 用 `.deb` 装的用户单独需要一条 `linux-x86_64-deb`:插件在 Linux 上按安装方式
    分派,deb 走 `install_deb()`,拿到 AppImage 只会报 `InvalidUpdaterFormat`。
  - collect 那一步的 `*.tar.gz` 会把 deb 拆出来的 `control.tar.gz` / `data.tar.gz`
    (7MB)一并收走发成 release 资产,收窄成 `*.app.tar.gz`。
  - `.msi` / `.rpm` 不发布但各自带一个 `.sig`,这类孤儿签名现在会被清掉。
  - 四个平台产物的签名已用配置里的 pubkey 逐个验过(Ed25519 + BLAKE2b 预哈希,
    和客户端做的是同一件事),仓库 secret 里那把私钥确实是配套的。
- **更新下载在慢网络上会谎报超时**:前端给 `install_update` 套了 120 秒硬超时,
  而 Windows 的 NSIS 包三四十兆 —— 下载还好好地进行时就弹「下载超时」,后端其实
  还在下、下完照样退出应用。改成按「有没有进展」判断。顺带接上从来没人监听的
  `update-progress` 事件(此前整个下载过程界面上只有一句不动的「正在下载...」),
  并给它加了节流,免得上千个事件把 IPC 刷爆。
- **发版资产里混着 pyinstaller 的 `.app` 包内容**:`CodeResources`、`Info.plist`、
  `icon-windowed.icns`、以及一个版本号还停在 2.0.0 的可执行文件,四个都挂在
  v2.0.10 的 release 上。起因是 `path: dist/*` 把整个 `.app` 收走,而清理那一步
  排在摊平之前,只清到了根目录。
- **发版时不再检查 tag 和源码版本号是否一致**:两者不一致会让客户端陷入
  「发现新版本 → 更新 → 还是老版本」的死循环。现在对不上直接让发版失败。

- **关闭按钮直接退掉整个应用**:`CloseRequested` 里先 `hide()` 了窗口却没有
  `prevent_close()`,窗口照样被真正关掉,主窗口一关应用就跟着退了 —— 用户点 ✕
  只想收起界面,结果全局快捷键一起没了。现在退出只有托盘菜单一条路。
- **打开 LLM 后处理后模型下拉框是空的**:LLM 模型列表只在连接时跟着 STT 的列表拉过
  一次,而后处理默认关着时 LLM 服务没在跑,那一次必然失败,之后再没人补拉。
  现在开关打开成功后、以及服务器面板看到 LLM 进入「运行中」而列表还空着时各补一次。
- **从界面启动服务后卡在「未连接」**:本地模式下客户端该连 `127.0.0.1:<本地端口>`,
  前端却拿远程模式那对字段自己拼地址,于是服务在本地跑着、客户端一直去敲用户填的远程地址。
- **accessory 应用启动时主窗口不前置**,表现为「没勾『启动时最小化』却自己最小化了」。
- **快捷键路径静默失败**:快捷键触发的转录出错时不说话,用户只看到什么都没发生。
- **转录结果被客户端篡改**:后处理里会去掉引号、撇号并去重行,把正文改坏。

## [2.1.0] - 2026-09-18

### Changed

- **macOS 以菜单栏应用（accessory）身份运行**：不再占用 Dock 图标，主窗口从托盘菜单打开。
  这不只是外观取舍 —— 常规（regular）应用的窗口在 macOS 上**无法加入其它应用的全屏 Space**，
  悬浮胶囊因此在全屏应用里完全不可见。实测即使 `collectionBehavior` 设为
  `CanJoinAllSpaces|FullScreenAuxiliary` 且层级提到 `NSStatusWindowLevel`，
  `isOnActiveSpace` 在全屏场景下仍为 `false`；accessory 应用不受此限制。

### Added

- **macOS 权限申请**：设置面板内可查看/申请麦克风、输入监控、辅助功能三项权限，
  并在缺失时给出跳转系统设置的入口。详见 `docs/macos-permissions.md`。
- **本地签名构建脚本** `scripts/build-macos.sh`：用本机证书签名、校验产物、安装到
  `/Applications`。详见 `docs/macos-signed-build.md`。

### Fixed

- **LLM 后处理返回整段推理过程**：推理模型（如 Qwen3.5-4B-OptiQ）会把思考过程当正文输出，
  而 `max_tokens=256` 全部消耗在思考上，根本走不到答案 —— 结果是一大段英文分析被
  敲进用户文档，接口却返回 `success=true`。改为在 `apply_chat_template` 传
  `enable_thinking=False`，延迟同时从 ~3900ms 降到 ~600ms。
- **麦克风在 hardened runtime 下被直接拒绝**：用真证书签名会自动启用 hardened runtime，
  而包内未声明 `com.apple.security.device.audio-input`，导致系统直接拒绝、不弹窗、
  也不出现在隐私设置列表里。新增 `Entitlements.plist`。
- **悬浮胶囊在 macOS 上不可见**：`screen_center_bottom` 把 `Monitor::size()` 的
  物理像素当逻辑像素用，Retina（scale=2）下算出的 y 坐标落到屏幕之外。
  改用 `work_area()` 并按 `scale_factor` 换算，底边对齐到 Dock 上方。
- **悬浮胶囊周围的黑色方块**：改回透明窗口。此前"透明窗口不渲染"的判断是被上述
  定位缺陷误导的 —— macOS 对完全离屏的窗口会推迟 WebView 渲染。
- **录音时抢走焦点**：移除 `indicator::show()` 中的 `set_focus()` 调用。
  语音输入的前提是把文字打进用户当前的应用，录音一开始就抢焦点会破坏这个前提。
- **托盘图标是绿色方块**：`tray.rs` 取的是 bundle 图标列表首项 `icons/32x32.png`，
  而那是 Tauri 的占位图（`icon.icns` 里一直是正确图标，所以 Finder 显示正常）。
  已从 1024×1024 原始 logo 重新生成全套图标。
- **服务端默认绑定 `0.0.0.0` 且 CORS 全开**：改为默认 `127.0.0.1`（env 可覆盖）、
  CORS 走显式列表，并对上传大小与 `/process` 文本长度施加上限。
- **并发与资源**：LLM 生成加锁串行化（`/process` 运行在线程池，原先并发请求会
  在同一模型实例上互相踩）；切换模型时释放旧模型；STT 的模型拆卸移入 `_load_lock`。
- **GUI 错误信息永远显示 "Unknown error"**：服务端发送的是 `error_message`，
  而 `stt.rs` 读的是 `message`。
- **音频二次重采样**：采集回调推入的已是 16 kHz，但 `stop()` 返回设备原生采样率，
  批处理回退路径据此再次重采样。
- **macOS 快捷键**：重复注册会泄漏旧的 event tap；非 Windows 路径缺少
  `MAX_RECORD_SECS` 安全停止；`<100ms` 的快按会吞掉 Release 事件导致录音卡在开启状态。

### Removed

- `gui/src-tauri/src/text.rs`（从未被 `lib.rs` 声明为模块，不参与编译）。
- `LLMEngine.load()` 中不可达的 `if self._loading:` 等待分支（若可达反而会自死锁）。

## [2.0.10] - 2026-05-30

### Fixed

- **Hotkey release detection after minimize (Windows)**: completely rewrote hotkey
  detection to use `GetAsyncKeyState` polling instead of `rdev` `WH_KEYBOARD_LL`
  hook. The hook stops firing KeyRelease events when the Tauri webview is minimized
  to tray, causing recordings to never stop. Pure Win32 API polling works
  regardless of window state.

## [2.0.1] - 2026-05-26

### Fixed

- **Hotkey debounce**: prevent double-trigger when mouse side button is mapped to modifier key
  - State guard, time debounce (150ms), min press duration (50ms, keep recording)
  - Fixes stuck recording state with mouse button bounce

## [1.1.6] - 2026-05-26

### Fixed

- **Hotkey debounce**: prevent double-trigger when mouse side button is mapped to modifier key
  - State guard: skip PRESS if already recording, skip RELEASE if not recording
  - Time debounce: ignore PRESS events within 150ms of last RELEASE
  - Min duration guard: ignore RELEASE events within 50ms of PRESS (noise spike),
    keeps recording active until the real release
  - Fixes issue where mouse button bounce caused stuck recording state and
    "already recording" popup with persistent floating indicator

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.5] - 2026-04-10

### Added
- **System Integration Features** (v1.1.5 milestone)
  - Startup tray notification - Shows "Ready" notification with hotkey info on launch
  - Update checker - Check for updates via GitHub releases, accessible from tray menu
  - Auto-start registration - One-click enable/disable开机自启动 from tray menu
    - Windows: Registry `HKEY_CURRENT_USER\...\Run`
    - macOS: LaunchAgents plist
    - Linux: `~/.config/autostart/` .desktop file

### Changed
- Updated ROADMAP to mark v1.1.5 features as completed

## [1.1.0] - 2026-04-08

### Added
- GitHub CI for unit tests
- Build configuration for Windows exe

### Fixed
- Build exe dependencies (numpy)
- Ubuntu CI with xvfb system package
- HotkeyVoiceInput import recovery
- Separated Ubuntu/macOS/Windows test workflows

## [1.0.2] - 2026-04-07

### Fixed
- Processing time logging and statistics output

## [1.0.1] - 2026-04-06

### Fixed
- Removed duplicate result processing calls

## [1.0.0] - 2026-04-05

### Added
- Initial release
- Real-time audio capture from microphone
- Streaming ASR with low latency
- Multiple model support:
  - Qwen3-ASR-1.7B (recommended, 52 languages/dialects)
  - Qwen3-ASR-0.6B (faster, real-time scenarios)
  - Whisper-large-v3 (OpenAI classic)
  - Whisper-small (lightweight)
- Client/server architecture for remote deployment
- Cross-platform GUI client (Windows/macOS/Linux)
- WebSocket streaming API
- System tray integration
- Hotkey-based recording control

[1.1.5]: https://github.com/3F3Feng/voice-input-framework/compare/v1.1.0...v1.1.5
[1.1.0]: https://github.com/3F3Feng/voice-input-framework/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/3F3Feng/voice-input-framework/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/3F3Feng/voice-input-framework/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/3F3Feng/voice-input-framework/releases/tag/v1.0.0
