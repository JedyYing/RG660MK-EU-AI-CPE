# ai-net-manager — AI 选网 Phase 1（RG660MK-EU 落地版）

依据：《AI Smart Cell Selection Phase 1 详细设计文档 v1.0》（2026-10-08，可执行设计文档），
以及《AI 选小区与业务保障 RG660MK 可行性评估 v2》（2026-10-06）。

## 目标设备适配结论（capability probe）

| 项目 | 设计文档假设 | RG660MK-EU 实测 | 结论 |
|---|---|---|---|
| 服务/邻区测量 | AT+QENG="servingcell"/"neighbourcell" | 固件无 QENG（CME ERROR 4） | 用 **AT+ECELLMEAS?**（MTK E 命令，服务小区+邻区一次返回） |
| CA 信息 | AT+QCAINFO | 固件无 QCAINFO（CME ERROR 4） | missing flag，降级运行 |
| 制式/Band | AT+QNWINFO | 无 | AT+C5GREG? + 本地 ARFCN→band 映射 |
| 标准 RSRP | — | AT+ECSQ?（标准 CESQ 语义） | 用于服务小区校准（低频次采样） |
| 锁小区 | AT+QNWLOCK | 无 QNWLOCK（CME ERROR 4）；**AT+EMMCHLCK 可用** | L3 执行器 = `AT+EMMCHLCK=1,<rat>,0,<arfcn>,<pci>,0`，撤销 `AT+EMMCHLCK=0`；默认 `executor_enabled=false` |

实测命令域：`AT+EMMCHLCK=?` → `+EMMCHLCK:(0-3),(0,2,7,11),(0,1),(0-2279165),(0-1007)`（rat 7=LTE, 11=NR）。

⚠ **开放问题 OP-1**：`+ECELLMEAS` 的 val1/val2/val3 物理单位未在文档给出；
当前默认 `provisional_div10`（val/10）为待验证假设，设备恢复后用
`tools/calibrate_ecellmeas.py`（ECSQ 交叉标定，只读）定标。

⚠ **开放问题 OP-2**：刷机后 `AT+ECELLMEAS?` 持续返回 `+CME ERROR: 0`（崩溃前可用）；
`AT+ECELLMEAS=?` 仅回 OK；`emdlogger1`/`mnld` 在跑。推测测量引擎需要一次"启用事件"
（历史会话可能由 `=1` 或主机工程工具触发）后 `?` 才有缓存可读 —— **禁止用 `=1` 验证此假设**
（见事故复盘）。解决前：radio 采样按 missing 处理，`modem_ok` 因 ECELLMEAS 失败保持 false →
执行器 guard 拦截一切动作（安全侧行为）。待办：向 Quectel/MTK 求证非流式启用方式。

## 安全约束（来自设计文档，硬性）

- 默认 Shadow 模式，`executor_enabled: false`；执行器为 allowlist + 结构化参数，禁止拼接任意 AT 字符串。
- 所有 AT 通道**只读轮询**，禁止开启流式上报模式：`AT+ECELLMEAS=1` / `AT+ECELL=1` 被
  `ATTransport` 正则硬护栏拒绝（`allow_streaming_modes: false`）。
  ⚠ 2026-10-08 12:51:37 现场探测时发送了 `AT+ECELLMEAS=1` / `AT+ECELL=1`（写类命令）：
  设备随即持续上抛 `+ECELLMEAS` 帧，约 12:54 AT 全通道静默，12:58:59 掉载波失联 2h25m；
  用户现场发现模组自动关机且无法启动，遂刷机。**因果无法直接证明（该机有周期性重启前科、
  崩溃日志已随刷机清空），但时间线高度吻合，列首要嫌疑** —— 详见
  `reports/incident_2026-10-08_at_streaming.md` 与 `executor/capability.py` incidents。
  即日起：**人工探测与程序采集一律只读（`=?`/`?`），写类 AT（含 L3 锁小区）仅在用户在场
  明确授权后执行**。
- 连续 AT 失败 ≥3 → `modem_ok=false`（状态机 guard 拦截动作）；执行器连续失败 ≥2 →
  运行期自动降级 Shadow（事件 `auto_degrade`）。
- 所有缺失值保留显式 `missing` 标记，绝不填 0（schema `missing` 字段 + 决策日志）。
- 设备侧 Python 为标准库版（无 pip/numpy），全部算法纯 Python。

## 目录结构（对应设计文档第 11 章）

