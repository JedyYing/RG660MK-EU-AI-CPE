1|# ai-net-manager — AI Smart Cell Selection (Phase 1, Shadow)
2|
3|面向 RG660MK / 蜂窝 CPE 的 AI 选网与流量识别工程方案（Phase 1）。
4|按《AI_Smart_Cell_Selection_Phase1_Design_v1.0》§11/§15 结构落地；
5|**部署形态 = 设备侧 Shadow 常驻**（只观测/只建议，不动生产网络）。
6|
7|## 关键适配（RG660MK / MTK 实测 2026-10-09）
8|- 设备为 **MTK 栈**：`AT+QENG*/QCAINFO/QNWINFO` 全部 `+CME ERROR: 4`（不支持）。
9|  采集走 **mipc_wan_cli** 适配层（设计 §3.1 预留的 firmware-specific parser 三层适配）：
10|  - RSRP/RAT: `--nw_get_signal` / `--nw_get_rat` / `--nw_show_register_rat`
11|  - 注册/射频: `--check_nw_status` / `--nw_radio_state_get`
12|  - 服务小区 TAC/NCI: `AT+C5GREG=2` + `AT+C5GREG?`
13|  - 额外: `AT+CESQ`（原始保留，字段映射 best-effort）、conntrack、/proc/net/dev
14|- **邻区 API 不可用**（无 QENG）。候选小区 = 历史 cell_profile（采集数据累计），
15|  与设计 §8.1 的降级口径一致；capability 报告如实标注。
16|- 设备无 PyYAML/LightGBM 等三方包 → 全部 **stdlib**；配置用 JSON（仓库存 YAML 镜像 +
17|  `tools/yaml2json.py` 转换）。分类器首版为**启发式占位**（低置信度→generic），
18|  待 24-72h Shadow 数据积累后训练 LightGBM（训练工具已就位）。
19|- 执行器默认 **disabled**（设计 §7.3/§15.10）；MTK 无锁频/锁小区命令，L2 候选动作 =
20|  射频软循环（`--nw_radio_state_set 0/1`，radiodiag 同款），仅 capability 验证后人工开启。
21|
22|## 目录（与设计 §11 对齐）
23|```
24|config/        main.yaml|json, rules.yaml|json, traffic_profiles.yaml|json
25|schemas/       radio_sample.py, qoe_sample.py, feature_window.py, decision.py
26|src/ai_net/    collectors/ features/ models/ decision/ executor/ storage/ service.py api.py
27|tools/         replay.py, validate_at_parser.py, yaml2json.py, data_quality_report.py,
28|               train_traffic.py, train_qoe.py
29|tests/         test_parsers.py, test_rules.py, test_replay.py, fixtures/
30|reports/       env_probe.txt, modem_capability.json, ...
31|deploy_ai_net.sh   # 一键部署到设备
32|ai_net.init        # OpenWrt procd 服务模板
33|```
34|
35|## 设备部署（Shadow）
36|```bash
37|bash services/ai_net_manager/deploy_ai_net.sh          # 推送+装服务+启动
38|ssh root@fcd3.. 'python3 -m ai_net.service --mode shadow --config /data/ai_cpe/ai_net/config/main.json'
39|curl http://[fcd3...]:8123/v1/status                   # HTTP API（设计 §12.1）
40|```
41|
42|## 数据与状态
43|- JSONL: `/data/ai_cpe/ai_net/data/{radio,qoe,flows,windows,decisions}.jsonl`
44|- 状态: `/data/ai_cpe/ai_net/data/status.json`（供看板/巡检）
45|- 服务: `/etc/init.d/ai-net`（procd respawn; 开机自启）
46|
47|## Phase 1 完成度（对照设计 §14.3）
48|- [x] 持续采集并落盘（radio/QoE/业务特征），字段覆盖率报告=`tools/data_quality_report.py`
49|- [x] 规则引擎+状态机（含单测）；replay 工具可回放
50|- [x] 只读采集 + 能力探测（capability 报告）
51|- [ ] Traffic Classifier 训练（待 ≥24h 数据；范式与训练脚本已就位）
52|- [ ] QoE/Cell Ranker 模型训练（同上，baseline 对照已内置）
53|- [ ] 执行器开启（需 capability 验证 + 人工许可）
54|

## 设备运行状态（2026-10-09 晚 实测）
- 服务: procd `ai-net`（S98 开机自启、respawn）; 路径: `/data/ai_cpe/ai_net`
- API（只读）: `http://192.168.1.1:8123/v1/status`（注意绑定 IPv4 0.0.0.0，设备上用 127.0.0.1 而非 [::1]）
- 实测: NCI=0594FCB484 / TAC=590D0A / RSRP=-65 dBm / reg=HOME / qoe_score≈65 / 活动流 126（WAN 99）/ ping 13.5ms
- 采样: radio 1s、qoe 1s、flows 2s、decision 2s；落盘 radio/qoe/flows/window/decisions.jsonl

## 与 services/ai-net-manager（2026-10-08 版）的关系（裁定记录）
设备上曾并行运行两套同规格实现（旧版部署于 `/data/ai_net`，procd 名 `ai-net-manager`）。
- 旧版 radio 观测源 `AT+ECELLMEAS?` 在 10-08 刷机后失效（+CME ERROR 0，其 OP-2 未解）→ radio 恒为 null；
  且每秒重试失败命令，与新版争抢 AT 通道。
- **裁定（2026-10-09）**：设备运行态保留本版；旧版**停用 + 禁自启**（文件数据完整保留）。
  恢复旧版: 先停本版，再 `/etc/init.d/ai-net-manager enable && /etc/init.d/ai-net-manager start`。
- 旧版保留价值: ECELLMEAS 邻区路径（待固件侧恢复）、EMMCHLCK 锁小区、`reports/incident_2026-10-08_at_streaming.md`。

## 安全红线（继承 2026-10-08 事故约束）
- **禁止写类 / 流式 AT**（`AT+ECELLMEAS=1`、`AT+ECELL=1` 在 10-08 紧随其后发生模组失联+刷机，列首要嫌疑）。
- 本版采集命令域（全部实测可用）: `--nw_get_signal` / `--nw_get_rat` / `--check_nw_status` /
  `--nw_radio_state_get` / `AT+C5GREG?`（=2 URC 模式沿用 radiodiag 既有做法）/ `AT+CESQ` / ping / /proc 读取。
- 执行器不在本版路径（capability 探测全只读；`executor.enabled=false`）。

## 引擎坑记录（2026-10-09）
- **stdout/stderr 拼接粘连**（已修）: subprocess 分别捕获后直接相加时，若 stdout 无结尾换行，
  首个 stderr 噪音行（`libtrm_init.`）与数据粘成一行 → 被"按行过滤 libtrm"误删 → **RSRP/注册态整列丢失**。
  修复=拼接前补 `\n` + 过滤器仅匹配行首（`util.sh` docstring 有详情）。回归: 近 120 样本覆盖率 85%+ 且持续上升。
- 教训: 嵌入式噪声行过滤，永远保留"原始输出"落盘（raw 字段），并写"数据缺失率"报表而非静默。
