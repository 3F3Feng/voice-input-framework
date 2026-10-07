# 配置、网络与接口

平时用不着这一页：端口、模型、令牌在客户端的设置面板里都能改。这里是给自己管服务端、
把服务放在另一台机器上、或者想直接调接口的人看的。

## 服务端环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VIF_STT_PORT` | 6544 | STT 服务端口 |
| `VIF_STT_HOST` | 127.0.0.1 | STT 服务监听地址 |
| `VIF_STT_MODEL` | 按硬件 | 启动时加载的 STT 模型（注册名见 [models.md](models.md)）。不设时按[硬件配置表](../README.md#-这台机器会用哪套模型)选 |
| `VIF_LLM_PORT` | 6545 | LLM 服务端口 |
| `VIF_LLM_HOST` | 127.0.0.1 | LLM 服务监听地址；在 STT 服务里是转发目标地址 |
| `VIF_LLM_ENABLED` | 按硬件 | 是否启用 LLM 后处理。没设、也没在界面上开关过时按硬件定：后处理只能跑在 CPU 上的小机器默认关 |
| `VIF_LLM_MODEL` | 按硬件 | 默认 LLM 模型；填了另一个后端的模型名时忽略 |
| `VIF_LLM_BACKEND` | 自动 | LLM 推理后端：`mlx` 或 `llamacpp`。不设时 Apple 芯片用 MLX，其它平台用 llama.cpp |
| `VIF_LLM_CTX` | 8192 | llama.cpp 后端的上下文窗口（token） |
| `VIF_LLM_GPU_LAYERS` | 按硬件 | llama.cpp 后端放到显卡上的层数，-1 为全部，0 为只用 CPU。不设时全部放显卡，只有显存只够放识别模型的机器上是 0；装的是 CPU 版时没有作用 |
| `VIF_DEVICE` / `VIF_DTYPE` | 自动 | PyTorch 引擎（Whisper 系）用的设备 `cpu\|cuda\|mps\|xpu` 和精度 `float16\|bfloat16\|float32`；一般不用设 |
| `VIF_API_TOKEN` | 未设置 | 访问令牌。设了之后除 `/health` 外的请求都要带 `Authorization: Bearer <令牌>`（WebSocket 也可用 `?token=`）。STT 和 LLM 两个服务要设同一个值 |
| `VIF_CORS_ORIGINS` | 桌面客户端的几个来源 | 允许的跨域来源，逗号分隔。只有自己写网页调接口时才需要加 |
| `VIF_REQUEST_TIMEOUT` | 300.0 | 请求超时（秒） |
| `VIF_LOG_LEVEL` | INFO | 日志级别 |
| `HF_ENDPOINT` | HuggingFace 官方 | 模型下载源，例如 `https://hf-mirror.com`。客户端里是「模型下载源」 |

完整的模型元数据见 `shared/model_registry.py`（单一来源）。

## 让别的机器连过来

两个服务默认只绑回环地址 `127.0.0.1`，而且没设 `VIF_API_TOKEN` 时不做鉴权，所以默认不暴露到局域网。

**最省事的办法**：桌面客户端「设置 → 服务 → 手机直连（局域网）」打开开关（本地管理模式）。它会让
STT 服务监听局域网、生成随机令牌、重启服务，并显示对方要填的地址和令牌。

**自己管服务的话**：

```bash
export VIF_STT_HOST=0.0.0.0           # 允许局域网连接
export VIF_API_TOKEN=换成一串随机字符   # STT 和 LLM 两个服务设同一个值
uv run python -m services.stt_server
```

- 另一台电脑上的桌面客户端：「设置 → 服务」选「远程连接」，填地址和同一个令牌。
- LLM 服务可以继续只绑本机：后处理由 STT 服务转发。
- 桌面客户端和手机应用都不受 CORS 限制，不用配 `VIF_CORS_ORIGINS`。
- **不要**把端口直接映射到公网。出门在外用 [Tailscale](https://tailscale.com/) 这类加密隧道。
- 局域网里用 `http://` 时令牌是明文传输的，只在你信任的网络里这样用。

> 客户端与服务端默认用 `127.0.0.1` 而不是 `localhost`：Windows 上 `localhost` 会优先解析到 IPv6
> （`::1`），每次连接先等 IPv6 超时再回落 IPv4，要多等几秒。

## 客户端配置

桌面客户端的配置是应用数据目录下的 `config.json`，一般不需要手改，设置面板里都能改：

- macOS：`~/Library/Application Support/com.voiceinput.app/config.json`
- Windows：`%APPDATA%\com.voiceinput.app\config.json`
- Linux：`~/.local/share/com.voiceinput.app/config.json`

内容包括：服务模式（本地管理 / 远程）与地址端口、快捷键、识别语言、麦克风、LLM 开关、启动选项。
首次启动时会把旧 Python 客户端的 `~/.voice_input_config.json` 迁移过来。

客户端日志写在应用日志目录（macOS 是 `~/Library/Logs/com.voiceinput.app/voice-input.log`，超过 1 MB
轮转一份），「设置 → 日志」里可以一键打开。

「下载并安装」默认从本仓库克隆；github.com 访问慢时可以用环境变量 `VIF_REPO_URL` 换一个地址。

## HTTP / WebSocket 接口

### STT 服务（端口 6544）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查；带硬件、当前模型、加载 / 下载进度 |
| `/models` | GET | 可用模型列表 |
| `/models/select` | POST | 切换模型 |
| `/models/status/{model}` | GET | 查询模型加载状态 |
| `/transcribe` | POST | 转写一个音频文件 |
| `/ws/stream` | WebSocket | 边录边传；客户端声明支持时录音期间就分段转写，结束后返回全文 |
| `/vocabulary` | GET / PUT | 词表（热词和替换） |
| `/llm/health` | GET | LLM 服务的状态（转发） |
| `/llm/models` | GET | LLM 模型列表（转发） |
| `/llm/models/select` | POST | 切换 LLM 模型 |
| `/llm/prompt` | GET / PUT / DELETE | 获取 / 保存 / 恢复默认的 LLM 提示词 |
| `/llm/enabled` | GET / PUT | 获取 / 设置 LLM 开关 |
| `/diarize`、`/diarize/models` | POST / GET | 说话人分离（可选，`VIF_DIARIZE_ENABLED=false` 关闭） |

### LLM 服务（端口 6545）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查；带当前模型、跑在显卡还是 CPU 上、加载进度 |
| `/models` | GET | 可用模型列表 |
| `/models/select` | POST | 切换模型 |
| `/process` | POST | 处理一段文本 |
| `/prompt` | GET / PUT / DELETE | 获取 / 保存 / 恢复默认的提示词 |
