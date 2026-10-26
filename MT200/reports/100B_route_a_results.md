# 路线 A 实测结果：106B 模型在 ThinkPad 上的真实表现

日期：2026-10-26 23:05–23:19
结论先行：**能跑，但不能用。** decode 中位 **0.72 tok/s**，prefill 中位 **0.9 tok/s**，内存吃满 31.6 GB。

---

## 1. 被测对象（可验证）

| 项 | 值 |
|---|---|
| 文件 | `D:\models\GLM-4.5-Air-UD-IQ1_S.gguf` |
| 大小 | **38,503,140,416 字节**（35.86 GiB） |
| SHA256 | `4491e4427ecbf93588f9f23483028ff1e7a3b6f63a0602fcb353f623bf0412ab`（与 HF LFS ETag 一致，本地拼接后复算一致） |
| 参数量 | 106B 总参 / 12B 激活（MoE，模型卡声明） |
| 许可 | MIT（ModelScope / HF `unsloth/GLM-4.5-Air-GGUF`） |
| 量化 | UD-IQ1_S（等效约 2.88 bpw，实测 35.86 GiB ÷ 106B） |

## 2. 运行配置

```
C:\AI\bin\llama-cpu\llama-server.exe
  -m D:\models\GLM-4.5-Air-UD-IQ1_S.gguf
  -c 2048 -t 6 -np 1 --jinja
  -ngl 0 -nkvo -ctk q8_0 -ctv q8_0 -b 128 -ub 64
  --no-warmup --metrics
```

- llama.cpp **b11836**（commit `f5027fb21`），CPU-only 构建（无 Vulkan 后端）
- KV cache 量化为 q8_0 并放在系统内存
- `-ngl 0`：完全不使用 iGPU

## 3. 实测结果（5 题，每题生成 16 token）

| # | prompt token | prefill tok/s | decode tok/s | 墙钟 | 输出（前 60 字） |
|---|---|---|---|---|---|
| 1 | 13 | 0.57 | **0.69** | 44.8 s | 一句话说明 MoE 混合专家模型：MoE 混…（截断） |
| 2 | 14 | 0.83 | **0.76** | 36.8 s | `<think>` 用户现在需要把"本地推理的关键瓶颈是内存带宽"翻译…（进入思考态） |
| 3 | 16 | 1.36 | **0.87** | 29.0 s | 质数是指大于1的自然数，除了1和它本身外，不能…（**答非所问**：要求列数字，给的是定义） |
| 4 | 32 | 1.53 | **0.54** | 48.9 s | 只回答数字。首先，问题是：一个仓库有 480 件货…（**未给出数字**） |
| 5 | 21 | 0.86 | **0.72** | — | 小批量推理时，NPU 算力利用率低，因输入…（正确方向） |

**汇总**：decode 中位 **0.72 tok/s**（0.54–0.87）；prefill 中位 **0.9 tok/s**（0.57–1.53）；内存峰值 **31,625 MB**（机器 31.5 GB，可用余量约 1 GB）。

原始数据：`C:\AI\logs\bench-glm45air-v5.json`、`C:\AI\logs\100b-glm45air-v5.log`、`C:\AI\logs\100b-server5.err.log`

## 4. 这个速度意味着什么

| 任务 | 耗时 |
|---|---|
| 生成 200 token 回答 | **≈ 4.6 分钟** |
| 读入 2048 token 上下文（prefill） | **≈ 38 分钟** |
| 5 轮简单问答（当前测试） | 约 3 分钟 |

对照同机 35B-A3B（Q3_K_XL，iGPU+CPU）：decode **5.71 tok/s**、prefill **50.5 tok/s** —— **快 8 倍，prefill 快 56 倍**。

原因：106B 的 12B 激活参数每 token 需读取约 **4.3 GB** 权重（12B × 2.88 bpw ÷ 8），而模型 35.9 GiB 远超可用内存，只能靠磁盘流式供给。实测有效带宽 = 4.3 GB × 0.72 tok/s ≈ **3.1 GB/s**，正好落在"磁盘 + 部分页缓存"的量级，说明**瓶颈完全在存储，不在算力**。

