# 加速与更大模型：GPU / NPU / 35B 实测报告

日期：2026-10-24 · 机器：ThinkPad（Core 7 240H / 31.5 GB / Intel iGPU）· 参考设备：MT200（IQ-9075，200 TOPS）

---

## 1. 结论

| 诉求 | 结果 | 依据 |
|---|---|---|
| **把 GPU 用起来** | ✅ 已用，Vulkan 后端 | 1.7B：prefill 68.5→**154.2 tok/s**（2.25×），decode 18.34→**28.98 tok/s**（1.58×）；dsh 工具闭环在 GPU 后端下 3/3 通过 |
| **把 NPU 用起来** | ✅ **MT200 的 NPU 可用，已跑通并曝光为 OpenAI 接口** | QNN 官方校验：`Unit Test on the backend DSP: Passed`（HTP V73）；`deepseek-7b` 在 NPU 上 **12.09 tok/s**（188 token，TTFT 1.11s）；已封装 `http://10.42.0.81:8090/v1` 并从 ThinkPad 调通 |
| 本机（ThinkPad）的 NPU | ❌ 无此设备 | 设备树里只有 `Intel(R) GNA Scoring Accelerator`（声音唤醒用） |
| **35B 以上** | ✅ 能跑，⚠️ 慢 | `Qwen3.5-35B-A3B`（35B 总参 / 3B 激活）已跑通 dsh 工具闭环 3/3，单回合 44–90 s，decode 5.7–8.6 tok/s |

一句话：**ThinkPad 用 iGPU 跑 1.7B（17 s/回合）或 35B-A3B（60 s/回合）；MT200 的 200 TOPS NPU 用 QNN/Genie 跑 7B @ 12 tok/s，已封装成 OpenAI 兼容接口，但模型包 context 只有 512 token，装不下 dsh 的 2.6K 提示——这一步需要重新导出模型（见 §4.4）。**

---

## 2. 硬件边界（先算清，再谈方案）

| 项 | 实测 | 影响 |
|---|---|---|
| CPU | Intel Core 7 240H，10 核 16 线程 | 可用线程留 6–8 给 llama.cpp |
| 内存 | 2×16 GB DDR5-5600（理论 ~89.6 GB/s） | dense 大模型的 decode 天花板由它决定 |
| 显卡 | Intel iGPU，Vulkan 报 `16125 MiB` 堆（共享内存） | 权重+KV+compute 必须一起塞进 ~15.7 GiB |
| NPU | 无（仅 GNA） | 任何"NPU 加速"方案在此机不成立 |
| commit limit | 37,372 MB（32 GB RAM + 5 GB pagefile），实测已用 28–32 GB | GPU 缓冲区吃 commit，全量卸载 16.6 GB 必然失败 |
| 系统 | Windows 11 26200 | Vulkan/DX12 可用，无 CUDA |

**关键推论**：iGPU 的"16 GB 显存"是系统内存的一部分，它不增加带宽，只提供并行算力。所以：

- **prefill 受益大**（算力密集）→ 实测 2.25×；
- **decode 受益小**（带宽密集）→ 实测 1.58×，且模型越大、收益越接近 1×。

---

## 3. GPU 加速（iGPU / Vulkan）

### 3.1 1.7B 对照组（同一 `bench.py`，6036 token prefill + 256 token 生成）

| 配置 | prefill tok/s | decode tok/s |
|---|---|---|
| CPU `-t 4` | 68.5 | 18.34 |
| **iGPU Vulkan `-ngl 99`** | **154.2** | **28.98** |
| 提升 | **2.25×** | **1.58×** |

### 3.2 GPU 后端下 dsh 工具闭环仍成立

换后端不是"推理跑通就行"，必须验 Agent 闭环：

```
accept20.py 3（DSH_PROFILE=thinkpad-local，后端=Vulkan iGPU）
→ 3/3 PASS，tool_call → tool_result → final 完整，答案对上真实数值
```

### 3.3 GPU 后端下的 dsh 工具闭环（20 轮）

同一套验收器（`accept20.py`），后端换成 Vulkan iGPU，跑满 20 轮：

