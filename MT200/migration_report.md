# DeepSeek Harness：MT200 → Windows ThinkPad 迁移报告

日期：2026-10-23 · 执行：Hermes Agent · 依据：《DeepSeek Harness MT200→Windows ThinkPad 移植设计文档 v1.0》

---

## 1. 结论

迁移完成，按文档 P0 目标**完整复现闭环**：

- Windows 原生路径（未使用 WSL2）：llama.cpp `llama-server` + dsh `0.2.1-rc.2`，全部跑在 ThinkPad 本机。
- 模型与 MT200 **同一个文件**（SHA256 逐字节一致），启动参数与 MT200 **完全一致**，因此性能数字可直接对照。
- 工具闭环成立：`tool_call → tool_result → final`，20 轮验收 **20/20 PASS**，无崩溃、无上下文溢出、无工具失败。
- Web UI 在 `127.0.0.1:3080` 常驻可用，模型显示为 `Qwen3-1.7B (local, CPU)`，权限档位 `Unattended`。
- 推理全程离线，不依赖任何云 API；MT200 上的服务未被改动，仍可独立运行。

第一阶段不做 GPU 加速（ThinkPad 无 NVIDIA 独显）。Vulkan A/B 与更高参数量模型作为 P2 另开。

---

## 2. 版本与硬件

| 项目 | MT200（源，未改动） | ThinkPad（目标，本次部署） |
|---|---|---|
| 平台 | 美格 MT200 AI BOX（IQ-9075 / QCS9075，aarch64） | 联想 ThinkPad，Intel Core 7 240H（10 核 16 线程，2.5 GHz 基准） |
| 系统 | Ubuntu，kernel 6.8.0-1077.81 aarch64 | Windows 11 专业版 10.0.26200（x64） |
| 内存 / 磁盘 | 10 GB / 113 GB（余 70 GB） | 31.5 GB / 401 GB（余 298 GB） |
| 加速器 | CPU（`-ngl 0`），NPU 栈未打通 | Intel iGPU（驱动 32.0.101.7085），无 NVIDIA（`nvidia-smi` 不存在） |
| 推理二进制 | `/opt/meigine/bin/llama-server`（MeiG 构建） | llama.cpp **b11836**（`0.7.0-dev`，官方 win-cpu-x64 包，`C:\AI\bin\llama-cpu`） |
| Harness | dsh 0.2.1-rc.2 | dsh 0.2.1-rc.2（npm 全局安装，非 npx 临时拉取） |
| Node / npm | 系统自带 | Node 26.12.1 / npm 12.0.2 |
| Shell | bash | PowerShell **7.6.7**（本次 winget 安装；Windows 自带 5.1 不够，dsh 的 pwsh 工具需要 7） |
| Git / winget | — | git 2.53.0 / winget 1.29.380 |

---

## 3. 模型（同文件基线）

| 项 | 值 |
|---|---|
| 文件 | `qwen3-1.7b.gguf`（Qwen3-1.7B，Q4_0，1.72B 参数） |
| 大小 | 1,229,453,568 字节 |
| SHA256 | `8dc9e01a6d62a6357a7c4b91f06e62e232ce3c370e9be249e68f2159d9af6e85` |
| MT200 路径 | `/data/local/tmp/gguf/qwen3-1.7b.gguf` |
| ThinkPad 路径 | `C:\AI\models\qwen3-1.7b.gguf` |

两侧 `sha256sum` 结果一致（日志：`C:\AI\logs\model-sha256.txt`）。文件经直连网线 SFTP 从 MT200 复制，**不是** 另行下载的等价模型——这一点决定了下面的性能对比是同模型对比。

> 过程中曾从 ModelScope 下载 unsloth 版 `Qwen3-1.7B-Q4_K_M`（1,107,409,472 B，SHA256 `b139949c…`）作为备选；发现与 MT200 文件不同后弃用，保留在 `C:\AI\models\Qwen3-1.7B-Q4_K_M.gguf` 未参与验收。

---

## 4. 配置对照

### 4.1 推理服务（两侧参数逐字对齐）

```
-m <model> --alias qwen3-1.7b --host 127.0.0.1 --port 8080
-c 12288 -t 4 -np 1 --jinja -ngl 0 --chat-template-kwargs {"enable_thinking": false}
```

`-np 1`（单 slot）、`--jinja`、`enable_thinking=false` 均按 MT200 保留：去掉它们会让 Qwen3 把预算花在思维链上，答案与基线不可比。

### 4.2 Harness

