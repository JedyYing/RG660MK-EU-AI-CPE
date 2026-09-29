# radiodiag —— RG660MK 5G 网络实时诊断与小区自愈

> 2026-09-29 落地并实机验证通过。设备侧零依赖（仅用固件原生命令）。

## 1. 目标

实时发现 5G 网络异常（弱信号 / 拥塞劣化 / 掉网），并主动触发**重新选网**，
让终端尽量驻留在**更优的小区**；全过程可审计、可验证（诊断→动作→前后对比）。

## 2. 诊断数据源（实测可用）

| 指标 | 来源命令 | 说明 |
|---|---|---|
| RSRP / RAT | `mipc_wan_cli --nw_get_signal` | `RAT 5G, RSRP=-70`（~0.01s） |
| 注册状态 | `mipc_wan_cli --check_nw_status` | `MIPC_NW_REGISTER_STATE_HOME` |
| 射频状态 | `mipc_wan_cli --nw_radio_state_get` | ON / OFF |
| **服务小区 TAC+NCI** | `mipc_wan_cli --at_cmd 'AT+C5GREG=2'` + `'AT+C5GREG?'` | `+C5GREG: 2,1,"590D0A","0594FCB484",…` → 小区变更实锤 |
| WAN 状态 | `ifstatus wan` | up + IP（PDN 会话） |
| 拥塞/质量 | `ping -c5 223.5.5.5` | RTT 均值 / 抖幅 / 丢包 |

## 3. 自愈动作（vendor 官方同款，LuCI 同源）

```
mipc_wan_cli --nw_radio_state_set 0   # 射频关
sleep 5
mipc_wan_cli --nw_radio_state_set 1   # 射频开 → 触发重新搜网/选网
```
随后等待「重注册 + WAN 恢复（≤90s）」→ 稳定 6s → 复测，并与动作前对比：
- 小区 NCI 是否变化
- RSRP 是否改善（Δ≥3 dB 判定改善）
- 诊断结论是否回到正常

> 固件现状：不支持 QENG/QNWLOCK（小区锁），故“切换”通过官方射频循环触发网络重选完成；
> 无邻区列表接口，因此“更优小区”以重选后的小区对比与信号改善来验证。

## 4. 用法

```sh
python3 /data/ai_cpe/radiodiag.py status            # 快照 JSON
python3 /data/ai_cpe/radiodiag.py check             # 诊断结论（含 ping 探测）
python3 /data/ai_cpe/radiodiag.py heal [--force] [--dry]   # 自愈（--dry 演练）
python3 /data/ai_cpe/radiodiag.py watch --interval 60 [--apply]  # 常驻监视（--apply 自动自愈）
python3 /data/ai_cpe/radiodiag.py history [N]       # 审计日志
```

状态文件（供看板集成）：`/data/ai_cpe/radiodiag_state.json`
审计日志（JSONL）：`/data/ai_cpe/radiodiag_log.jsonl`

阈值（环境变量可覆盖）：`RSRP_SEVERE=-105` `RSRP_WEAK=-95` `LOSS_PCT=10` `RTT_MS=200` `JITTER_MS=100` `HEAL_COOLDOWN=180`

## 5. 实测记录（2026-09-29）

- `heal --force` 两次真机执行：射频循环 → 重注册耗时 **9s / 10s**，WAN 均正常恢复（PDN 新会话）。
- 小区对比：`0594FCB484 → 0594FCB484`（本环境为单强小区，重选后保持**最优小区**，RSRP 稳定 -70~-74 dBm；符合预期）。
- `watch` 常驻：5s 间隔观察模式连续运行正常，状态文件/审计日志实时写入。
- 演示建议：现场用 `heal --force` 展示全流程（约 30~60 秒，含 WAN 短暂切换）。

## 6. 后续可选（未做，待定）

- [ ] 看板集成：CPE 面板增加「网络诊断」芯片（读状态文件）+ 自愈事件流水。
- [ ] 常驻服务（procd）：`watch --interval 60 --apply`，实现真正的“实时自动自愈”。
- [ ] 若固件升级开放邻区接口（QENG/等效），可升级为“基于邻区测量择优切换”。