| 指标 | CPU 后端（20 轮，昨夜） | **iGPU/Vulkan（20 轮，今早）** |
|---|---|---|
| 工具闭环 | 20/20 | **20/20**（每轮 1 次 tool_call、1 条 completed） |
| 答案接地 | 20/20 | 19/20（1 轮把 141.8 GB 写成 1.42 GB，100× 单位错） |
| 回合均值 / 中位数 | 17.2 s / 16.4 s | 22.4 s / 21.4 s |
| 启动（不计入回合） | 54.7 s | 51.0 s |

**"GPU 更快"这个说法要限定条件**。同一时段补做的 5 轮 CPU 对照（22.0 s 均值 / 18.0 s 中位数）与 GPU 的 22.4 s / 21.4 s **在噪声范围内不可区分**。原因在 agent 语境下的请求形态：

| 场景（1.7B，2630 token 上下文） | CPU `-t 4` | iGPU Vulkan | 差异 |
|---|---|---|---|
| 冷 prefill（新会话首次请求） | 19,475 ms / 135 tok/s | **7,653 ms / 343 tok/s** | **2.55×** |
| decode | 19.96 tok/s | **27.31 tok/s** | 1.37× |
| 部分前缀命中（+10 token，模拟 tool_result 追加） | 308–548 ms | **224–267 ms** | ~1.4× |
| 完整前缀命中（同 prompt 复跑） | 52.6 ms | 38.0 ms | 持平 |

dsh 每轮绝大多数时间花在**前缀已被缓存**的两三次请求上（实测每轮 prefill 只有 146–209 token），GPU 那 2.5× 的冷 prefill 优势在稳态回合里被缓存直接吃掉，剩下的差距是 dsh 插件开销与系统负载。**结论：GPU 该开（冷启动、长 prompt、35B 都受益，且从不更慢），但不要指望它把稳态回合整体提速。**


### 3.4 为什么不用 SYCL

`llama-b11836-bin-win-sycl-x64.zip`（155 MB）在 GitHub 上只给到 ~70 KB/s，30 分钟拿不完，且与模型下载抢带宽。Vulkan 已经拿到 2.25×/1.58×，SYCL 留作后续单变量实验（理论上 Arc 的 prefill 还能再高一些）。

---

## 4. NPU 调查（两条线，都有证据）

### 4.1 ThinkPad：设备不存在

```
Get-PnpDevice | ? FriendlyName -match 'NPU|AI Boost|Neural|VPU'
→ 仅 Intel(R) GNA Scoring Accelerator（老一代声音唤醒加速器）
Win32_VideoController / PnP Display 类 → 只有 Intel(R) Graphics
```

GNA 没有主流 LLM 运行时（ONNX-Runtime 也不把它当执行后端），且算力面向关键词唤醒。**结论：此机无 NPU 可用，不是驱动没装。**

### 4.2 MT200：NPU 可用（QNN/Genie 路径），已实测 12 tok/s

先纠正一个我此前的错误结论：**"NPU 不可用"是错的**。MT200 上装的是完整 **Qualcomm QNN/AI-RT 2.39**（`/opt/qnn/sdk`、`/data/sdk/bin/genie-t2t-run`、签名 skel `libQnnHtpV73Skel.so`），走 QNN 这条路 NPU 是活的。

**证据 1 — 官方校验器通过**（不是日志猜测）：

```
$ qnn-platform-validator --backend dsp --testBackend
Loading sample stub: libQnnHtpV73CalculatorStub.so
Success in executing the sum function
Unit Test on the backend DSP: Passed.
QNN is supported for backend DSP on the device.
```

**证据 2 — 7B 模型真的在 NPU 上出字**（`deepseek-7b` QNN bundle，Genie 1.13）：

```
$ genie-t2t-run -c deepseek-7b/deepseek-7b-htp_sharing.json --prompt_file /tmp/ds7b.txt
Using libGenie.so version 1.13.0
Allocated total size = 387190784 across 14 buffers
[BEGIN]: 我是一个智能 AI 机器人，可以回答各种问题和提供帮助。[END]
```

**证据 3 — QNN profile 给出的真实性能**（188 token 的一次生成）：

| 指标 | 值 |
|---|---|
| 模型初始化 | 7.11 s（每次进程启动一次） |
| prompt 处理（28 token） | 25.2 tok/s |
| TTFT | 1.11 s |
| **生成 188 token** | **12.09 tok/s** |
| 释放 | 0.33 s |

**证据 4 — 已封装成 OpenAI 兼容接口，从 ThinkPad 直连调通**：

