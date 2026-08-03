# Voice Input Framework — 架构审查报告

> 审查日期:2026-08-03
> 审查范围:整个仓库(commit `a279c8e` 基线)
> 审查方式:静态代码审查(结构分析 + 关键路径验证),未运行服务端。

## 一、项目概述

Voice Input Framework (VIF) 是一个基于大模型的语音识别框架,支持实时流式语音识别与 LLM 后处理。当前处于 **"单体 → STT/LLM 分离服务" 迁移中途**:

- **STT 服务** `services/stt_server.py`(端口 6544):FastAPI,负责音频转写,并**兼任 LLM 代理**(转发 `/llm/*` 请求到 6545)。
- **LLM 服务** `services/llm_server.py`(端口 6545):独立进程,`mlx_lm` 加载模型做文本后处理,解决 transformers 版本冲突。
- **两套客户端并存**:Python 客户端(`client/`,PySimpleGUI)与 Tauri GUI(`gui/`,Rust + Vue 3)。
- **旧单体残留**:`server/` 包(除 `models/` 外)、`vif-run.py`、`client/gui.py`、`shared/protocol.py` 等仍是死代码或失效入口。

## 二、架构现状

### 2.1 目录职责

| 目录 | 职责 | 状态 |
|------|------|------|
| `services/` | **新架构核心**:`stt_server.py`、`llm_server.py`、`diarize_engine.py` | ✅ 活跃 |
| `shared/` | 共享协议/类型/模型注册表:`protocol.py`、`data_types.py`、`model_registry.py` | ⚠️ 部分死代码 |
| `server/` | 旧单体:config、llm_engine、models/(STT 引擎实现,仍被新服务使用) | ⚠️ 半废弃 |
| `client/` | Python 客户端(app.py 新入口 + 拆分出的模块) | ✅ 活跃 |
| `gui/` | Tauri + Vue 客户端 | ✅ 活跃 |
| `llm_postprocessing/` | LLM 评估/基准工具 | ⚠️ 仅被死代码引用 |
| `models/` | 模型文件存放目录 | — |
| `deploy/` `config/` `scripts/` | launchd/systemd 部署、启动脚本 | ⚠️ 引用失效入口 |
| `tools/` | 独立工具(audio_transcriber_gui.py) | ✅ |
| `tests/` `client/tests/` | pytest 测试 | ✅(见 2.4) |

### 2.2 数据流

```
GUI (client/app.py 或 gui/Tauri)
   │  WebSocket ws://host:6544/ws/stream (音频流)
   │  HTTP http://host:6544 (/models, /transcribe, /llm/*)
   └─ HTTP 直连 http://host:6545 (/process)          ← client/app.py:41 硬编码 6545
        │
services/stt_server.py ──httpx 转发 /llm/*──▶ services/llm_server.py
        │
server/models/* 引擎(MLX / whisper.cpp / transformers)
```

依赖方向:client → services 仅网络协议(无 import);services → shared/ + server/models/。

> ⚠️ **当前实际状态(2026-08-03 复核)**:上图中 Python 客户端的 LLM 路径**实际不可用**——`client/app.py:41` 把 `LlmClient` 指向 6545,但 `LlmClient` 请求的是 `/llm/*` 路径(`client/network.py:639,690,724,755`),而 LLM 服务(6545)的路由是 `/models`/`/process`/`/prompt`(无 `/llm/*`),必然 404;且 `app.py` 调用的 `stt.transcribe`/`stt.get_models`/`llm.process`/`llm.get_models` 在 `network.py` 中不存在,`run_client.py` 启动即崩(见 H5/M2)。

### 2.3 技术栈

- 服务端:FastAPI + uvicorn + pydantic v2 + websockets + httpx;STT 用 mlx-audio / mlx_whisper / whisper.cpp / transformers(torch);LLM 用 mlx-lm。
- Python 客户端:PySimpleGUI、pynput、sounddevice、pyautogui、pystray。
- Rust 客户端:Tauri 2 + tokio-tungstenite + reqwest。
- 多份 requirements-*.txt 对应独立 conda 环境(vif-stt、mlx-test)。

### 2.4 测试组织

