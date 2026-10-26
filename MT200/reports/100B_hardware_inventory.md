# 100B+ 可运行性摸底：硬件与软件清单（阶段一交付）

采集日期：2026-10-26
采集方式：全部只读命令（未停止/重启/改配置任何服务）
采集者：Hermes Agent
Git 基线：`C:\AI` HEAD = `50e3c2e`（采集时工作区仅有本次新增的 `logs/inv-*.txt`，无既有改动被覆盖）
原始输出：`C:\AI\logs\inv-thinkpad.txt`、`C:\AI\logs\inv-mt200.txt`、`C:\AI\logs\inv-mt200b.txt`

> 本文只记录**实测事实**。凡未实测的推断，统一放入第 6 节并标注"推测"；未取得的信息列入"未知"。

---

## 1. 结论摘要

| 项 | ThinkPad | MT200 |
|---|---|---|
| 可用内存（实测） | **31.5 GB** 物理，空闲 13.5 GB，已提交 24.2 GB | **11.1 GB** 物理，可用 7.6 GB，**无 swap** |
| 可用于模型的上限（扣 OS/服务） | ≈ 25–29 GB | ≈ 6–7 GB |
| 加速器 | Intel iGPU，Vulkan 可用，15.4 GiB 空闲 | HTP V73 NPU，QNN/Genie 可用 |
| 现成 100B+ 模型资产 | **无** | **无** |
| 是否可能跑 >100B | 仅"低比特 + mmap 流式"，见阶段二 | **不可能**（权重最小形态也 >7.6 GB 数个量级） |

**关键硬约束（实测）**
1. MT200 物理内存 11.1 GB、可用 7.6 GB、无 swap，且 CMA 167936 kB **已全部占用（CmaFree=0）**。
2. MT200 的 QNN SDK **只有推理 runner，没有任何模型转换/编译工具**（`/opt/qnn/sdk/bin` 仅 `qnn-net-run`、`qnn-platform-validator`、`qnn-profile-viewer`、`snpe-net-run`、`snpe-platform-validator`、`snpe-diagview`）。
3. 现有 QNN bundle 上下文 **1024 token**，5 个 ctx bin 合计 **4.2 GB**。
4. ThinkPad 磁盘冷读实测 **1135–1927 MB/s**（绕过系统缓存的 NO_BUFFERING 读取）。
5. ThinkPad 无任何 >20 GB 的 GGUF；两台机器均无 100B 级模型文件。

---

## 2. 采集命令（可复现）

### 2.1 ThinkPad（Windows 11，bash/MSYS 环境）

```powershell
# CPU / 内存 / 系统
Get-CimInstance Win32_Processor | Select Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed
Get-CimInstance Win32_OperatingSystem | Select TotalVisibleMemorySize,FreePhysicalMemory,TotalVirtualMemorySize,FreeVirtualMemory
Get-CimInstance Win32_PhysicalMemory | Select Capacity,Speed,PartNumber
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
Get-CimInstance Win32_VideoController | Select Name,DriverVersion,DriverDate,AdapterRAM

# GPU / Vulkan / llama.cpp
C:\AI\bin\llama-vulkan\llama-server.exe --list-devices
C:\AI\bin\llama-vulkan\llama-server.exe --version
C:\AI\bin\llama-cpu\llama-server.exe --version

# 模型资产
ls -lh C:\AI\models\*.gguf
find C:\AI D:\ C:\Users\<user>\Downloads -name *.gguf -size +20G

# 磁盘冷读（Python + CreateFileW FILE_FLAG_NO_BUFFERING，绕过页缓存）
# 见 logs/inv-thinkpad.txt 与阶段二脚本

# 后端能力：直接扫描 llama.dll 里的架构名
python -c "d=open(r'C:/AI/bin/llama-vulkan/llama.dll','rb').read(); print([a for a in [b'glm4moe',b'llama4',b'qwen3moe',b'deepseek3',b'gpt-oss'] if a in d])"
```

### 2.2 MT200（Ubuntu 24.04 aarch64，通过 ssh2 只读执行）