```bash
curl http://10.42.0.81:8090/v1/models
{"data":[{"id":"deepseek-7b-npu","meta":{"backend":"QnnHtp","device":"MT200 Hexagon V73"}}]}

POST /v1/chat/completions → 11.1 s，返回通顺中文
```

桥接脚本：`C:\AI\scripts\npu-openai-bridge.py`（部署到 MT200 的 `/data/local/tmp/`，`setsid python3 ... &` 常驻，端口 8090）。它把 OpenAI messages 转成 DeepSeek 对话模板，调用 `genie-t2t-run`，再把 `[BEGIN]…[END]` 解析回 OpenAI 响应。因为 Genie 是一次性 CLI，**每个请求要付 7 s 初始化**。

### 4.3 为什么 llama.cpp 的 ggml-hexagon 仍然不通

MeiG 版 llama.cpp **自带** Hexagon 后端（`libggml-hexagon.so` + `libggml-htp-v68…v81.so`），设备节点与守护进程都正常，但开会话一律失败：

```
E ggml-hex: failed to open session 0 : error 0x80000406
```

原因：llama.cpp 加载的是它**自己编译的未签名 HTP skel**，安全启动设备只允许高通签名的 PD；而 QNN 用的 `libQnnHtpV73Skel.so` + `.cat` 是签名过的，所以 QNN 通、ggml-hex 不通。**这是签名链路问题，不是 NPU 或 FastRPC 的问题。**

顺带排除的两个死路（都实测过）：
- `deepseek-qwen-1.5b` 包：KV 融成单个 **313 MB** dmabuf，超过本机可映射上限（CMA 167 MB），`memRegister ERROR(8003)`；改 `use-mmap`/`mem_type`/`ctx 128` 均无效（尺寸由编译进图的固定形状决定）。
- `deepseek-qwen-7b` 包：能加载但输出退化成重复 token —— 其 tokenizer 词表 151,643 与图配置 `n-vocab 152064` 不匹配，属模型包本身的问题。

### 4.5 榨干记录（每一项都实测）

| 尝试 | prefill tok/s | decode tok/s | 结论 |
|---|---|---|---|
| 原始 `burst` | 183.9 | 12.24 | 基线 |
| `+ vtcm_mb: 8` | 184.1 | 12.32 | 无变化（VTCM 由图的编译期决定） |
| `+ num_hvx_threads: 4` | 184.1 | 12.35 | 无变化 |
| `vtcm 8 + hvx 4` | 184.3 | 12.25 | 无变化 |
| `sustained_high_performance` | **161.4** | **9.29** | ❌ 比 burst 差 25%，时钟策略不适合突发推理 |
| 两进程并发 | — | — | ❌ DSP 串行：整机 load 冲到 27，sshd 一度失联，吞吐不叠加 |
| `--save` / `--restore` | — | — | save 成功，**restore 失败**（`Failed to query`）→ 每次请求仍要付 7–8.5 s 初始化 |
| **context 512 → 1024** | — | — | ✅ **成功**，可用上下文翻倍（模型配置里 `context_size: 1024`） |
| context 2048 | — | — | ❌ 加载失败，与 `context_size: 1024` 一致 |
| 长 prompt（681 token） | **607.9** | 12.24 | prefill 随长度上升：固定开销被摊薄，681 token 时 608 tok/s |

**改完 context 后**，桥接层能吃下约 600 token 的 prompt（此前 ~500 就 `Context Size was exceeded`），一次问答 9.2 s 返回通顺中文。

### 4.6 "用了多少 TOPS"——按实测算

单序列自回归推理是访存受限，不是算力受限，所以峰值 200 TOPS 用不上。按每 token ≈ 2×激活参数 FLOP 折算：

| 阶段 | 实测速率 | 折合算力（按 2.4B 激活，V2-Lite 类） | 折合算力（按 7B dense） |
|---|---|---|---|
| decode | 12.24 tok/s | ≈ **0.06 TOPS** | ≈ **0.17 TOPS** |
| prefill（681 token） | 607.9 tok/s | ≈ **2.9 TOPS** | ≈ **8.5 TOPS** |

也就是说：**decode 只用了峰值的 0.03%–0.09%，prefill 用了 1.5%–4.3%**（200 TOPS 为稀疏 INT8 峰值，稠密约 100 TOPS）。注意力/专家权重每 token 都要从 DDR 里流一遍，瓶颈在内存带宽与单序列延迟，不在 NPU 的 MAC 阵列。