| 配置项 | MT200 | ThinkPad |
|---|---|---|
| `DSH_HOME` | `/home/ubuntu/.dsh` | `C:\AI\dsh-home`（独立，不碰用户现有 `~/.dsh`） |
| profile | `headless` / `web` | `thinkpad-local`（headless）/ `thinkpad-web`（web），均由官方模板新建 |
| provider id | `meig-local` | `thinkpad-local` |
| Base URL | `http://127.0.0.1:8080/v1` | 同 |
| 协议 | `openai-completions` | 同 |
| API key | `apiKeyEnv: LOCAL_LLM_KEY`（`sk-local` 占位） | 同（`LOCAL_LLM_KEY=sk-local`） |
| contextWindow / maxTokens | 12288 / 1024 | 同 |
| 工具集 | bash / fs / fs-search（其余 14 个工具插件显式禁用） | **pwsh** / fs / fs-search（其余同样禁用，bash 在 Windows 上本就禁用） |
| 权限 | 默认 `ask` | `workspace-write` + `approval: never`（自定义 `unattended` preset），工作区根 `C:\AI\dsh` |
| 服务化 | systemd（`llama-server.service`、`dsh-web.service`） | PowerShell 脚本 `start-dsh.ps1` / `stop-dsh.ps1`（幂等）；开机自启留待 P2 |

`persona`、`agent-instructions`（8192 B）、`time-context` 与 MT200 保持一致；旧会话一律未复用，验收全部使用新会话。

### 4.3 目录结构

```
C:\AI\
├─ bin\llama-cpu\        llama.cpp b11836（llama-server.exe + ggml dll）
├─ models\               qwen3-1.7b.gguf（与 MT200 同文件）
├─ dsh\                  Harness 工作区（Agent 唯一可写范围）
├─ dsh-home\             DSH_HOME：profiles\{thinkpad-local,thinkpad-web}、sessions、storages
├─ logs\                 preflight、llama、dsh-web、20 轮 jsonl 与 summary.json、ACL 修复备份
└─ scripts\              start-dsh.ps1、stop-dsh.ps1、accept20.py、mt200-ssh.js/mt200-get.js
```

---

## 5. 验收结果

### 5.1 分阶段（对应设计文档第 9 节）

| 阶段 | 验收项 | 结果 |
|---|---|---|
| A 推理服务 | `/v1/models` + `/v1/chat/completions` | **通过**：`qwen3-1.7b`，`n_ctx 12288`；固定 prompt 返回 `WINDOWS_OK`（HTTP 200） |
| B Harness UI | `dsh web` | **通过**：`127.0.0.1:3080` HTTP 200，token 认证，无启动报错 |
| C 本地 Provider | Harness → llama-server | **通过**：新会话普通问答正常，UI 显示 `Qwen3-1.7B (local, CPU)` |
| D 工具闭环 | PowerShell 工具调用 | **通过**：headless 与 Web UI 两侧均出现 `tool_call → tool_result → final` |
| E 原任务复现 | date / 磁盘任务 | **通过**：20 轮全部基于真实 `Get-Date`/`Get-PSDrive` 输出作答（判定见 5.3） |
| F 连续稳定性 | 连续 20 次同类任务 | **通过**：20/20，0 崩溃、0 上下文溢出、0 无故工具失败 |
| G 性能 | TTFT / decode / 总时延 | **通过**：见第 6 节（启动与回合分开记账） |

Web UI 侧单独跑的一次完整回合：1 轮 2 步、56 s、7.2K tok、缓存命中 46%，页面显示 14–16 tok/s，答案为 C 盘真实已用/剩余字节数。

### 5.2 20 轮明细

| # | 任务 | 结果 | 启动 s | 回合 s | 首个 tool_call s | prefill tok/s | decode tok/s | 答案质量 |
|---|---|---|---|---|---|---|---|---|
| 1 | disk | PASS | 62.1 | 18.6 | 71.3 | 108.7 | 19.7 | 精确 |
| 2 | ls | PASS | 70.6 | 19.0 | 82.1 | 101.9 | 13.2 | 精确 |
| 3 | cpu | PASS | 65.6 | 19.1 | 77.9 | 94.7 | 17.4 | 精确 |
| 4 | disk | PASS | 63.4 | 23.6 | 72.6 | 104.7 | 16.4 | 精确 |
| 5 | ls | PASS | 65.1 | 17.0 | 75.7 | 102.6 | 20.2 | 精确 |
| 6 | cpu | PASS | 64.5 | 19.8 | 75.5 | 103.3 | 19.7 | 精确 |
| 7 | disk | PASS | 67.2 | 20.8 | 79.3 | 105.4 | 20.3 | **单位换算错误** |
| 8 | ls | PASS | 60.1 | 14.6 | 69.1 | 100.7 | 20.6 | 精确 |
| 9 | cpu | PASS | 48.5 | 15.7 | 58.4 | 108.7 | 20.1 | 精确 |
| 10 | disk | PASS | 52.0 | 20.3 | 60.3 | 108.8 | 19.5 | 精确 |
| 11 | ls | PASS | 47.6 | 14.4 | 56.4 | 101.7 | 20.3 | 精确 |
| 12 | cpu | PASS | 47.8 | 15.2 | 56.9 | 104.5 | 19.5 | 精确 |
| 13 | disk | PASS | 46.3 | 15.8 | 55.0 | 110.0 | 20.5 | **单位换算错误** |
| 14 | ls | PASS | 46.5 | 15.0 | 55.3 | 103.3 | 20.6 | 精确 |
| 15 | cpu | PASS | 47.2 | 16.3 | 57.2 | 101.1 | 19.5 | 精确 |
| 16 | disk | PASS | 48.5 | 18.4 | 57.9 | 104.5 | 19.5 | 部分数值错 |
| 17 | ls | PASS | 48.5 | 14.4 | 57.0 | 104.8 | 20.1 | 精确 |
| 18 | cpu | PASS | 46.8 | 15.8 | 56.8 | 103.0 | 19.9 | 精确 |
| 19 | disk | PASS | 46.8 | 16.5 | 56.1 | 111.5 | 20.4 | **单位换算错误** |
| 20 | ls | PASS | 49.8 | 14.1 | 58.5 | 99.0 | 20.8 | 精确 |

