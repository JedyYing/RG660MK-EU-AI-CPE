# MT200 → Windows ThinkPad：本地 DeepSeek Harness 部署

把 MT200（aarch64，MeiG IQ-9075）上已跑通的「本地模型 + DeepSeek Harness（dsh）+ 工具调用闭环」
迁移到 Windows 11 ThinkPad 的全部代码、配置、文档与验收证据。

- 设计依据：`docs/DeepSeek_Harness_MT200_to_Windows_ThinkPad_移植设计文档_v1.0.pdf`
- 硬件参考：`docs/MT200操作说明手册V1.00.pdf`
- 验收报告：`migration_report.md`（版本清单、配置对照、20 轮明细、故障处理、复现命令）

## 状态

| 阶段 | 结果 |
|---|---|
| 推理层（llama.cpp + 同一 GGUF） | 通过：`/v1/models` + `/v1/chat/completions` |
| Harness Web UI（127.0.0.1:3080） | 通过：token 认证，模型 `Qwen3-1.7B (local, CPU)` |
| 工具闭环（pwsh → 真实结果 → final） | headless 与 Web UI 两侧均成立 |
| 连续 20 轮 | **20/20 PASS**，0 崩溃 / 0 上下文溢出 / 0 无故工具失败 |
| iGPU 加速（Vulkan，1.7B） | 通过：prefill **2.25×**、decode **1.58×**；GPU 后端下工具闭环 3/3 |
| 35B 以上（Qwen3.5-35B-A3B，MoE） | 通过：`-ngl 40` @8K，decode 5.7 tok/s，3/3 PASS，回合 44–90 s |
| NPU | 不可用：本机无 NPU 设备；MT200 的 HTP 被 FastRPC `0x80000406` 拒绝 |

细节见 `migration_report.md`（迁移验收）与 `gpu-npu-35b_report.md`（加速与 35B 实测）。


性能（同模型、同参数）：冷 prefill ≈130 tok/s（MT200 ≈80），decode ≈19.4 tok/s（MT200 ≈17），
单回合 ≈17.2 s；headless 进程冷启动 ≈55 s（单列，不计入 Agent 时延）。

## 目录

```
.
├─ docs/                       设计文档（MT200→ThinkPad 移植）+ MT200 硬件手册
├─ migration_report.md         迁移验收报告：20 轮明细、故障处理、GPU 对照
├─ gpu-npu-35b_report.md       GPU / NPU / 35B 实测报告（含 200 TOPS 压榨记录）
├─ reports/                    状态报告（HTML 源 + PDF + 逐页渲染图）
│  ├─ mt200-status.html        A4 打印版状态报告源文件
│  └─ MT200_DeepSeekHarness_进展报告_2026-10-24.pdf   交付版（桌面同款）
├─ scripts/
│  ├─ start-dsh.ps1            一键启动（幂等：跳过已跑进程，等 /v1/models，打印带 token 的 UI 地址）
│  ├─ stop-dsh.ps1             停止
│  ├─ switch-model.ps1         -Target small|35b 单命令切换（两模型不能同时驻留）
│  ├─ accept20.py              N 轮验收：grounded PASS/FAIL + 启动/回合分开计时
│  ├─ bench.py                 prefill/decode 固定负载基准
│  ├─ trace_round.py           单轮事件时间戳（定位启动 vs 推理 vs 工具耗时）
│  ├─ npu-openai-bridge.py     MT200 上运行的 Genie→OpenAI 兼容桥（:8090）
│  ├─ qnn-conc.sh              QNN CNN 并发吞吐扫描（1/2/4/8/16/24 进程）
│  ├─ mt200-qnn-throughput2.sh 单/多进程吞吐测试
│  ├─ mt200-snpe.sh            SNPE 路径可行性探测（结论：本机不可用）
│  ├─ npu_knobs.py             Genie/HTP 参数扫描（spill-fill / mmap / 权重共享）
│  ├─ npu_mixed.py             并发与混跑实验（双 LLM、LLM+CNN）
│  ├─ mt200-ssh.js             直连 MT200 的 ssh2 驱动（密码走 MT_PASS 环境变量）
│  ├─ mt200-get.js / mt200-put.js   SFTP 拉/推文件
│  ├─ dl-parallel.sh           ModelScope 分段并发下载（可续传 + SHA256）
│  └─ mt200-link.ps1           给 USB 网口配同网段静态 IP（需管理员）
├─ dsh-home/profiles/          dsh profile 补丁层（真正的配置，随仓库版本化）
│  ├─ thinkpad-local/          headless：pwsh/fs/fs-search + unattended 权限
│  ├─ thinkpad-web/            web UI：同上，显式打开 web bundle 默认禁用的工具
│  ├─ thinkpad-35b/            35B headless（contextWindow 8192）
│  └─ thinkpad-web-35b/        35B web UI（绑定 :8081）
└─ logs/                       过程证据（不含运行时日志与 token）
   ├─ preflight.txt            环境探测
   ├─ model-sha256.txt         两侧模型哈希
   ├─ rounds*/                 各轮验收的事件流 + summary.json
   ├─ mt200-*.txt              QNN / Genie / NPU 实测原始输出
   ├─ npu-knobs.txt / npu-mixed.txt   参数扫描与并发混跑数据
   ├─ acl-report/              Windows ACL 沙箱修复的备份、报告与回滚脚本
   └─ ui-proof.png             Web UI 截图
```

