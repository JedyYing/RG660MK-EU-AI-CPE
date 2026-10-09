# AI 选网（AI Smart Cell Selection）Phase 1 — 部署报告（RG660MK-EU）

- 日期: 2026-10-09 | 依据: 《AI_Smart_Cell_Selection_Phase1_Design_v1.0.pdf》(§15 执行路径)
- 交付: `RG660MK-EU-AI-CPE` repo → `services/ai_net_manager/`（代码+配置+工具+测试+报告）

## 一、一句话结论
**全链路 Shadow 版已常驻运行在 RG660MK**：1Hz 采集"无线+网络+业务流" → 每 2s 特征窗评估 QoE 与候选 →
安全规则闸门裁决 → **只记录、只建议，绝不控制网络**（严格只读，零写类/流式 AT）。

## 二、运行状态（实测证据，2026-10-09 16:51）
| 项 | 值 |
|---|---|
| 服务 | procd `ai-net`（S98 开机自启、崩溃自动拉起），路径 `/data/ai_cpe/ai_net` |
| API | `http://192.168.1.1:8123/v1/status`（另 /v1/radio /v1/candidates /v1/decision，只读） |
| 服务小区 | NCI `0594FCB484` / TAC `590D0A` / RSRP `-65 dBm` / 注册 `HOME` |
| QoE 评分 | ≈65/100（radio 子分 1.0；评分权重按"当前业务类别"切换） |
| 业务流 | 活动 126 条（WAN 侧 99），含手机看 8090 视频流等 |
| 探测 | ping RTT 13.5ms（抖动/丢包入窗统计） |
| 数据 | radio 450+ / qoe 434 / flows 237 / decisions 240 条（持续增长，20MB 轮转） |

## 三、设计适配（设备实测 vs 设计假设）
| 设计假设 | RG660MK 实测 | 处置 |
|---|---|---|
| AT+QENG serving/neighbour | 不支持（CME 4） | 采集走 mipc_wan_cli + C5GREG/CESQ 适配层 |
| 邻区测量 | 无可用只读源 | 候选=历史 cell_profile（设计 §8.1 降级口径） |
| CA / QCAINFO | 不支持 | missing 显式标记（绝不填 0） |
| 锁小区 QNWLOCK | 不支持 | 本版执行器保持禁用 |

## 四、期间发现与处置
1. **发现设备上已有 10-08 版实现**（`services/ai-net-manager`，其 radio 源 ECELLMEAS 刷机后失效、
   每秒重试争抢 AT 通道）→ 已**停用+禁自启**（文件保留，恢复一条命令）；运行态唯一=本版。
2. **修复关键采集 bug**：stdout/stderr 拼接粘连 → 信号列整列丢失；修复后近 120 样本 RSRP 覆盖 85%↑。
3. **安全红线对齐**：探测全只读；未发任何写类/流式 AT（详见 README 安全红线节）。

## 五、交付物清单
- `src/ai_net/`：采集（MTK/mipc 适配）/ 5·10·30s 特征窗 / 启发式分类（待训练替换）/ 评分+候选 /
  规则引擎+状态机（设计 §7/§9 全量安全规则）/ Shadow 执行器 / HTTP API
- `config/`：main / rules / traffic_profiles（YAML 权威 + JSON 设备副本）
- `tools/`：replay（回放重裁）/ data_quality（覆盖率报表）/ validate_at_parser / train_*（训练管线）
- `tests/`：18 用例全过（解析/规则/回放）+ 真实回包 fixtures  → `reports/test_report_20261009.txt`
- `reports/`：env_probe / modem_capability / data_quality / replay_report / test_report / 本报告

## 六、验收方式（1 分钟）
```bash
# 设备上或局域网：
curl -s http://192.168.1.1:8123/v1/status   # 实时总览
curl -s http://192.168.1.1:8123/v1/decision # 最新一次裁决（含 top_features 可解释字段）
```
或看设备数据: `/data/ai_cpe/ai_net/data/*.jsonl` 与 `status.json`。

## 七、后续清单（Phase 1 收尾）
- [ ] 累积 ≥24-72h shadow 数据 → 主机侧训练 Traffic Classifier / QoE Ranker（tools 已就位）
- [ ] 数据质量/回放报告滚动更新（一条命令）
- [ ] （可选）旧版资产回收：ECELLMEAS 邻区（待固件恢复）、EMMCHLCK 锁小区（需现场授权）