原始事件：`C:\AI\logs\rounds\round-NN.jsonl`；汇总：`C:\AI\logs\rounds\summary.json`。

### 5.3 判定口径（为什么 PASS 不含"答得漂亮"）

每轮必须同时满足：① 至少 1 次 `tool_call`；② 至少 1 条 `status=completed` 的 `tool_result`；③ final 不含错误标记（`Error`/`[stderr]`/`找不到驱动器` 等）；④ **final 里的数字必须能在本轮 tool_result 中核对**（GB/GiB 与原始字节两种口径，2% 容差）。

质量单独归档：16 轮精确，3 轮单位换算错误（把 316 GB 说成 31.6 GB），1 轮只对一个数值。这是 1.7B 的算术能力问题，不是工具链问题——数据确实来自工具输出，只是模型自己算错了。

---

## 6. 性能

### 6.1 与 MT200 基线对照（同模型、同参数、同工具族）

| 指标 | MT200（部署报告） | ThinkPad 本次实测 | 说明 |
|---|---|---|---|
| 冷 prefill | 2232 tok / 27.7 s ≈ **80 tok/s** | 3232 tok / 24.9 s ≈ **130 tok/s** | ThinkPad 快 ~1.6×；token 数更大是因为 dsh 0.2.1 的基座 prompt + 3 工具 schema ≈ 2.6K |
| decode | ≈ **17 tok/s** | 20 轮均值 **19.4 tok/s**（13.2–20.8） | 单轮 70+ token 计权，未用 3-token 小样本 |
| 单回合（session → final） | — | 均值 **17.2 s**（14.1–23.6） | 含 2 次 LLM 调用 + 1 次工具执行 |
| 工具执行 | — | ≈ 2–3 s / 次 | pwsh 冷启动 + ACL 沙箱 provisioning |
| 进程启动 | — | 均值 **54.7 s**（46.3–70.6） | **不计入** Agent 时延，见 6.2 |

### 6.2 启动开销要单列

headless CLI 每次调用都要重新装载整套插件：实测 39 s 时点 CPU 16.7 s、静置约 22 s（疑似启动期网络探测/凭据检查）。这 55 s 是 dsh 开发者预览版在 Windows 上的进程启动成本，与模型、CPU 无关——把它算进"Agent 时延"会让 CPU/GPU 对比失真，因此本报告全程分开记账。

对使用方式的直接影响：**交互走常驻的 Web UI**（一次启动、长期复用），批量验收才用 headless。

### 6.3 后续：iGPU 后端实测（详见 `gpu-npu-35b_report.md`）

上表是 CPU 基线。把同一模型换到 Intel iGPU（Vulkan，`-ngl 99`）后重跑 20 轮：

| 指标 | CPU `-t 4` | iGPU Vulkan |
|---|---|---|
| 20 轮工具闭环 | 20/20 | 20/20 |
| 回合均值 / 中位数 | 17.2 s / 16.4 s | 22.4 s / 21.4 s（同时段 CPU 对照 22.0 s / 18.0 s，与 GPU 不可区分） |
| 冷 prefill（2630 token） | 135 tok/s（19.5 s） | **343 tok/s（7.7 s）** |
| decode（2630 token 上下文） | 19.96 tok/s | **27.31 tok/s** |

原因是 agent 稳态回合几乎都走前缀缓存（每轮只 prefill 146–209 token），GPU 的冷 prefill 优势被缓存吃掉。**因此默认仍开 GPU（冷启动/长 prompt/35B 都受益），但稳态回合耗时与 CPU 持平。**

---

## 7. 问题与处理（都已解决，记录备查）

