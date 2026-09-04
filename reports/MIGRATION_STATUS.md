# Hermes USB 算力棒移植状态

更新时间：2026-08-24

| 项目 | 状态 | 证据/动作 |
|---|---|---|
| 附件架构改写为 Hermes | 完成 | `RG660MK-EU_AI_CPE_Hermes_USB算力棒移植实施文档_V2.2.md` |
| Hermes 真实安装与 skill 格式识别 | 完成 | v0.19.0；`/data/ai_cpe/hermes`；现有 `SKILL.md` frontmatter |
| 统一 AI API 客户端 | 完成 | `services/hermes_ai_tool.py` |
| Hermes 本地 AI skill | 完成 | `hermes-skill/rg660mk-local-ai/SKILL.md` |
| Gate 0/1/2 脚本 | 完成 | `scripts/gate0_inventory.sh`、`gate1_cpe_baseline.sh`、`gate2_hermes_baseline.sh` |
| AI/Hermes 独立回滚 | 完成 | `scripts/stop_ai.sh`、`scripts/stop_hermes.sh` |
| Gate 0 实机 | BLOCKED | 当前 UDC high-speed，无 Host/SuperSpeed 运行态 |
| Hermes 最小文本请求 | BLOCKED | provider HTTP 429 总额度超限；CLI RC=0 陷阱已纳入脚本 |
| HailoRT/UGen300 | 未进入 | 等待 Gate 0 PASS 和官方 ARM64/OpenWrt 兼容证据 |
| 模型迁移/多外设/完整 Demo | 未进入 | 依赖前序 Gate |

## 必须由现场解除的阻塞

1. 确认并接入载板 USB 3.2 Host 口、有源 Hub 和算力棒；切换前建立 RJ45/串口管理。
2. 补充或切换 Hermes 模型 provider 额度，使精确哨兵请求成功。

阻塞解除前，本移植包只保存在 Ubuntu 工作区，未写入设备；Hermes gateway 与 CPE 网络维持原状。
