# 模型

这台机器默认用哪一套，见 [README](../README.md#-这台机器会用哪套模型) 里的表。这里是全部可选的模型、
那张表的细节，以及为什么这么选。

## 语音识别（STT）模型

下表的「注册名」就是 `VIF_STT_MODEL`、设置面板下拉框和 `/models/select` 里用的名字，
唯一来源是 `shared/model_registry.py`。

| 注册名 | 说明 | 内存 | 平台 |
|--------|------|------|------|
| `qwen_asr_mlx_native` | Qwen3-ASR-1.7B MLX 8bit，52 种语言 / 方言 | ~1GB | Apple 芯片（内存 ≥ 16GB 时的默认） |
| `qwen_asr_mlx_native_small` | Qwen3-ASR-0.6B MLX 4bit，更快 | ~0.5GB | Apple 芯片（内存 < 16GB 时的默认） |
| `whisper_mlx` | MLX Whisper Large V3 | ~3GB | Apple 芯片 |
| `whisper_mlx_turbo` | MLX Whisper Large V3 Turbo，快速且准确 | ~2GB | Apple 芯片 |
| `whisper_mlx_medium` | MLX Whisper Medium | ~1.5GB | Apple 芯片 |
| `whisper_mlx_small` | MLX Whisper Small，最快 | ~0.5GB | Apple 芯片 |
| `qwen_asr_small` | Qwen3-ASR-0.6B 8 位量化版（llama.cpp），中文和中英混说明显好于 Whisper，显卡 / CPU 都能跑 | ~1.3GB | 全平台（Windows / Linux 的默认） |
| `qwen_asr` | Qwen3-ASR-1.7B 8 位量化版（llama.cpp），中文最准 | ~3GB | 全平台（显存 ≥ 11GB 时的默认） |
| `whisper_tiny` | Whisper Tiny（transformers），最快、精度一般，适合低配 / 纯 CPU | ~0.3GB | 全平台 |
| `whisper_base` | Whisper Base（transformers），小机器上的默认 | ~0.5GB | 全平台 |
| `whisper_small` | Whisper Small（transformers），速度与精度折中 | ~1GB | 全平台 |
| `whisper_medium` | Whisper Medium（transformers），有独显时适用 | ~2.5GB | 全平台 |
| `whisper_turbo` | Whisper Large V3 Turbo（transformers），建议配 GPU | ~3GB | 全平台 |
| `whisper_cpp_base` / `whisper_cpp_large` | Whisper V3 via whisper.cpp | 1GB / 3GB | 需自行编译 `~/whisper.cpp` 并把模型放到 `~/.cache/whisper/` |

**为什么非 Apple 平台用量化版 Qwen3-ASR 而不是 Whisper**：同一批中文 / 中英混说录音，`whisper_base`
出繁体字和错字，量化版 Qwen3-ASR-0.6B 和 `whisper_turbo` 一样准、自带标点，而且快得多——M3 Max 的
显卡上 90 秒录音 2 秒出结果，只给 2 个 CPU 线程也只要 11.5 秒。它和半精度的原版权重比过 12 段
录音：9 段逐字相同，其余 3 段是标点和中英文之间空格的差别；权重小三分之一，识别服务也不再需要
加载 PyTorch。

## 按硬件配好的几套

不指定模型时，两个服务按**同一张表**挑（`shared/hardware_plan.py`，表本身在 README 里）。补充几点：

- 后处理跑在 CPU 上时一句话要等几秒，所以只有 ≥ 12 线程且内存 ≥ 16 GB 的机器默认开着，其余默认关
  （可以自己打开）。「线程」是系统报的逻辑处理器数。
- 显卡看的是 **llama.cpp 认得出什么**：显卡在、但装的是 CPU 版的 llama.cpp（或者驱动不可用）时，
  按没有显卡来选。集成显卡不算。
- 8 GB 的显卡落在第二档（1.3 + 3.8 = 5.1 GB），而不是勉强塞进 1.7B——Windows 的桌面自己要占
  0.5–1.5 GB 显存。想换可以手动选，「设置 → 服务」里会提示模型是不是跑在了 CPU 上。
- 显存只够放识别模型的那一档（2.5–5.5 GB），后处理一层都不往显卡上放，免得两个模型抢显存。
- `VIF_STT_MODEL` / `VIF_LLM_MODEL` 可以强制指定；在设置里手动切换过的模型会被记住，优先于这张表。

**哪些是实测的**：Apple 芯片两档；llama.cpp 上的每个模型（M3 Max 的 Metal 和纯 CPU）；NVIDIA 上
CUDA 版能装上并认出显卡（GTX 1070 Ti）。**还没实测的**：各档的显存门槛（按模型大小算的，不是在
每种显卡上量出来的）；CUDA / Vulkan 上的真实速度和显存占用；AMD / Intel Arc 的真卡（Vulkan 只在
CI 的软件渲染上跑过）；Intel Mac（按「没有独立显卡」处理）。

## LLM 后处理模型

LLM 服务按平台挑推理后端：**Apple 芯片用 MLX**，**Windows / Linux 用 llama.cpp** 跑 GGUF 模型。
两个后端的模型不通用，模型列表只列当前后端的。想在 Mac 上也用 llama.cpp，设
`VIF_LLM_BACKEND=llamacpp` 并 `uv sync --extra llm-cpp`。

MLX 后端（Apple 芯片）：

| 模型 | 内存占用 | 特点 |
|------|----------|------|
| Gemma-4-E4B | ~6.4GB | **默认（内存 ≥ 16GB）**，Google QAT 4bit；格式整理最好（口述列举 → 编号列表、换话题分段、数字） |
| Gemma-4-E2B | ~4GB | **默认（内存 < 16GB）**，Google QAT 4bit；最快，中文、英文、中英混说都稳 |
| Qwen3.5-4B-OptiQ | ~4GB | 旧默认；默认模型加载失败时自动退回它 |
| Qwen3.5-4B-MLX | ~3GB | 同一模型的普通 4bit 量化，内存更省 |
| Qwen3.5-2B-OptiQ / Qwen3-0.6B / Qwen3-1.7B | ~2GB / ~0.5GB / ~1.5GB | 更小，但实测多数句子原样照抄，不推荐 |

llama.cpp 后端（其它平台，4bit 量化，首次使用时下载到 HuggingFace 缓存）：

| 模型 | 下载大小 | 特点 |
|------|----------|------|
| Gemma-4-E4B-GGUF | ~5.2GB | 显存 ≥ 11 GB 时的**默认**；格式整理 18/18，M3 Max 的 Metal 上中位延迟 0.44 秒 |
| Gemma-4-E2B-GGUF | ~3.4GB | 其余情况的**默认**，Google 官方 QAT q4_0；中文、英文、中英混说都稳 |
| Qwen3.5-2B-GGUF | ~1.3GB | 旧默认；实测多数句子原样照抄，默认模型加载失败时退回它 |
| Qwen3.5-0.8B-GGUF | ~0.5GB | 最快，但填充词和改口常常原样留着 |
| Qwen3.5-4B-GGUF | ~2.7GB | 纯 CPU 上一段长文要等十几秒以上 |

**默认模型是这样选的**：用中文、英文、两种方向的中英混说、改口、夹术语、「帮我写一首诗」等 8 类输入
× 默认提示词和三个预设共 64 例自动检查（`tools/llm_prompt_eval.py`），再量延迟和内存。
Gemma-4-E2B 的 QAT 版在 MLX 和 llama.cpp 上都只有 1 例不合格，延迟约为旧默认的一半。

## llama.cpp 装哪个版本

非 Apple 平台上，量化版 Qwen3-ASR 和 LLM 后处理都跑在 llama.cpp 上（`llama-cpp-python` 0.3.25 以上，
更早的版本不认 Gemma 4）。`setup-env` 默认就会装，装的是官方**预编译包**，不需要编译器，并且
**有显卡就装显卡版**——默认模型在纯 CPU 上一句话要等好几秒，CPU 版只是兜底：

| 显卡 | 装哪个 | 说明 |
|------|--------|------|
| NVIDIA | CUDA 版 | 最快。约 500 MB（Windows）/ 1.8 GB（Linux）。包里不带 CUDA 运行库，用的是 PyTorch CUDA 版带的那份，所以只需要显卡驱动 |
| AMD / Intel Arc | Vulkan 版 | 约 40 MB，只需要显卡驱动；也能在 NVIDIA 上跑，比 CUDA 慢一些 |
| 没有独立显卡 | CPU 版 | 兜底，慢 |

- 装完脚本会真的加载一次，看 llama.cpp 认不认得出显卡；认不出（驱动太旧、缺运行库）就依次退回
  CUDA → Vulkan → CPU，并在最后说清楚落在了哪一个。
- `--llm-backend cuda|vulkan|cpu`（PowerShell 是 `-LlmBackend`）可以手动指定，「更新服务」会记住手动的
  选择；自动选的每次更新都重新探测，装了新驱动之后能自己升到显卡版。
- Windows 上预编译包要 **Visual C++ 运行库（`msvcp140.dll`）不早于 14.40**，包里不带。进程加载到旧的
  那份时三种版本都装得上，但一调用就崩（`access violation reading 0x0000000000000000`）。最常见的
  来源不是系统，而是 Python 自己：Anaconda 的 Python 目录里自带一份旧的，排在系统那份前面。所以
  `setup-env.ps1` 用 uv 自己管理的 Python 建环境（没装过会自动下载，约 20 MB；以前建在别的 Python 上的
  `.venv` 会重建一次）。系统那份真的太旧时脚本会说明，更新用
  `winget install --id Microsoft.VCRedist.2015+.x64 -e`。
- `--no-llm`（PowerShell 是 `-NoLlm`）不装 llama.cpp，那样只剩 Whisper 系的识别模型，也没有后处理。
  更早建的环境里没有 llama.cpp 时，界面上的后处理开关会置灰，旁边有「安装 llama.cpp」。
- 运行时如果模型在显卡上放不下（显存不够），服务会退回 CPU，并在「设置 → 服务」和 LLM 开关下面提示。
- PyTorch 的后端另外选：`scripts/setup-env.sh --backend cpu|cuda|rocm|xpu|mlx`（PowerShell 是
  `-Backend cpu|cuda|xpu`）。

显卡版的选择和回退逻辑在 CI 里验证过（Vulkan 版用软件渲染真的跑了一次推理和一次语音识别；假装有
N 卡时 CUDA 版加载不了会退回）。CUDA 版在真的 NVIDIA 显卡上确认过能装上并认出显卡（GTX 1070 Ti），
速度和显存占用还没量过。
