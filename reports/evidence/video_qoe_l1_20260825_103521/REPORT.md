# RG660MK-EU 单终端视频网络第一层采集报告

- 采集时间：2026-08-25T10:35:21+08:00
- 采集时长：179.0 s
- 终端：`HONOR-90` / `192.168.1.x` / `e2:c6:**:**:70:5d`
- 接入：`rai0`，采集开始/结束关联终端数：1/1
- 方法：ADB 只读采样 + 单终端 128-byte 包头流式采集；CPE 端未写文件、未改网络/防火墙/offload。

## 1. 单终端吞吐

吞吐主口径为唯一关联终端所在 Wi-Fi 接口的 TX/RX 字节差分；TX 即终端下行。

| 指标 | 结果 |
|---|---:|
| 下行平均吞吐 | 0.043 Mbit/s |
| 下行峰值吞吐（实际平均采样间隔 1.49 s） | 0.562 Mbit/s |
| 下行 P5 / P50 / P95 | 0.000 / 0.004 / 0.393 Mbit/s |
| 波动幅度 P95-P5 | 0.393 Mbit/s |
| 标准差 / 变异系数 CV | 0.122 Mbit/s / 2.827 |
| 5 秒窗口 P5 / P50 / P95 | 0.002 / 0.007 / 0.170 Mbit/s |
| Wi-Fi 信号均值 / 最差 | -61.0 / -63.0 dBm |

分片视频会呈现“突发下载—空闲缓存”节奏，因此 1 秒吞吐 CV 高不能单独判为卡顿；5 秒窗口更适合观察持续供给能力。

## 2. IP 层与接口丢弃

- CPE 蜂窝 WAN 主动探测：发送 180、接收 180，丢包率 **0.00%**。
- ICMP RTT 均值 / P95 / 相邻样本抖动：51.15 / 98.61 / 38.53 ms。
- 蜂窝接口 RX/TX dropped 增量：0/0；errors 增量：0/0。
- Wi-Fi 驱动 RX/TX dropped 增量：1795/447。该 MTK 驱动计数可能包含无线失败/重试，不能直接当作端到端 IP 丢包率。
- qdisc dropped 增量：{'br-lan': 0, 'ccmni3': 0, 'rai0': 0}。

主动 ICMP 从 CPE 发起，代表 CPE→公网路径，不等同于手机应用端到端丢包；目标业务的 TCP 重传是补充证据。

## 3. TCP 层

| 指标 | 结果 |
|---|---:|
| 被动 RTT 样本方法 / 数量 | tcp_timestamp_echo / 190 |
| TCP RTT 均值 / P95 | 31.15 / 47.98 ms |
| TCP RTT 抖动（相邻 RTT 绝对差均值） | 9.42 ms |
| 目标终端 TCP 重传 | 49 次 |
| 目标终端 TCP 重传占 TCP 数据包比例 | 2.669% |
| 目标终端零窗口事件 | 7 次 |
| TCP 流数量 | 395 |

下行包头可见流量中 TCP 字节占比约 75.9%；UDP/443（QUIC 候选）占比约 0.2%。UDP/443 只按端口识别，不属于 DPI 业务识别。

### 拥塞窗口骤降记录

**无法直接读取真实 cwnd。** 手机业务连接是经 CPE 转发的 socket，TCP 状态属于手机和远端服务器，不属于 CPE 本地内核；`ss/TCP_INFO` 即使存在也看不到这些转发连接。以下仅记录可能触发发送端降窗的包级线索（重传、三次重复 ACK），不能表述为真实 cwnd 数值：

- 间接拥塞控制线索：57 条。
  - t=+2.314s，flow `0a0faec331`，fast_like_retransmission，方向 down
  - t=+2.615s，flow `0283e7d2cc`，fast_like_retransmission，方向 down
  - t=+2.757s，flow `6f6c6fc8a9`，timeout_like_retransmission，方向 down
  - t=+4.939s，flow `eec90520fd`，fast_like_retransmission，方向 down
  - t=+7.081s，flow `596a4c4335`，fast_like_retransmission，方向 down
  - t=+7.081s，flow `165990f069`，fast_like_retransmission，方向 down
  - t=+17.137s，flow `165990f069`，timeout_like_retransmission，方向 up
  - t=+17.137s，flow `596a4c4335`，timeout_like_retransmission，方向 up
  - t=+17.361s，flow `165990f069`，timeout_like_retransmission，方向 up
  - t=+17.361s，flow `165990f069`，three_duplicate_acks，方向 down，估算在途 0 B
  - t=+17.361s，flow `596a4c4335`，timeout_like_retransmission，方向 up
  - t=+17.361s，flow `596a4c4335`，three_duplicate_acks，方向 down，估算在途 0 B
  - t=+17.858s，flow `596a4c4335`，timeout_like_retransmission，方向 up
  - t=+17.858s，flow `165990f069`，timeout_like_retransmission，方向 up
  - t=+18.807s，flow `165990f069`，timeout_like_retransmission，方向 up
  - t=+18.807s，flow `596a4c4335`，timeout_like_retransmission，方向 up
  - t=+29.405s，flow `47cf9d8287`，fast_like_retransmission，方向 down
  - t=+31.300s，flow `2b6f18f375`，fast_like_retransmission，方向 down
  - t=+36.325s，flow `4d7d99b1cc`，fast_like_retransmission，方向 down
  - t=+65.055s，flow `58fa961571`，fast_like_retransmission，方向 down

## 4. 采集有效性与边界

- 原始包记录 3997，解析后的目标 IP 包 3935；修正 MTK/bridge tap 重复呈现 44 包。
- tcpdump 内核丢包：不可用（该设备的 ADB exec-out 不回传远端 stderr）。
- 终端采样缺失：0 次；采集结束仍关联：True。
- 原始 pcap 仅保存每包前 128 字节，但仍含 IP/端口等元数据，应按敏感诊断证据管理。
- `/proc/net/snmp` 的 TCP 计数只覆盖 CPE 本地 socket，不能替代本报告的目标终端包级分析。
- 若视频采用 QUIC/HTTP3，TCP RTT、重传、零窗口指标不会覆盖该部分流量；需结合 UDP/443 占比判断适用性。
- 本报告只评价网络是否具备导致卡顿的条件，不能感知手机解码、APP 缓存、片源或手机性能。

## 5. 证据文件

- `station_samples.csv`：1 秒接口/终端采样
- `wan_ping.txt`：WAN 主动探测原始输出
- `target_headers.pcap`：单终端 128-byte 包头
- `snapshot_start.txt` / `snapshot_end.txt`：CPE 内核与接口计数
- `summary.json`：结构化汇总