## 5. 失败尝试记录（含原始错误）

| # | 配置 | 结果 | 原始错误 |
|---|---|---|---|
| 1 | `-ngl 20 -c 4096` | 失败 | `failed to allocate buffer for kv cache`；提交内存冲到 56 GB，物理内存剩 11 MB |
| 2 | `-ngl 99 -ncmoe 46 -c 4096 -ctk q8_0` | 失败 | `ggml_vulkan: Device memory allocation of size 409993216 failed` / `ErrorOutOfDeviceMemory` |
| 3 | `-ngl 46 -ncmoe 46 -nkvo -c 2048` | 失败 | `failed to allocate Vulkan0 buffer of size 433193472` → `failed to allocate compute pp buffers` |
| 4 | `-ngl 0`（仍用 Vulkan 构建） | 失败 | 同上（Vulkan 后端仍在分配显存） |
| 5 | **CPU-only 构建，`-ngl 0 -nkvo`** | **成功** | — |

**根因**：Intel iGPU 的"显存"是**系统内存的一部分**。36 GiB 模型把 RAM 占满后，显卡连 400 MB 都拿不到。任何 GPU 卸载在这台机器上都会让情况更糟，不是更好。

## 6. 质量观察（IQ1_S）

5 题中 **2 题明显不合格**（第 3 题答非所问、第 4 题不给数字），第 2 题进入 `<think>` 后未在 16 token 内给出译文。这说明 1.5 bpw 级别量化对 106B MoE 的能力损伤是实质性的：**参数规模更大 ≠ 更好用**。同机 35B-A3B 在 Q3_K_XL（4.5 bpw）下，同类算术题是答对的。

## 7. 判定（对应任务书四项）

| 目标 | 判定 |
|---|---|
| A 硬件摸底 | ✅ 完成（`100B_hardware_inventory.md`） |
| B 选出 >100B 路线 | ✅ 选出并验证路线 A；B 物理不可能；C 缺 RPC 且用不上 NPU |
| C 利用 GPU/NPU | ⚠️ 本路线**证明 iGPU 有害**（共享内存）；NPU 无法参与 GGUF 推理 |
| D 真实加载+推理+测量+复现 | ✅ 完成：真实文件、SHA256 校验、真实输出、v5 配置可复现 |

**未达标项**：性能。0.72 tok/s 不具备任何交互价值，判定为"技术上可行、工程上不可用"。

## 8. 复现命令

```powershell
# 1) 校验（应为 38503140416 字节）
(Get-Item D:\models\GLM-4.5-Air-UD-IQ1_S.gguf).Length
Get-FileHash D:\models\GLM-4.5-Air-UD-IQ1_S.gguf -Algorithm SHA256   # 4491e442...0412ab

# 2) 启动（CPU-only，约 1–2 分钟加载 + 首次换页更久）
C:\AI\bin\llama-cpu\llama-server.exe -m D:\models\GLM-4.5-Air-UD-IQ1_S.gguf `
  --alias glm-4.5-air-iq1s --host 127.0.0.1 --port 8081 `
  -c 2048 -t 6 -np 1 --jinja -ngl 0 -nkvo -ctk q8_0 -ctv q8_0 -b 128 -ub 64 --no-warmup

# 3) 测速
python C:\AI\scripts\bench_100b.py 8081 glm45air-v5 16

# 4) 恢复日常栈
pwsh -File C:\AI\scripts\switch-model.ps1 -Target small
```

## 9. 如果要让它"能用"，只有三条路

1. **加内存**：64 GB 机器可让 35.9 GiB 全驻留，decode 预计提升到 2–3 tok/s（仍远低于 35B）；
2. **换更小的 100B 类模型**：目前不存在 <27 GiB 的 100B+ GGUF（阶段二已核算）；
3. **换更高比特量化 + 更少参数**：那就不再满足 ">100B" 的硬性要求。

**建议**：把 35B-A3B 作为日常主力（5.71 tok/s），100B 只作为"能力上限演示"保留，不接入 dsh 工作流。