- `pytest.ini` testpaths 仅 `tests/`(10 个文件);`client/tests/` 只有 1 个文件且不在默认 testpaths。
- `tests/test_e2e.py` 标 `pytest.mark.integration`,依赖真实服务;`scripts/test_services.py` 是重复的独立集成脚本,硬编码端口。
- CI 只跑 `tests/ -m "not integration"`,不装 GUI 依赖,且 black/ruff 后接 `|| true`——**lint 失败不阻塞 CI**。

## 三、发现的问题

按严重程度分级:**🔴 高(功能必坏/崩溃)**、**🟠 中(行为错误/隐患)**、**🟡 低(卫生/维护性)**。

### 3.1 🔴 高优先级

| # | 问题 | 证据 |
|---|------|------|
| H1 | **失效入口 `vif-run.py` 引用不存在的 `server.api`** | `vif-run.py:27` `from server.api import main`;`server/api.py` 不存在。第 7-23 行还用 `types.ModuleType` 伪造 `server.models` 模块。`deploy/launchd.plist:12`、`deploy/voice-input-framework.service:13` 同样引用不存在的入口 |
| H2 | **`STTEngine.transcribe` 返回类型不一致** | `services/stt_server.py:504,516,524,532`(whisper_mlx / whisper_cpp / whisper_turbo / qwen transformers 分支)返回 `(text, lang)` 元组,而正常路径返回 `TranscriptionResult`(544 行)。`/transcribe` 端点(858 行,response_model=TranscriptionResult)与 WS 端点(989 行 `result.text`)遇到这些分支必然失败 |
| H3 | **async 函数内嵌 `asyncio.run`** | `services/stt_server.py:511`(whisper_cpp transcribe)在 async 方法 `transcribe` 内调用 `asyncio.run(...)`——该方法是经事件循环 `await` 进入的,必然报 `RuntimeError: asyncio.run() cannot be called from a running event loop`。注:`:358`(加载)位于 `_load_model_sync`,非 qwen_native 引擎经 `run_in_executor` 调用(282 行)在 executor 线程执行,那里无运行中的事件循环,**不会**报错,但设计上仍是脆弱模式 |
| H4 | **时间戳功能必然坏(aligner 悬空引用)** | `services/stt_server.py:246` `self._aligner = None`;`load()`(289-290 行)只记 warning 却设 `_aligner_loaded=True`;`_generate_timestamps`(580 行)`self._aligner.align(...)` 必然 AttributeError,且被 `except` 静默吞掉(609-610 行) |
| H5 | **Python 客户端 `client/app.py` 与 `client/network.py` 接口严重脱节,运行即崩** | `client/app.py`(run_client.py:19 的入口)调用了 `network.py` 中不存在的方法:`self.stt.get_models()`(app.py:74,90)、`self.stt.transcribe()`(app.py:179)、`self.stt.get_model_status()`(app.py:107)、`self.llm.get_models()`(app.py:121)、`self.llm.process()`(app.py:184)。而 `network.py` 的 `SttClient` 只提供 `fetch_models/switch_model/poll_model_loading_status/send_audio/stream_audio`(37-603 行),`LlmClient` 只提供 `fetch_models/switch_model/load_prompt/save_prompt`(605-760 行)——没有 `transcribe`、`process`、`get_models`。`run_client.py` 启动后首次触发 `_connect`(app.py:72)即 AttributeError |

### 3.2 🟠 中优先级