模型、llama.cpp 二进制、会话记录、运行时日志与本机 token 不入库（见 `.gitignore`）。
访问 MT200 的脚本不含凭据：先 `export MT_PASS=...`（或 `export MT_PASS="$(cat ~/.mt200-pass)"`）。

## 复现

```powershell
# 1) 推理服务 + Web UI（幂等；1.7B 走 iGPU/Vulkan，回退 CPU 见脚本头部注释）
pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dsh.ps1

# 2) 20 轮验收
python .\scripts\accept20.py 20 C:/AI/logs/rounds

# 3) 单次 headless 任务
$env:DSH_HOME='C:\AI\dsh-home'; $env:LOCAL_LLM_KEY='sk-local'
C:\Users\jedy.ying\dsh-runtime\npm-global\dsh.cmd --profile thinkpad-local --json "调用 pwsh 执行 Get-Date"

# 4) 换模型（两个模型不能同时驻留）
pwsh -File .\scripts\switch-model.ps1 -Target small   # 1.7B / :8080 / 17s 回合
pwsh -File .\scripts\switch-model.ps1 -Target 35b     # 35B-A3B / :8081 / 60s 回合
```

## 开机自启

登录时自动拉起 1.7B + Web UI（用户级，无需管理员）：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\deepseek-harness.vbs
```

隐藏窗口调用 `start-dsh.ps1`；禁用就删掉或重命名这个 `.vbs`。脚本是幂等的，重复触发不会起第二份。

依赖：llama.cpp b11836（win-cpu-x64）、Node 26.12.1、PowerShell 7.6.7、dsh 0.2.1-rc.2、
模型 `qwen3-1.7b.gguf`（Q4_0，1,229,453,568 B，sha256 `8dc9e01a…`，取自 MT200）。

## 关键约束（不要改坏）

- `llama-server` 与 dsh 只监听 `127.0.0.1`；dsh 带 token 认证。
- llama-server 现在强制 `--api-key sk-local`：直接 curl 必须带 `-H "Authorization: Bearer sk-local"`，
  否则 401；dsh 侧靠 provider 的 `apiKeyEnv: LOCAL_LLM_KEY` 自动补上。
- Agent 写权限限定在 `C:\AI\dsh`（`sandbox-policy.workspaceRoot`）。
- 模型的 `contextWindow` 必须等于 `llama-server -c`（12288），改了就开新会话。
- 改配置只动 `cordis.patch.yml`；`cordis.yml` 是 dsh 自己重写的空入口，不要手改。

## 下一步

1. Vulkan A/B（Intel iGPU）——同模型同 context 再跑 20 轮。
2. 4B Q4 评估（7B 在本机 CPU decode ≈7 tok/s，不划算）。
3. `llama-server --api-key sk-local` + provider 已就位的 `LOCAL_LLM_KEY`。
4. headless 冷启动 55 s 的优化（启动期网络探测 / Defender 扫描）。
5. 开机自启：Task Scheduler 调用 `start-dsh.ps1`（调试期不要服务化）。
