# ai-net-manager 数据质量报告

| 数据集 | 样本数 | 时间跨度 |
|---|---|---|
| radio | 450 | 0.1 h |
| qoe | 434 | 0.1 h |
| flows | 237 | 0.1 h |
| window | 240 | - |
| decisions | 240 | 0.1 h |

## 字段覆盖率（radio）

| 字段 | 覆盖率 |
|---|---|
| rat | 100.0% |
| reg_state | 22.7% |
| radio_on | 100.0% |
| rsrp_dbm | 22.7% |
| cell_id | 100.0% |
| tac | 100.0% |

## 字段覆盖率（qoe）

| 字段 | 覆盖率 |
|---|---|
| rtt_ms | 100.0% |
| dl_mbps | 99.3% |
| ul_mbps | 99.3% |

实测 radio 采样率: 0.93 Hz（目标 1 Hz；设备负载高时允许下调）

## 决策动作分布

- none: 240