| # | 问题 | 证据 |
|---|------|------|
| M1 | **"流式"实为伪流式**:WS 端点先收完所有音频再一次性转写 | `services/stt_server.py:966-984`;客户端 `client/network.py:388-397` 也是"录完再发"。与 README 宣称的"录音期间实时传输/低延迟"不符 |
| M2 | **LLM 访问双路径并存且自相矛盾**:`client/app.py:41` 把 `LlmClient` 指向 `http://{host}:6545`(LLM 服务直连),但 `LlmClient` 内部请求的路径是 `/llm/models`、`/llm/prompt` 等(`client/network.py:639,690,724,755`);而 LLM 服务(6545)的路由是 `/models`、`/process`、`/prompt`(`services/llm_server.py:371-423`),**没有 `/llm/*` 前缀**——`LlmClient` 的所有请求必然 404。同时 `SttClient` 又用 6544 的转发层(`network.py:573` `/llm/enabled`),形成两套互相矛盾的路径。LLM 直连应请求 `/process`、配置管理应请求 `/llm/*`(6544 转发)或 `/models`(6545 直连),需二选一并统一 |
| M3 | **LLM 端口 6545 硬编码** | `client/app.py:41` `LlmClient(f"http://{self.server_host}:6545")`;STT 端口 6544 硬编码于 `gui/src-tauri/src/stt.rs:51` |
| M4 | **死代码/新旧并存** | `server/llm_engine.py:14,137` 的 LLMEngine/LLMManager 与 `services/llm_server.py:97` 重复且无外部引用;`client/gui.py` 仅被 `VoiceInputFramework.spec:4` 引用;`llm_postprocessing/evaluator.py` 只被死代码引用 |
| M5 | **协议层死代码** | `shared/protocol.py` 的 StreamRequest/StreamResponse 仅 `shared/__init__.py` 导出,WS 两端都用裸 JSON dict,协议类零使用 |
| M6 | **CORS 配置无效组合** | `services/stt_server.py:649-650` `allow_origins=["*"]` + `allow_credentials=True` 是浏览器明确拒绝的组合 |
| M7 | **错误处理过于粗糙** | 多为 `except: return {"error": str(e)}`(如 `stt_server.py:733-735`),吞掉堆栈、返回字符串错误而非结构化 ErrorResponse;`shared/data_types.py:45` 的 ErrorResponse 仅测试引用,`tests/test_models.py:54` 还在测试里重复定义 |
| M8 | **`tools/audio_transcriber_gui.py:19` 硬编码服务器名** | `"shifengmacbook-pro"` 是本机私有名,不可移植 |

### 3.3 🟡 低优先级

| # | 问题 | 证据 |
|---|------|------|
| L1 | 魔法数字/常量散落:超时、轮询、16000 采样率 | `client/network.py:25-34`、`services/stt_server.py:43-45,477` |
| L2 | `stt_server.py:534-552` 的公共返回路径(含时间戳)仅 `qwen_asr_mlx_native` 分支可达——`else` 分支内各引擎(whisper_mlx/whisper_cpp/whisper_turbo/qwen)全部提前 `return` 元组,导致该段对它们不可达,时间戳功能也仅对 qwen_native 生效;单文件 40KB(FastAPI app 640 行 + STTEngine 236 行 + WS 端点)过大,建议拆分模块 |
| L3 | CI lint 不阻塞:`|| true` | `.github/workflows/ci.yml:51-52,73-77` |
| L4 | 版本号三处不一致:`pyproject.toml` 2.0.1、`CHANGELOG.md` 2.0.10、`client/__init__.py:7` 2.0.0;`[project.scripts]` 的 `voice-server` 指向 `services.stt_server:main` 而 README 用 `python -m services.stt_server`,需对齐 |
| L5 | `client/tests/` 不在 pytest testpaths,测试分散两处 | `pytest.ini` testpaths = tests |

## 四、修改建议

### 4.1 建议立即处理(对应 H1–H5)

1. **删除或重写失效入口**
   - 删除 `vif-run.py`,或在其中改为 `from services.stt_server import main`。
   - 同步修正 `deploy/launchd.plist:12` 与 `deploy/voice-input-framework.service:13` 的 `server.api` 引用为 `services.stt_server`。
   - 删除 `server/config.py`、`server/llm_engine.py`(若确认无引用),保留 `server/models/` 或将其上移为 `services/models/`。

2. **统一 `transcribe` 返回类型**
   - 让 whisper_mlx / whisper_cpp / whisper_turbo / qwen transformers 分支全部构造并返回 `TranscriptionResult`,与正常路径一致。
   - 为每种引擎补一个单元测试,断言返回类型。