要真正把 200 TOPS 吃满，只有三条路，且都不在"跑一个 7B 单序列"范围内：多序列批处理（Genie CLI 不支持）、更小的模型 + 更大 batch（无 bundle）、或换成 prefill 密集型任务（例如把长文档 embedding/分类批量丢给 NPU）。


### 4.4 上下文：已经翻倍到 1024，但仍装不下 dsh 的 2.6K

`deepseek-7b` 的模型配置写着 `context_size: 1024`，而 dialog 只配了 512——把 dialog 提到 **1024 后实测可用**（681 token prompt 正常），2048 则加载失败。改后桥接层可吃 ~600 token 的提示（此前 ~500 就报 `Context Size was exceeded`）。

而 dsh 的系统提示 + 3 个工具 schema 就有 **~2.6K token**，所以"让 NPU 直接托管 dsh Agent"这一步仍**卡在上下文尺寸**，不是卡在 NPU 或桥接层。实测边界：

| 提示长度 | 结果 |
|---|---|
| ~150 token | ✅ |
| ~600 token（ctx=1024 后） | ✅ |
| ~1000 token | ❌ 超 1024 |


**解锁方式（需要 x86 主机 + QNN SDK）**：用 `qnn-onnx-converter` + `qnn-context-binary-generator` 把同一模型按 ≥4K context 重新导出（`--context-length 4096`），或直接向美格索取长上下文版 bundle。导出后把 `npu-openai-bridge.py` 的 `CONFIG` 指到新包即可，dsh 侧只需加一个 provider：

```yaml
- id: llm-pi-ai
  config:
    providers:
      mt200-npu:
        api: openai-completions
        baseURL: http://10.42.0.81:8090/v1
        models:
          - id: deepseek-7b-npu
            contextWindow: 4096      # 重新导出后按实际值
```


---

## 5. 35B 以上：为什么只能是 MoE

dense 35B 在这台机器上的算术：

```
权重 Q4 ≈ 20 GB / token 需读一遍 ≈ 20 GB
DDR5-5600 双通道理论 89.6 GB/s → 理论上限 ≈ 4.5 tok/s（实测通常 1.5–2.5 tok/s）
```

MoE（35B 总参 / 3B 激活）每 token 只读激活专家 ≈ 2 GB → 带宽上限高一个数量级，实测 4–6 tok/s。

### 5.1 选型与校验

| 项 | 值 |
|---|---|
| 模型 | `Qwen3.5-35B-A3B`（Unsloth UD-Q3_K_XL） |
| 文件 | `C:\AI\models\Qwen3.5-35B-A3B-UD-Q3_K_XL.gguf`，16,601,176,704 B |
| SHA256 | `fef37f41877128b0c82195c5cd173ca0e02394f48c7667dbf96172fa300fc945`（与 ModelScope 源一致） |
| GGUF 元数据 | `n_params 34.66 B`，`ftype Q3_K - Medium`，`n_ctx 8192` |
| 服务 | `llama-server`（Vulkan）`:8081`，`-c 8192 -ngl 40 -t 6 -np 1 --jinja` |
| Harness | dsh profile `thinkpad-35b`（provider `thinkpad-local-35b`，contextWindow 8192） |

### 5.2 三组配置实测（`bench.py`，1534 token prefill / 256 token 生成）

| 配置 | prefill tok/s | decode tok/s | 备注 |
|---|---|---|---|
| 纯 CPU `-t 8 -ngl 0` | 32.0 | 3.67 | 不占 GPU 堆 |
| iGPU 30/48 层 + CPU，16K ctx | 36.0 | 4.49 | 6034 token 长 prefill 组 |
| **iGPU 40/48 层 + CPU，8K ctx（Q3_K_XL）** | **50.5** | **5.71** | **当前最优，已设为默认** |
| iGPU 42/48 层 | — | — | ❌ 加载失败（连 88 MB compute buffer 都分不出） |
| iGPU 48/48 层 | — | — | ❌ `ErrorOutOfDeviceMemory`（335 MB KV 分配失败） |
| IQ3_XXS 48/48 层（全驻留，13.1 GB） | 25.3 | 3.44 | 装得下，但更慢 |
| IQ3_XXS 40/48 层 | 33.2 | 2.79 | 更慢 → 是量化问题，不是卸载比例 |

