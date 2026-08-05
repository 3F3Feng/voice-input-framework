# Rust 客户端(gui/)等价性验证清单(第 3 层)

> 背景:`gui/src-tauri` 原本无自动化测试;本次 review 修复主要涉及 Python
> 服务端/客户端。Rust 客户端仅 `stt.rs` 的 6544 端口硬编码经确认是兜底默认
> (调用方总传完整 URL),未改动逻辑。
>
> **2026-08-03 更新**:新增 `gui/stt-logic-tests` 轻量测试 crate,可在
> **Linux/macOS/Windows 任意平台**运行(不链接 tauri,无需 webkit/gtk 系统库),
> 覆盖 `stt.rs` 纯逻辑:URL 构造、StreamEvent 协议序列化、WS URL 派生。
> 已在 CI 新增 `rust-logic` job(`cargo test`,ubuntu-latest)。
>
> **2026-08-03 更新 2**:完整 Tauri crate 已在 **Linux 上 cargo check + cargo test
> 通过**(编译/链接/运行)。需要的系统 dev 包:`pkg-config`、`libwebkit2gtk-4.1-dev`、
> `libgtk-3-dev`、`libsoup-3.0-dev`、`libjavascriptcoregtk-4.1-dev`、`libasound2-dev`
> (cpal)、`libxdo-dev`(enigo)。`reqwest` 已切到 rustls,**无需 libssl-dev**。

## 自动化测试(已就绪,Linux 可跑)

```bash
cd gui/stt-logic-tests && cargo test
# 9 passed:SttClient::new URL 构造 / StreamEvent JSON 序列化 / WS URL 派生
```

- 通过 `#[path = "../../src-tauri/src/stt.rs"]` 复用生产源码(单一来源,不复制)
- 依赖用 rustls 替代 openssl,无 root 环境可编译
- 为支持该测试,`stt.rs` 的 `tauri::async_runtime::spawn` 改为功能等价的 `tokio::spawn`
  (tauri 2 底层即 tokio runtime),使 stt.rs 彻底平台无关

## 手工验证清单(完整 Tauri 集成,需 macOS/Windows)

以下仍需要真实平台环境(录音设备、全局快捷键、托盘):

## 前置

- macOS(Apple Silicon)或 Windows
- `cd gui && npm install && npm run tauri dev`

## A. 服务端连通(与修复后服务对接)

| # | 验证项 | 预期 | 通过 |
|---|--------|------|------|
| A1 | 启动修复后 `services.stt_server`(6544)与 `llm_server`(6545) | 两服务 /health 返回 200 | ☐ |
| A2 | GUI 连接设置填入 host,确认模型列表加载 | 下拉框出现 `qwen_asr_mlx_native` 等模型 | ☐ |
| A3 | 切换模型 | 状态显示"加载中"→"已加载",无报错 | ☐ |
| A4 | `GET /models/status/{m}` 轮询正常(GUI 不卡死) | 模型加载状态刷新 | ☐ |

## B. 录音与转写(核心链路)

| # | 验证项 | 预期 | 通过 |
|---|--------|------|------|
| B1 | 按住快捷键说话→松手 | 悬浮胶囊出现/消失 | ☐ |
| B2 | 转写结果出现在输入框 | 文本正确,无 `error` 消息 | ☐ |
| B3 | WS 消息序列正常(`ready→config_ack→stt_result→result→done`) | GUI 日志面板可见完整序列 | ☐ |
| B4 | 无 `aligner_loaded`/`return_timestamps` 相关报错 | 日志无相关字段引用 | ☐ |

## C. LLM 后处理(6545 直连)

| # | 验证项 | 预期 | 通过 |
|---|--------|------|------|
| C1 | 开启 LLM 后处理,完成一次转写 | 结果经 LLM 优化(与关闭 LLM 时不同) | ☐ |
| C2 | GUI 的 LLM 模型列表/提示词管理 | 列表加载、提示词读写成功 | ☐ |
| C3 | LLM 服务未启动时优雅降级 | 返回原始文本,无崩溃 | ☐ |

## D. 平台功能

| # | 验证项 | 预期 | 通过 |
|---|--------|------|------|
| D1 | 全局快捷键在应用失焦时可用 | 热键触发录音 | ☐ |
| D2 | 系统托盘/最小化 | 最小化到托盘,热键仍可用 | ☐ |
| D3 | 自动更新检查 | 无报错(可离线失败) | ☐ |

## 备注

- 若后续要为 Rust 客户端加自动化测试,建议优先为 `stt.rs` 的
  `SttClient::new`(URL 构造)与消息解析逻辑补 `#[cfg(test)]` 单测;
  `audio.rs` 的音频格式转换也可单测。工作量:stt.rs 约 1-2 小时,
  audio.rs 约 1 小时。
