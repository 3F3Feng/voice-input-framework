# 开发

怎么从源码跑起来、怎么构建、怎么测。用户向的说明在 [README](../README.md)。

## 结构

```
gui/            桌面客户端:Tauri 2 + Vue 3 + TypeScript
  src/            界面(App.vue、首启向导 Onboarding.vue、本机安装 LocalSetup.vue、悬浮胶囊)
  src-tauri/src/  Rust 后端
services/       服务端:stt_server.py(6544)、llm_server.py(6545)和各个识别引擎
shared/         两个服务共用:模型注册表、硬件配置表、鉴权、中英文文案
scripts/        建环境(setup-env.sh / .ps1)、macOS 签名构建、集成测试
mobile/         Android 输入法和 iOS 键盘,见 mobile/README.md
tests/          Python 测试
client/         早期的 Python 客户端(见最后一节)
```

客户端的 Rust 后端按职责分文件：

| 文件 | 管什么 |
|------|--------|
| `lib.rs` | 命令注册、应用生命周期 |
| `audio.rs` / `stt.rs` | 录音（cpal）、WebSocket 边录边传和分段转写 |
| `hotkey.rs` / `input.rs` | 全局快捷键（macOS 用 CGEventTap）、把结果输入到光标处 |
| `server_manager.rs` / `heartbeat.rs` | 启停本地 STT / LLM 服务、认领已在跑的服务、健康检查 |
| `local_setup.rs` / `env_check.rs` / `service_update.rs` | 一键下载并建环境、环境体检、更新服务和切换分支 |
| `indicator.rs` / `tray.rs` | 悬浮胶囊、托盘菜单 |
| `update.rs` / `crash.rs` / `log.rs` | 客户端自动更新、上次异常退出的提示、日志 |
| `lan_share.rs` / `mobile_pairing.rs` | 手机直连（局域网）、配对二维码 |
| `config.rs` / `history.rs` / `permissions.rs` / `i18n.rs` | 配置、识别历史、macOS 权限、中英文文案 |

数据流：客户端录音 → WebSocket 发给 STT 服务 → 识别 →（开着的话）STT 服务把文字转给 LLM 服务整理 →
结果回到客户端 → 输入到光标处。客户端只和 STT 服务说话，LLM 的接口都由 STT 服务转发。

## 从源码运行

服务端按 README 的「自己在终端里建服务端」建好环境（开发时加 `--dev` / `-Dev` 装上测试依赖）。客户端：

```bash
cd gui
npm install
npm run tauri dev
```

## 构建客户端

**macOS 走脚本**，它会用本机证书签名、校验产物，并安装到 `/Applications`：

```bash
scripts/build-macos.sh --install
```

签名不是可选项：macOS 的隐私权限（TCC）按**代码签名身份**记录授权，不带证书构建时 Tauri 只做 ad-hoc
签名、身份每次构建都变，后果是麦克风、输入监控、辅助功能三项权限每构建一次就要重新授予一遍。
脚本还会校验 hardened runtime 需要的 entitlement 确实进了产物（缺了麦克风会静默失效）。完整说明见
[macos-signed-build.md](macos-signed-build.md)。

其它平台（或只想编译不签名）：

```bash
cd gui
npm install
npm run tauri build
```

**版本号**唯一的来源是 `gui/src-tauri/Cargo.toml`（`tauri.conf.json` 不写 `version`，Tauri 缺省回落到它）；
发版时 `client/__init__.py` 和 `pyproject.toml` 跟着改。「设置 → 关于」里的构建 ID 是每次构建现生成的
UUID，用来分辨本地反复构建出来的产物。

## 测试

提交前跑这一组（CI 跑的也是它们，外加在干净的 Windows / Linux 机器上真跑一遍建环境脚本）：

```bash
cd gui/src-tauri && cargo fmt --check && cargo clippy --all-targets && cargo test
cd gui/stt-logic-tests && cargo test
cd gui && ./node_modules/.bin/vue-tsc --noEmit
uv run --with pytest --with pytest-asyncio python -m pytest tests -q -m "not integration"
uvx black --check services shared tests && uvx ruff check services shared tests --select F
```

- `gui/stt-logic-tests` 是不依赖 Tauri 系统库的逻辑测试，任何平台都能跑；完整的 `gui/src-tauri`
  在 Linux 上需要 webkit2gtk / gtk / alsa / xdo 的开发包。
- `tests/test_contract.py` 用 TestClient 断言 HTTP / WebSocket 端点的契约，不需要模型。
- 真实模型的集成测试（要模型和显卡环境）：`bash scripts/run_integration.sh`，它启动 STT 和 LLM 服务，
  跑 `tests/test_e2e.py` 和 `tests/test_api_endpoints.py`。
- 改了 LLM 的提示词或默认模型：用 `tools/llm_prompt_eval.py` 和 `tools/llm_format_eval.py` 对着一个测试用的
  LLM 服务跑一遍。
- `scripts/pre-commit.sh` 是提交钩子（rustfmt + clippy），装到 `.git/hooks/pre-commit` 用。

手动验证的清单：桌面客户端见 [rust-client-verification.md](rust-client-verification.md)，
找人帮忙测试用 [testing.md](testing.md)。还没在真机上验证过的东西记在
[UX_REVIEW.md](UX_REVIEW.md) 第 5.2 节。

## 早期的 Python 客户端

`client/` 和 `run_client.py` 是早期的 Python 客户端，不再加新功能（本地服务管理、权限引导、悬浮胶囊、
自动更新都只在 Tauri 客户端里有）。
