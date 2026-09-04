# RG660MK-EU 单终端视频网络第一层采集报告

- 采集时间：2026-08-25T10:38:55+08:00
- 采集时长：179.0 s
- 终端：`HONOR-90` / `192.168.1.x` / `e2:c6:**:**:70:5d`
- 接入：`rai0`，采集开始/结束关联终端数：1/1
- 方法：ADB 只读采样 + 单终端 128-byte 包头流式采集；CPE 端未写文件、未改网络/防火墙/offload。

## 1. 单终端吞吐

吞吐主口径为唯一关联终端所在 Wi-Fi 接口的 TX/RX 字节差分；TX 即终端下行。

| 指标 | 结果 |
|---|---:|
| 下行平均吞吐 | 0.507 Mbit/s |
| 下行峰值吞吐（实际平均采样间隔 1.49 s） | 6.043 Mbit/s |
| 下行 P5 / P50 / P95 | 0.000 / 0.002 / 5.953 Mbit/s |
| 波动幅度 P95-P5 | 5.953 Mbit/s |
| 标准差 / 变异系数 CV | 1.620 Mbit/s / 3.193 |
| 5 秒窗口 P5 / P50 / P95 | 0.001 / 0.004 / 1.792 Mbit/s |
| Wi-Fi 信号均值 / 最差 | -61.4 / -67.0 dBm |

分片视频会呈现“突发下载—空闲缓存”节奏，因此 1 秒吞吐 CV 高不能单独判为卡顿；5 秒窗口更适合观察持续供给能力。

## 2. IP 层与接口丢弃

- CPE 蜂窝 WAN 主动探测：发送 180、接收 180，丢包率 **0.00%**。
- ICMP RTT 均值 / P95 / 相邻样本抖动：55.63 / 99.43 / 38.84 ms。
- 蜂窝接口 RX/TX dropped 增量：0/0；errors 增量：0/0。
- Wi-Fi 驱动 RX/TX dropped 增量：1538/1364。该 MTK 驱动计数可能包含无线失败/重试，不能直接当作端到端 IP 丢包率。
- qdisc dropped 增量：{'br-lan': 0, 'ccmni3': 0, 'rai0': 0}。

主动 ICMP 从 CPE 发起，代表 CPE→公网路径，不等同于手机应用端到端丢包；目标业务的 TCP 重传是补充证据。

## 3. TCP 层

| 指标 | 结果 |
|---|---:|
| 被动 RTT 样本方法 / 数量 | tcp_timestamp_echo / 189 |
| TCP RTT 均值 / P95 | 32.59 / 53.16 ms |
| TCP RTT 抖动（相邻 RTT 绝对差均值） | 8.53 ms |
| 可见 TCP 数据重传 | 4 次 |
| 可见 TCP 数据重传占数据段比例 | 0.538% |
| SYN/FIN 等握手或关闭重试 | 13 次 |
| 手机通告零窗口 / 远端通告零窗口 | 0 / 0 次 |
| TCP 流数量 | 357 |

下行包头可见流量中 TCP 字节占比约 95.6%；UDP/443（QUIC 候选）占比约 0.0%。UDP/443 只按端口识别，不属于 DPI 业务识别。
软件抓包对 Wi-Fi 下行字节的覆盖率约 7.6%；硬件快速转发确认=True，相关模块=['hw_nat', 'mtk_warp', 'mtk_wed', 'nf_flow_table', 'nft_flow_offload', 'tops']。因此 RTT、重传、零窗口和拥塞线索只代表软件可见子集，不能外推至全部视频流。

### 拥塞窗口骤降记录

**无法直接读取真实 cwnd。** 手机业务连接是经 CPE 转发的 socket，TCP 状态属于手机和远端服务器，不属于 CPE 本地内核；`ss/TCP_INFO` 即使存在也看不到这些转发连接。以下仅记录可能触发发送端降窗的包级线索（重传、三次重复 ACK），不能表述为真实 cwnd 数值：

- 间接拥塞控制线索：9 条。
  - t=+11.239s，flow `1cea87a572`，timeout_like_retransmission，方向 up
  - t=+11.820s，flow `fcf9310765`，three_duplicate_acks，方向 down，估算在途 15600 B
  - t=+21.319s，flow `8844facb19`，fast_like_retransmission，方向 up
  - t=+92.364s，flow `b81d51fec4`，fast_like_retransmission，方向 up
  - t=+94.757s，flow `8589c7dff0`，fast_like_retransmission，方向 down
  - t=+97.674s，flow `ebbcda7289`，three_duplicate_acks，方向 down，估算在途 0 B
  - t=+176.500s，flow `0fc510bad1`，three_duplicate_acks，方向 down，估算在途 0 B
  - t=+176.500s，flow `0be3a0de4c`，three_duplicate_acks，方向 down，估算在途 0 B
  - t=+176.500s，flow `8d653235d9`，three_duplicate_acks，方向 down，估算在途 0 B

## 4. 采集有效性与边界

- 原始包记录 4132，解析后的目标 IP 包 4093；修正 MTK/bridge tap 重复呈现 11 包。
- 字节覆盖：pcap/Wi-Fi=7.6%，br-lan/Wi-Fi=7.3%；低覆盖由 HNAT/WARP/WED 快速路径造成。
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