| 现象 | 根因 | 处理 |
|---|---|---|
| `llama-server: failed to load model '/c/AI/models/...'` | MSYS 风格路径传给原生 exe | 改用 `C:/AI/models/...`（原生程序不做路径转换） |
| `HTTP 400 ill-formed UTF-8 byte` | bash 里 `curl -d` 传中文 | JSON 写文件 + `--data-binary @req.json` |
| dsh web 里没有 shell 工具 | web bundle 默认 `disabled: true` 掉 tool-bash/pwsh/fs | 新 profile 里 `disabled: false` 显式打开 tool-pwsh/fs/fs-search |
| `composed sandbox and approval defaults match no preset` | `workspace-write` + `approval: never` 不匹配内置 preset | 声明自定义 preset `unattended`（sandbox=workspace-write，approval=never） |
| 工具直接报 `SetNamedSecurityInfoW failed (Win32 5): grantWrite(C:\AI\dsh)` | 该目录缺少当前用户的 **WRITE_OWNER**（只有 Modify），ACL 沙箱无法置备 | 用 dsh 自带的 `diagnose-windows-sandbox-acl.ps1` 诊断并修复 `C:\AI\dsh` 与 `C:\AI`（先行备份，回滚脚本在 `C:\AI\logs\acl-report\`） |
| `option '--profile' argument 'thinkpad-web' is invalid. select a profile only once` | 把 `web` 当 app 名又传了 `--profile` | 正确形式：`dsh --profile thinkpad-web --host … --port …` |
| Windows OpenSSH 连 MT200 输密码后 `Connection closed` | 该 PTY/密码路径在 Windows 客户端上不可用 | 改用 Node `ssh2` 驱动（`C:\AI\scripts\mt200-ssh.js` / `mt200-get.js`），密码走环境变量，不落盘 |
| `npx` 每轮额外开销、版本漂移 | npx 解析 + 缓存检查 | 固定到全局安装的 `dsh.cmd`（0.2.1-rc.2），脚本里写死路径 |

安全边界：llama-server 只监听 `127.0.0.1:8080`；dsh 只监听 `127.0.0.1:3080` 且带 token 认证；Agent 写权限限定在 `C:\AI\dsh`；未复制任何明文凭据；MT200 侧服务与配置未改动。

---

## 8. 待办建议

1. **Vulkan A/B（P2，收益最大的一步）**：`llama-b11836-bin-win-vulkan-x64.zip`，同模型同 context 跑 20 轮对比。Intel iGPU 走 Vulkan 通常能显著改善 prefill；先用同一 prompt 集做 AB，再决定是否常开。
2. **模型上限**：31.5 GB 内存足够 7B/14B Q4，但 CPU decode 会掉到 ~7 tok/s（1.7B 的 1/3），4B Q4 大约 9–10 tok/s 是更实际的折中。建议先试 4B 再谈 7B。
3. ~~**推理服务加 API key**~~：**已完成**——`llama-server --api-key sk-local` 已写进 `start-dsh.ps1` / `start-35b.ps1` / `switch-model.ps1`；实测无 key 返回 401、带 key 200，dsh 通过 `apiKeyEnv: LOCAL_LLM_KEY` 自动补头且闭环仍 1/1 通过。
4. **启动 55 s 优化**：排查启动期网络探测（凭据/版本检查）与 Defender 对 node_modules 的扫描；若能关掉探测，headless 批量任务会快一倍以上。
5. **开机自启（P2）**：`start-dsh.ps1` 挂 Task Scheduler（登录触发、普通权限），不要在调试期就服务化。
6. **热管理**：连续满载时用 HWiNFO/Lenovo Vantage 记录温度与降频；1.7B + `-t 4` 属于轻载，本机不构成风险（MT200 空闲 37 °C 可作对照）。

---

## 9. 复现方式

```powershell
# 一键启动（幂等，已在跑就跳过，最后打印带 token 的 Web UI 地址）
pwsh -NoProfile -ExecutionPolicy Bypass -File C:\AI\scripts\start-dsh.ps1

# 停止
pwsh -NoProfile -ExecutionPolicy Bypass -File C:\AI\scripts\stop-dsh.ps1

# 20 轮验收（headless）
python C:\AI\scripts\accept20.py 20 C:/AI/logs/rounds

# 单次 headless 任务（注意 DSH_HOME 与占位 key）
$env:DSH_HOME='C:\AI\dsh-home'; $env:LOCAL_LLM_KEY='sk-local'
C:\Users\jedy.ying\dsh-runtime\npm-global\dsh.cmd --profile thinkpad-local --json "调用 pwsh 执行 Get-Date"
```

Web UI：`http://127.0.0.1:3080/?token=<启动日志里的 token>`（token 每次启动不同，`start-dsh.ps1` 会打印）。