```bash
cat /proc/device-tree/model; cat /sys/devices/soc0/{soc_id,machine}; lscpu
free -m; grep -E "^(MemTotal|MemAvailable|CmaTotal|CmaFree|SwapTotal)" /proc/meminfo
cat /proc/pressure/memory; swapon --show
cat /proc/cmdline; dmesg | grep -iE "cma|reserved memory" | head
ls /dev/dma_heap/; cat /sys/kernel/debug/dma_buf/bufinfo
df -h /; lsblk
uname -a; grep PRETTY_NAME /etc/os-release
ls /opt/qnn/sdk/{bin,lib,dsp}; find / -name "*Skel*.so"
cat /data/local/tmp/qnn/deepseek-7b/deepseek-7b-htp_sharing.json
cat /data/local/tmp/qnn/deepseek-7b/htp_backend_ext_config_sharing.json
systemctl list-units --type=service --state=running
ps -eo pid,pcpu,pmem,rss,comm --sort=-rss
find /data /opt -xdev -type f -size +500M
```

---

## 3. ThinkPad 清单（实测）

### 3.1 硬件

| 项 | 值 | 来源 |
|---|---|---|
| CPU | Intel Core 7 240H，10 核 16 线程，2500 MHz 标称 | `Win32_Processor` |
| 内存 | **31.5 GB** 可见（2 × 16 GB DDR5-5600，`CXMR4E816S6AS-CN1C`） | `Win32_PhysicalMemory` |
| 内存现状 | 空闲 13.5 GB；已提交 24.2 GB / 提交上限 37.4 GB | `Win32_OperatingSystem` |
| 系统盘 | C: 261.7 GB 可用 / 400 GB | `Win32_LogicalDisk` |
| 数据盘 | D: 552.3 GB 可用 / 553.4 GB | 同上 |
| GPU | Intel(R) Graphics，驱动 **32.0.101.7085**（2026-03-03） | `Win32_VideoController` |
| GPU 显存（WMI 报告） | 2048 MB（WMI 对 iGPU 历来不可靠，见下） | 同上 |
| Vulkan 设备 | `Vulkan0: Intel(R) Graphics (16125 MiB, 15357 MiB free)` | `llama-server --list-devices` |
| Vulkan 运行时 | `C:\Windows\System32\vulkan-1.dll`，1,639,536 B（2026-04-09） | 文件属性 |
| 磁盘冷读 | **1135 / 1927 MB/s**（两个偏移各 512 MB，`FILE_FLAG_NO_BUFFERING`） | 自写 Python 基准 |

> GPU 可用内存以 Vulkan 报告的 **15.4 GiB free** 为准；WMI 的 2 GB 是 32 位字段溢出导致的经典误报，不作为证据使用。

### 3.2 推理栈

| 项 | 值 |
|---|---|
| llama.cpp | **b11836**，commit `f5027fb21`，Clang 20.1.8，x86_64 |
| 已安装构建 | `C:\AI\bin\llama-cpu`（CPU）、`C:\AI\bin\llama-vulkan`（CPU + Vulkan） |
| 已编译后端 | CPU（按微架构分派 SSE42/x64/…/alderlake/sapphirerapids）、Vulkan、**RPC**（存在 `ggml-rpc.dll`） |
| 支持的 MoE/大模型架构（二进制内字符串实证） | `qwen3moe`、`qwen2moe`、`glm4moe`、`llama4`、`deepseek2`、`deepseek3`、`deepseek32`、`deepseek4`、`qwen3next`、`gpt-oss`、`bailingmoe`、`afmoe`、`cohere2moe` |

### 3.3 现有模型资产

| 文件 | 大小 | 说明 |
|---|---|---|
| `Qwen3.5-35B-A3B-UD-Q3_K_XL.gguf` | 16.6 GB | 当前 35B 主模型 |
| `Qwen3.5-35B-A3B-UD-IQ3_XXS.gguf` | 13.1 GB | 对照量化 |
| `qwen3-1.7b.gguf` | 1.2 GB | 当前 dsh 默认（iGPU） |
| `Qwen3-1.7B-Q4_K_M.gguf` | 1.1 GB | 早期下载 |

**没有任何 >20 GB 的 GGUF**；HuggingFace/ModelScope 缓存目录为空。

### 3.4 当前运行态（未干扰）

| 端口 | 进程 | 内容 |
|---|---|---|
| 127.0.0.1:8080 | pid 9472 | llama-server，当前模型 `qwen3-1.7b`，`n_ctx 12288`，`-ngl 99`（Vulkan） |
| 127.0.0.1:3080 | pid 21700 | dsh Web UI（profile `thinkpad-web`） |

---

## 4. MT200 清单（实测）

### 4.1 硬件