3. **消除 `asyncio.run` 嵌套**
   - 将 511 行的同步调用改为 `await loop.run_in_executor(None, ...)`(与主模型加载方式 `stt_server.py:282` 一致),或让 whisper.cpp 引擎暴露 async 接口;同时清理 358 行"executor 内再开事件循环"的脆弱模式(把 `WhisperCppEngine.load()` 改为同步方法)。

4. **修复或移除时间戳功能**
   - 要么实现真正的 ForcedAligner 加载(设置 `self._aligner`),要么删除 `_generate_timestamps` 相关代码与 `_aligner_loaded` 标志,并在 `load(load_aligner=True)` 时返回失败而不是假成功。

5. **修复 Python 客户端接口脱节(最关键,H5)**
   - 对照 `client/network.py` 的实际 API(`fetch_models`/`switch_model`/`poll_model_loading_status`/`send_audio`/`stream_audio`)重写 `client/app.py` 的 `_connect`/`_fetch_models`/`_poll_model_loading`/`_process_audio`/`_fetch_llm_models`,或为 `SttClient`/`LlmClient` 补齐 `transcribe`/`process`/`get_models`/`get_model_status` 方法——二选一,必须让 `run_client.py` 启动路径上的每个调用都能解析。
   - 为 `run_client.py` 启动链路补一个冒烟测试(可 mock network),防止再次脱节。

### 4.2 建议中期处理(对应 M1–M8)

5. **实现真流式识别** 或 **改文档**:如果当前模型(MLX 非流式)无法支持边录边转,应把 `/ws/stream` 语义改为"分块上传 + 结束后转写",更新 README 措辞;若要做真流式,需要引入支持流式的引擎(如 Whisper streaming / silero vad 分段)。
6. **统一 LLM 访问路径(对应 M2)**:二选一——(a) `LlmClient` 直连 6545,但请求路径改为 LLM 服务真实路由(`/process`、`/models`、`/models/select`、`/prompt`);(b) `LlmClient` 改连 6544 走 STT 转发层(`/llm/*`),与 `SttClient` 的 `/llm/enabled` 一致。当前 app.py 指向 6545 却请求 `/llm/*` 的组合必然 404,必须先统一再谈是否移除转发层。
7. **端口集中配置**:端口、超时、采样率等收进 `shared/config.py` 或环境变量(已有 `VIF_LLM_PORT` 等),消除 `client/app.py:41`、`gui/.../stt.rs:51` 的硬编码;`tools/audio_transcriber_gui.py:19` 改默认值或读配置。
8. **清理死代码**:删除 `client/gui.py`、`shared/protocol.py`(或让 WS 两端真正使用协议类)、`llm_postprocessing/`(或接入 llm_server)。
9. **修正 CORS**:`allow_origins=["*"]` 时去掉 `allow_credentials=True`;若需要凭据则显式列出 origin。
10. **统一错误模型**:所有端点返回 `ErrorResponse`(或 pydantic 错误模型),异常处理记日志后返回结构化错误。

### 4.3 建议长期处理(对应 L1–L5)

11. **拆分 `stt_server.py`**:FastAPI 路由层 / STTEngine / WS 会话处理分文件;魔法数字提取为常量或配置。
12. **让 lint 真正生效**:CI 去掉 `|| true`,跑 `ruff check` 失败即红;格式化统一用 black。
13. **统一测试布局**:把 `client/tests/` 并入 `tests/` 或加入 testpaths;删除 `scripts/test_services.py`(被 `tests/test_e2e.py` 取代);为 `transcribe` 返回类型、WS 协议、LLM 代理各补单测。
14. **版本号对齐**:`pyproject.toml`、`CHANGELOG.md`、`VoiceInputFramework.spec` 的版本一致化。

## 五、总结

项目架构方向正确(双服务分离解决依赖冲突),`services/` 层是稳定核心;但当前存在**客户端接口脱节(run_client.py 入口不可用)**、**迁移未完成导致的死代码与失效入口**、**STT 引擎多分支返回类型不一致**、**LLM 访问双路径自相矛盾**、**伪流式与文档不符**五处主要风险。建议按 4.1 → 4.2 → 4.3 顺序推进,先修 H1–H5 五个必然出错点(其中 H5 客户端接口脱节优先级最高,它让 `run_client.py` 完全不可用),再做结构清理。
