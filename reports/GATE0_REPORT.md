# Gate 0：RG660MK-EU 只读盘点与 USB 资格确认

- 执行时间：2026-08-24 19:19～19:23 CST
- 设备：ADB `0123456789ABCDEF`
- 执行方式：Ubuntu Host → ADB，只读命令
- 结果：**BLOCKED**

## 1. 执行命令范围

执行了 `id`、`uname`、`/etc/os-release`、`ip`、`df`、`mount`、`/proc/meminfo`、`/proc/cpuinfo`、USB sysfs、device tree、`dmesg` 过滤和 Hermes 路径/进程/CLI 盘点。未执行分区、格式化、刷机、恢复出厂、未知 AT、USB role 写入或网络配置修改。

## 2. 系统与资源证据

```text
uid=0(root) context=system_u:system_r:init_t
Linux OpenWrt 5.15.134 aarch64
OpenWrt 23.05.0 r23497-6637af95aa
OPENWRT_BOARD=gem6xxx/evb6988_cpe_mt7992_emmc
MemTotal=1492444 kB
SwapTotal=0 kB
/data=/dev/block/user_data ext4 2.7G, 2.0G available
/overlay=21.4M, 19.1M available
SELinux=Permissive
```

影响：模型、Runtime 和日志必须放 `/data`，不能放根 overlay；AI 服务需单并发、限内存。

## 3. 网络证据

```text
ccmni3 10.42.13.53/29
br-lan 192.168.1.1/24
default via 10.42.13.54 dev ccmni3
223.5.5.5: 4/4 replies, avg 45.238 ms
qlitellm.phicotek.com -> 140.210.138.13
```

网关 `10.42.13.54` 不响应 ICMP，但公网 ICMP 与 DNS 成功，因此不能仅凭网关 ping 判定数据面失败。

## 4. USB 证据

设备树：

```text
/proc/device-tree/usb@11591000
dr_mode=otg
maximum-speed=super-speed-plus
compatible=mediatek,mtu3
```

运行态：

```text
/sys/class/udc/11591000.usb
state=configured
current_speed=high-speed
maximum_speed=super-speed-plus
USB_UDC_DRIVER=g1
```

系统没有 `lsusb`；`/sys/class/usb_role/...` 只有 role-switch 对象但没有可读 `role` 属性；没有发现 `/sys/bus/usb/devices/usb*/speed` 对应的 Host root bus，也没有算力棒 VID:PID/速度证据。

### 判定

- 控制器能力：OTG + SuperSpeed Plus，**有能力声明**。
- 当前模式：Device/UDC，ADB 实际链路为 high-speed 480M，**不是 Host 证据**。
- Gate 0：**BLOCKED**，不能判 PASS，也不能安装 HailoRT。

### 解锁条件

1. 确认载板 USB 3.2 Gen2 外露接口和 VBUS/CC/ID/role-switch 设计。
2. 先建立 RJ45 或 DB9/UART 带外管理，避免切 role 后失去 ADB。
3. 接有源 USB 3.x Hub 和算力棒。
4. 重新记录 Host root bus、算力棒 VID:PID、`speed>=5000` 和无反复 reset/disconnect 的 dmesg。

## 5. Hermes 证据

```text
Install: /data/ai_cpe/hermes
Launcher: /usr/bin/hermes -> /data/ai_cpe/hermes/hermes.sh
Version: Hermes Agent v0.19.0 (2026.7.20)
Python: 3.12.14
Gateway: PID 12701, hermes gateway run
Messaging: Feishu configured
Model: deepseek-v4-pro, custom provider
```

最小请求实际返回：

```text
API call failed after 3 retries: HTTP 429: 额度超限[TOTAL 总额]，累计 300.9552584 超上限 300.0000 ￥
HERMES_RC=0
```

结论：Hermes 进程与网关存在，但模型调用被额度阻塞；v0.19.0 在该错误下仍返回 RC=0，后续验收必须检查精确回复内容。

`hermes doctor` 全量诊断发生了自动 `pip install boto3` 尝试并在 ADB 超时后遗留进程。相关诊断与 pip 子进程已停止，gateway PID 12701 保留，检查结果 `boto3_installed=False`。后续 Gate 禁止使用会安装依赖的 doctor 全量模式。

## 6. 对 CPE 的影响

- 未修改 USB role、网络、启动、固件、分区或 Hermes 配置。
- 未安装 Hailo Runtime、驱动、boto3 或其他依赖。
- Hermes gateway 保持运行。
- 本轮仅产生一次最小模型请求；该请求因 HTTP 429 未成功。

## 7. 下一 Gate 决策

根据硬 Gate 规则，Gate 1～6 暂不进入。可以并行完成离线软件接口和文档，但不得向设备安装 NPU Runtime。若载板无法提供稳定 Host/SuperSpeed，采用 RJ45 外置 AI 主机 fallback。