| 项 | 值 | 来源 |
|---|---|---|
| 板卡标识 | `Qualcomm Technologies, Inc. Addons IQ 9075 EVK (Meig BoardV1.00)` | `/proc/device-tree/model` |
| SoC | `soc_id=676`，`machine=QCS9075` | `/sys/devices/soc0/*` |
| CPU | 8 × Cortex-A78C，最高 2361.6 MHz，单核单簇 | `lscpu` |
| 物理内存 | **11,119 MB**（MemTotal 11,386,008 kB） | `free -m` / `/proc/meminfo` |
| 可用内存 | **7,622 MB**（MemAvailable 7,805,064 kB） | 同上 |
| Swap | **0**（无交换分区） | `swapon --show` |
| 内存压力 | PSI `some avg10=0.00`，无实时压力 | `/proc/pressure/memory` |
| CMA | **CmaTotal 167,936 kB / CmaFree 0 kB** | `/proc/meminfo` |
| 启动保留 | 多个 CMA 池：32 + 16 + 48 + 20 + 16 + 32 MiB 等；内核自报 `167936K cma-reserved` | `dmesg` |
| dmabuf heaps | `qcom,cma-secure-cdsp`、`qcom,qseecom`、`qcom,qseecom-ta`、`qcom,secure-non-pixel`、`qcom,secure-pixel`、`qcom,system`、`reserved`、`system` | `/dev/dma_heap/` |
| 根盘 | `/dev/sda3` 113 G，已用 38 G，**可用 70 G** | `df -h` |
| 系统 | Ubuntu 24.04.4 LTS，kernel 6.8.0-1077.81 aarch64 | `uname -a` |

### 4.2 推理栈

| 项 | 值 |
|---|---|
| QNN SDK 路径 | `/opt/qnn/sdk`（`bin/`、`lib/`、`dsp/`） |
| 可用工具 | `qnn-net-run`、`qnn-platform-validator`、`qnn-profile-viewer`、`snpe-net-run`、`snpe-platform-validator`、`snpe-diagview` |
| **缺失工具** | `qnn-onnx-converter`、`qairt-converter`、`qnn-context-binary-generator`、`snpe-dlc-quantize` —— **设备上没有任何模型转换/编译能力** |
| HTP stub | `libQnnHtpV68Stub.so`、`V69`、`V73`、`V75`、`V79` |
| HTP skel（设备侧） | `/opt/qnn/sdk/dsp/lib/libQnnHtpV73Skel.so`；另有 `/usr/lib/rfsa/adsp/hexagon-v68`、`hexagon-v75` |
| Genie | `/opt/qnn/sdk/lib/libGenie.so`；可执行文件在 `/data/local/tmp/qnn/bin/genie-t2t-run`（不在 PATH） |
| 平台校验 | 此前已实测 `qnn-platform-validator --backend dsp --testBackend` → `Unit Test on the backend DSP: Passed` |

### 4.3 现有 QNN bundle（`/data/local/tmp/qnn/deepseek-7b/`）

| 项 | 值 |
|---|---|
| ctx bins | 5 个：1.4 G + 602 M + 602 M + 602 M + 1009 M ≈ **4.2 GB** |
| context size | **1024** token |
| n_vocab | 102400（bos 100000 / eos 100001） |
| kv-dim / pos-id-dim | 128 / 64 |
| n-threads | 3，cpu-mask `0xe0` |
| use-mmap / mmap-budget | true / 0 |
| spill-fill-bufsize | 0（32/128/256 均加载失败，已实测） |
| perf_profile | `burst`；rpc_control_latency 100 |
| weight_sharing | true（关闭后实测掉 30%） |
| mem_type | `shared_buffer` |

其余 bundle：`deepseek-qwen-7b`、`deepseek-qwen-1.5b`（后者因 313 MB 单缓冲 > CMA 而失败，已实测）。设备上最大的非 bundle 文件是 4.5 GB 的 `qwen3-8b.gguf`。

### 4.4 当前运行态（未干扰）

`llama-server`、`dsh-web` 均 active；`npu-openai-bridge.py` pid 77647 在跑（`:8090`）。CMA 空闲为 0 的状态在服务运行期间持续存在。

---

## 5. 版本清单（汇总）

| 组件 | ThinkPad | MT200 |
|---|---|---|
| OS | Windows 11 专业版 | Ubuntu 24.04.4 LTS |
| Kernel | — | 6.8.0-1077.81（PREEMPT_DYNAMIC） |
| 推理引擎 | llama.cpp b11836（f5027fb21） | QNN/AI-RT（SDK 位于 /opt/qnn/sdk）+ Genie 1.13 |
| 加速 API | Vulkan（vulkan-1.dll 2026-04-09）+ Intel 驱动 32.0.101.7085 | QNN HTP V73（signed skel） |
| 编译工具链 | 无（仅用预编译包） | **无**（只有 runner） |