**40 层是 Q3_K_XL 的硬上限**：权重 13.8 GB + KV 0.8 GB + compute ≈ 0.6 GB ≈ 15.2 GB，逼近 15.7 GiB 堆顶。

**全驻留不是答案**：IQ3_XXS 13.1 GB 能整块进 iGPU，但 IQ 系量化在 Vulkan 后端要额外反量化，decode 反而掉到 2.8–3.4 tok/s。同样的 40 层配置下 IQ3（2.79）比 Q3_K_M（5.71）慢一倍——**瓶颈是量化格式在 Vulkan 上的反量化开销，不是显存装不下**。所以"更小的量化 + 全 GPU"这条路在此机上不成立，保留 Q3_K_XL 混合卸载。


### 5.3 35B 的 Agent 闭环（关键验收）

`thinkpad-35b` 连跑 3 轮（disk / ls / cpu 三类任务，判定标准同主报告：答案须对上 tool_result 数值）：

| 轮 | 结果 | 回合时长 | decode | 答案质量 |
|---|---|---|---|---|
| 1 | PASS | 50.7 s | 8.6 tok/s | 精确 |
| 2 | PASS | 90.3 s | 5.6 tok/s | 精确 |
| 3 | PASS | 44.2 s | 8.5 tok/s | 精确 |

均值 61.7 s/回合。作为对比，同一任务 1.7B（GPU）约 17–26 s/回合。

### 5.4 质量差异（值得记一笔）

同一道"C 盘已用/剩余"题：

- 1.7B：`C: 盘的已用空间约为 113.36 GB，剩余空间约为 31.61 GB` ❌（已用对，剩余把 316 GB 写成 31.6 GB；20 轮里同类十进位错 3 次、只对一半 1 次）
- 35B-A3B：`C盘已用约132.09GB，剩余约267.88GB` ✅（GiB 换算正确，对应 141.8 GB / 287.7 GB 原始字节）

即：**35B 慢 3 倍，但算数和指令遵循明显更稳**——值不值得取决于任务性质。

---

## 6. 还没做完 / 明确做不到

| 项 | 状态 | 说明 |
|---|---|---|
| IQ3_XXS 全驻留（13.1 GB） | ❌ 已测，不划算 | 13,080,066,688 B / SHA256 `9f7f5928…` 校验通过；全驻留 3.44 tok/s、40 层 2.79 tok/s，均不如 Q3_K_XL 的 5.71（见 5.2） |
| `--flash-attn on` 换更多层 | ❌ 无效 | `-ngl 44 + -fa on`、`-ngl 42 + -fa on` 均在 88 MB compute buffer 上分配失败 |
| SYCL 后端 | 未测 | GitHub 带宽 70 KB/s，155 MB 拿不下；有本地镜像时再补 |
| dense 35B / 70B | 不做 | 带宽算术决定了 1–4 tok/s，不是可用区间 |
| MT200 NPU 跑 35B | 不可行 | 10 GB 内存装不下 35B 权重；且 QNN 包 context 只有 512 token |
| 更大 pagefile / 关后台应用 | 未动 | 能缓解 commit 压力，但 Vulkan 缓冲区需物理驻留，收益有限；不建议为此改系统设置 |

---

## 7. 复现命令

```powershell
# 1.7B（快，日常）：iGPU Vulkan，:8080
C:\AI\bin\llama-vulkan\llama-server.exe -m C:\AI\models\qwen3-1.7b.gguf `
  --alias qwen3-1.7b --host 127.0.0.1 --port 8080 -c 12288 -t 4 -np 1 --jinja -ngl 99
# dsh 侧用 profile thinkpad-local / thinkpad-web

# 35B（慢，重活）：iGPU 40 层 + CPU，:8081
pwsh -File C:\AI\scripts\start-35b.ps1 -Ngl 40 -Ctx 8192
# dsh 侧用 profile thinkpad-35b

# 基准与验收
python C:\AI\scripts\bench.py "1.7B iGPU" http://127.0.0.1:8080 qwen3-1.7b
$env:DSH_PROFILE='thinkpad-35b'; $env:LLAMA_LOG='C:/AI/logs/llama-35b-40.out.log'
python C:\AI\scripts\accept20.py 3 C:/AI/logs/rounds-35b
```
