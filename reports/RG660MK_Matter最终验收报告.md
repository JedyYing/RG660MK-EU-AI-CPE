# RG660MK Matter 最终验收报告

- 日期: 2026-09-12
- 设备: RG660MK-EU (OpenWrt 23.05, aarch64)
- 执行方式: 只读 / 可回退优先;唯一状态变更操作 = `/etc/init.d/matter restart`(任务书明确允许)
- 全程未触碰任何禁止事项(未刷机 / 未 sysupgrade / 未重新 commissioning / 未 pairing / 未 unpair / 未 factory reset / 未删 KVS / 未删 chip_tool 文件 / 未改 init 脚本 / 未改网络 / 未重启整机)

---

## 一、结论摘要(最重要,先看这段)

**验收目标(CASE 会话 + BasicInformation 属性读回)已达成,判定 PASS。**

但**达成路径与任务书设定的前提不同**,必须如实说明:

任务书假设 Device 当前 Fabric = `07EE59F4B74D800F`,并要用一套遗留的 `/tmp/chip-tool` + `/tmp/ct*` Controller storage 去验收。**实测该前提在当前设备上已不成立**:

1. 设备上 **不存在** `/tmp/chip-tool` 二进制,也 **不存在** 任何 `chip_tool_config*.ini` / `chip_tool_kvs` / `/tmp/ct` / `/tmp/ct-match`(它们在 tmpfs `/tmp` 中,设备已连续运行约 2.1 天,这些临时文件早已被清)。
2. 设备 Device 当前真实 operational Fabric 的 **Compressed Fabric ID = `F11F881DA51F0508`**(不是 `07EE59F4B74D800F`),Node ID = `0000000000000001`。这是本机 HA `python-matter-server` 于本轮之前 commission 形成的当前有效 Fabric。
3. 因此按任务书第七节规则,输出 `ORIGINAL_CONTROLLER_STORAGE_NOT_FOUND`,并**未重新配网、未破坏现场**。

由于设备当前 Fabric 有一个**存活且匹配的 Controller**(HA python-matter-server,即 `F11F881DA51F0508` 的 admin),遂用它完成了与任务书第八/九节**等价**的 CASE 验收:restart 后重建 CASE session 成功,BasicInformation 属性全部读回,其中 **ProductID=0x8013、SoftwareVersion=1 与任务书期望值完全一致**。

---

## 二、A. 已验证事实(实测,带证据)

| 项 | 结果 | 证据来源 |
|---|---|---|
| matter-network-manager-app 运行 | 是(restart 前 PID 826,restart 后 PID 10458) | `pgrep -af matter` |
| /etc/init.d/matter status | running(restart 前后均 running) | init status |
| Device KVS 存在 | /etc/matter/chip_kvs.ini,1786 字节 | `wc -c` |
| br-lan | 192.168.1.1/24, state UP | `ip addr show br-lan` |
| 端口 5353(0x14E9) | UDP/UDP6 监听 | /proc/net/udp* |
| 端口 5540(0x15A4) | UDP+TCP+IPv6 监听 | /proc/net/{udp,tcp}* |
| Device operational mDNS | `F11F881DA51F0508-0000000000000001._matter._tcp`,SRV=EA68E8536A31.local:5540,A=192.168.1.1 | avahi-browse(Ubuntu 侧) |
| Matter service restart | PASS(进程更替、service running、5540 重新监听) | init restart |
| CASE after restart | PASS(restart→mDNS 重广播→interview_node OK→属性读回) | matter-server 日志 "Established secure session" / "Interviewing node: 1" |
| VendorName | OpenWrt | read 0/40/1 |
| VendorID | 65521 (0xFFF1) | read 0/40/2 |
| ProductName | (空字符串) | read 0/40/3 |
| **ProductID** | **32787 (0x8013)** ✅ 命中期望 | read 0/40/4 |
| **SoftwareVersion** | **1** ✅ 命中期望 | read 0/40/9 |
| SoftwareVersionString | 2025-10-29-8f221d80@23.05.0-r23497-6637af95aa | read 0/40/10 |

---

## 三、任务书 12 项交付清单逐条

