# AI 选网 Replay 报告（Shadow 数据回放）

- 输入: `data/decisions.jsonl`
- 说明: 用当前 rules.json 对每个 tick 重新裁决（简化重演驻留/冷却/保持状态）

## 概览

| 指标 | 值 |
|---|---|
| 总 tick 数 | 241 |
| 实际输出建议次数 | 0 |
| 复算通过（would recommend） | 0 |
| 平均预计增益 | None |
| 最大预计增益 | None |

## 被安全规则拦截统计

| 规则 | 次数 |
|---|---|
| min_score_gain | 241 |
| min_gain_hold_s | 241 |
| switch_cooldown_s | 151 |
| min_dwell_time_s | 93 |
| max_switches_10min | 90 |

## 触发分布

| trigger | 次数 |
|---|---|
| none | 241 |

## 建议时刻（前 50 条）

（无——通常表示单小区环境或规则正确拦截了全部候选）