> MT200 的 QNN 精确版本号未能从 `libQnnHtp.so` 提取（strings 未见标准版本串），此前记录为 AI-RT 2.39 / Genie 1.13，本次未能独立复核版本号 —— 列入"未知"。

---

## 6. 已确认 / 推测 / 未知

### 已确认（有命令输出为证）
1. ThinkPad 物理内存 31.5 GB，当前空闲 13.5 GB，提交上限 37.4 GB。
2. ThinkPad iGPU 经 Vulkan 可用，报告 16125 MiB 总量 / 15357 MiB 空闲。
3. llama.cpp b11836 二进制内含 `glm4moe`、`llama4`、`qwen3moe`、`deepseek3`、`gpt-oss` 等架构名。
4. ThinkPad 磁盘冷读 1.1–1.9 GB/s。
5. MT200 内存 11.1 GB、可用 7.6 GB、无 swap、CMA 167936 kB 且空闲为 0。
6. MT200 无任何模型转换/编译工具，只有推理 runner。
7. MT200 现有 bundle 上下文 1024、权重文件合计 4.2 GB。
8. 两台机器上都不存在 >20 GB（ThinkPad）/ >4.5 GB（MT200）的模型文件。

### 推测（未直接实测，需在阶段二/三验证）
1. ThinkPad 的 DDR5-5600 双通道理论带宽约 89.6 GB/s；按 35B-A3B 实测 5.71 tok/s（3B 激活、约 4.5 bpw ≈ 1.7 GB/token）反推，**有效带宽仅约 10 GB/s**（Vulkan 路径效率与 MoE 路由开销所致）。
2. MT200 的 `deepseek-7b` bundle 实际参数量与结构未验证；`n_vocab 102400 + bos 100000` 与 DeepSeek-V2-Lite 系列一致，但不能据此断定。名称中的 "7b" 不等于参数量 7B。
3. CMA 空闲为 0 很可能被常驻的 DSP/Genie 共享缓冲占用；未做释放实验（属破坏性操作，需批准）。

### 未知（本阶段未取得）
1. MT200 QNN/AI-RT 的精确版本字符串。
2. ThinkPad iGPU 在 Vulkan 下可用的最大动态共享显存（驱动策略未文档化）。
3. 现有 7B bundle 的量化格式（推测 w8a8/w4a16 之一）与 KV cache 占用明细。
4. MT200 是否具备厂商侧重新导出 bundle 的通道（需向美格/高通确认）。

---

## 7. 对 100B+ 部署的直接影响

| 限制 | 数值/证据 | 后果 |
|---|---|---|
| MT200 内存 7.6 GB 可用、无 swap | `/proc/meminfo` | 任何 >100B 模型（最小可用 GGUF 27.3 GiB）**差 3.6 倍以上**，直接排除 |
| MT200 无转换工具 | `/opt/qnn/sdk/bin` 列表 | 无法自行把 GGUF/ONNX 编译成 HTP bundle，只能等厂商提供 |
| 现有 bundle ctx=1024 | dialog JSON | 即便有 100B bundle，也装不下长上下文 |
| CMA 空闲 0 | `CmaTotal 167936 / CmaFree 0` | 新增大型共享缓冲极易 `memRegister 8003`（1.5B bundle 已实证） |
| ThinkPad 31.5 GB vs 最小 100B GGUF 27.3 GiB | 阶段二表格 | 只能"刚好装下 + 无余量"，KV/计算缓冲会挤压到 mmap 流式 |
| ThinkPad 磁盘 1.9 GB/s | 冷读实测 | mmap 流式 decode 的理论上限 ≈ 1.9 GB/s ÷ 每 token 权重读取量 |

---

## 8. 原始数据文件

- `C:\AI\logs\inv-thinkpad.txt` —— ThinkPad 全部命令输出（含 GPU/DIMM/磁盘/服务/内存压力量）
- `C:\AI\logs\inv-mt200.txt` —— MT200 第一轮（SoC/内存/CMA/dmabuf/磁盘/OS/QNN/bundle 全文）
- `C:\AI\logs\inv-mt200b.txt` —— MT200 第二轮（skel 路径/ext config 全文/大文件清单/服务状态）
- `C:\AI\scripts\inv-mt200.sh` —— MT200 采集脚本（可复跑，只读）
