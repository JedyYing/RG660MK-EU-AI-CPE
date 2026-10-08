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

## 安全约束（来自设计文档，硬性）

- 默认 Shadow 模式，`executor_enabled: false`；执行器为 allowlist + 结构化参数，禁止拼接任意 AT 字符串。
- 所有 AT 通道**只读轮询**，禁止开启流式上报模式：`AT+ECELLMEAS=1` / `AT+ECELL=1` 被
  `ATTransport` 正则硬护栏拒绝（`allow_streaming_modes: false`）。
  2026-10-08 现场：开启流式测量后数分钟内设备 AT 全静默 + USB 链路掉载波；
  因果未证实（该设备本身有周期性重启特性），但保留禁令与事故记录（见 `executor/capability.py` 的 incidents）。
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

## 当前状态（2026-10-08）

- 已完成：本地全链路实现 + 35 用例通过 + 回放/训练工具（合成数据打通链路，模型 `models/traffic_clf.json` 已生成）。
- 阻塞：**CPE 离线**（13:05 起 USB 网卡 NO-CARRIER、设备不在 lsusb 枚举；疑似挂死后未自恢复，需现场断电重启）。
- 待设备恢复：Step 0 环境自检落 `reports/env_probe.txt` → capability probe（EMMCHLCK/QNWLOCK 结论落盘）→
  ECELLMEAS 单位定标（OP-1）→ 24h shadow 采集 → 现场标注数据替换合成数据重训 → 回放/数据质量报告。