```
ai-net-manager/
├── config/            # main.yaml / rules.yaml / traffic_profiles.yaml
├── schemas/           # RadioSample / QoESample / TrafficPrediction / FeatureWindow / Decision
├── src/ai_net/
│   ├── collectors/    # at_transport（3 后端+硬护栏）/ modem.py（E 命令解析）/ qoe_probe.py / traffic_flow.py
│   ├── features/      # aggregator（5/10/30s 窗）/ transforms / history（小区画像）
│   ├── models/        # traffic（启发式 + GBDT 包装）/ gbdt（纯 Python 训练+推理）/ registry（sha256 校验）
│   ├── decision/      # scorer（QoE 归一化）/ rules（硬约束/迟滞/回滚）/ state_machine
│   ├── executor/      # capability（只读探测）/ shadow（L0）/ mtk_lock（L3 EMMCHLCK）
│   ├── storage/       # jsonl（按天+大小轮转）
│   ├── api.py         # 本地只读 HTTP（设计 12 章）
│   └── service.py     # 主循环编排 + 降级 + state.json
├── tools/             # replay / train_traffic / calibrate_ecellmeas / data_quality
├── deploy/            # ai-net-manager.init（procd）+ install.sh
├── tests/             # 35 用例（解析/规则/状态机/服务集成，离线可跑）
└── reports/           # env_probe / modem_capability / 训练报告 / 回放报告 / 数据质量
```

## 运行与接口

```sh
# 设备侧（procd，见 deploy/ai-net-manager.init）
python3 -m ai_net.service --config /data/ai_net/config
python3 -m ai_net.service --config config --once     # 单次自检（打印 snapshot）

# 本机测试
python3 -m pytest tests/ -q

# 离线工具
python3 tools/replay.py --radio radio.jsonl --qoe qoe.jsonl   # 回放→决策报告（A/B：规则 vs RSRP-only）
python3 tools/train_traffic.py                                # 训练六类分类器（合成弱标注打通链路）
python3 tools/calibrate_ecellmeas.py                          # 设备上跑：ECELLMEAS 单位定标（只读）
python3 tools/data_quality.py --log-dir /data/ai_net/log       # shadow 数据质量报告
```

本地只读 API（仅 127.0.0.1，设计 12 章）：`/health` `/status` `/decisions?n=` `/cells`。

## 运行模式与升级路径（设计 8 章）

| 模式 | executor_level | 行为 |
|---|---|---|
| shadow（默认） | L0 | 全链路运行、决策落盘，绝不写 Modem |
| recommend | L1 | 同上 + 对外给建议（日志/API） |
| execute | L3 | 需 `rules.yaml safety.executor_enabled=true` **且** capability 审计通过，否则启动自动降级 |

`execute` 上线前必须：设备端跑 `tools/calibrate_ecellmeas.py` 定标 → capability probe →
锁当前服务小区往返验证（锁→回读→解锁，现场授权）→ 再放开执行器。

## 部署

```sh
deploy/install.sh              # 同步 + Step0 环境自检 + procd 注册 + 启动（shadow）
deploy/install.sh --restart    # 重启
deploy/install.sh --probe      # 只读 capability probe 并取回报告
```

> **状态注记（2026-10-09 晚）**: 设备运行态已切至并行实现的 `services/ai_net_manager`
> （其 radio 源为本机实测可用的 mipc/AT+C5GREG 路径；评测数据 2026-10-09）。本项目 procd 服务
> 已**停用 + 禁自启**，文件/数据完整保留于 `/data/ai_net`。恢复: 先停新版（`/etc/init.d/ai-net stop`），
> 再 `/etc/init.d/ai-net-manager enable && /etc/init.d/ai-net-manager start`。本项目 OP-1/OP-2 与
> EMMCHLCK L3 路径仍为后续资产。

## 当前状态（2026-10-08 晚）

- 已完成：本地全链路实现 + 用例通过 + 回放/训练工具。
- 事故：12:51 流式写命令 → 12:58:59 掉载波 → 现场模组自动关机且无法启动 → 用户刷机恢复
  （15:24 重新上线，见 `reports/incident_2026-10-08_at_streaming.md`）。
- **已部署（shadow）**：`/data/ai_net`（src + schemas + config + tools + `vendor/python311` stdlib 补齐
  + 合成训练模型 `traffic_clf.json`）；procd 服务 `ai-net-manager` 运行中，API `127.0.0.1:8787` 正常
  （mode=shadow）；Step0 落 `reports/env_probe.txt`，capability 落 `reports/modem_capability.json`。
- 刷机后只读复测：固件串号不变（`...350.01.350`，QGMR）；EMMCHLCK 域不变、当前无锁；QNWLOCK 仍无；
  ⚠ `AT+ECELLMEAS?` → CME ERROR 0（**OP-2**，radio 暂无观测源）；`AT+ECSQ?` 仅回裸 `0`（OP-1）。
- 下一步：解决 OP-2 观测源（找安全启用路径，禁止 `=1`）→ ECELLMEAS 定标（OP-1）→ 24h shadow 采集 →
  现场标注数据重训 → 回放/数据质量报告；L3 锁往返验证等用户现场授权。