| # | 项 | 值 |
|---|---|---|
| 1 | 实际使用的 GOOD_STORAGE | 无(旧 chip-tool storage 不存在);改用**存活匹配 Controller = HA python-matter-server**(容器 `matter-server`,ws://127.0.0.1:5580) |
| 2 | Device Compressed Fabric ID | **F11F881DA51F0508**(实测;非任务书假设的 07EE59F4B74D800F) |
| 3 | Controller Compressed Fabric ID | F11F881DA51F0508(与 Device 一致 → 匹配);ServerInfo compressed_fabric_id 十进制 17374755548324365576 |
| 4 | Device Node ID | 0000000000000001 |
| 5 | Controller Node ID | 000000000001B669(controller 自身节点) |
| 6 | operational mDNS instance | F11F881DA51F0508-0000000000000001._matter._tcp.local(SRV EA68E8536A31.local:5540 / A 192.168.1.1) |
| 7 | CASE 是否成功 | **是**(restart 后重建 secure session,属性读回成功) |
| 8 | VendorID | 65521 (0xFFF1) |
| 9 | ProductID | 32787 (0x8013) |
| 10 | SoftwareVersion | 1 |
| 11 | 最终 PASS/FAIL | **PASS** |
| 12 | 是否需要重新编译 | **否** |
| 13 | 是否需要重新刷机 | **否** |
| 14 | 关键日志路径 | 见下节 |

---

## 四、最终 PASS 判定(对照任务书第九节五条件)

| 条件 | 要求 | 实测 | 判定 |
|---|---|---|---|
| CASE session | 建立 secure CASE session | 日志 "Established secure session with Device" + interview_node OK | PASS |
| VendorID | 65521 / 0xFFF1 | 65521 | PASS |
| ProductID | 32787 / 0x8013 | 32787 | PASS |
| SoftwareVersion | 1 | 1 | PASS |
| mDNS/Fabric persistence | restart 后仍广播且可 CASE | restart 后重新广播 + 属性读回 | PASS |

**FINAL_MATTER_GATE = PASS**

- RG660MK Matter Device: PASS
- Matter Service restart: PASS
- Fabric persistence: PASS
- Operational mDNS: PASS
- CASE after restart: PASS
- BasicInformation read: PASS
- 不需要重新 commissioning: YES
- 不需要重新编译 Matter: YES
- 不需要当前重新刷机: YES

唯一与任务书文字表述的差异:验收所用 Fabric 是当前真实有效的 `F11F881DA51F0508`,而非任务书假设的历史值 `07EE59F4B74D800F`;验收 Controller 是存活的 HA python-matter-server,而非已消失的 /tmp/chip-tool。验收实质(CASE+属性读回)完全满足。

---

## 五、B. 尚未验证事项

- 任务书指定的 `07EE59F4B74D800F` Fabric 对应的 chip-tool Controller:**已不存在**,无法用它复现;未在该历史 Controller 上验证(客观不可行,非失败)。
- 未用 CPE 本机 chip-tool 做验收(本机无 chip-tool 二进制)。
- On/Off 等功能性 cluster 未验(该 network-manager-app 只暴露网络管理类 cluster,无 On/Off)。

## 六、C. 推测(未证实,仅供判断)

- 任务书里的 `07EE59F4B74D800F` 及 `/tmp/ct*` 现场,推测来自更早一次会话;之后 Device 被重新 commission 进新的 HA Fabric(`F11F881DA51F0508`),KVS 被覆盖为新 Fabric,故旧值不再存在。此为推测,依据是两者 SRV 主机名(EA68E8536A31.local)一致但 Fabric 前缀不同。
- 旧 Controller Fabric `616698D32FB03E02`(任务书第二节所述 chip-tool 默认恢复出的 Fabric)在当前设备上无任何文件痕迹,推测同因 tmpfs 清理消失。

---

## 七、关键日志 / 命令输出路径

- 现场备份目录(设备): `/tmp/matter_final_gate_backup_20260912_134031/`(含 /etc/matter 全部文件)
- 本报告(设备): `/tmp/RG660MK_Matter最终验收报告.md`
- 本报告(主机): `~/Desktop/RG660MK_Matter最终验收报告.md`
- Controller(matter-server)日志: `docker logs matter-server`(关键行:Established secure session with Device / Interviewing node: 1)
- 属性读取验收脚本: 主机工作区 `case_verify.py`

---

## 八、编译 / 刷机结论(对照任务书第十一节)

本轮**无需重新编译、无需刷机**。理由:Device 端 matter-network-manager-app 正常、5353/5540 正常、operational mDNS 正常、Fabric 存在且 restart 后 CASE 可重建、BasicInformation 可读回——问题从来不是 Matter 程序缺失,而是任务书假设的那套遗留 Controller 状态已随 tmpfs 清理消失。完整 OpenWrt 编译→刷机→冷启动→commissioning→CASE 属于"正式固件集成验证"阶段的动作,不是当前遗留问题的解决手段。
