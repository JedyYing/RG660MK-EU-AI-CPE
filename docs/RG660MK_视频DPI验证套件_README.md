# RG660MK 5G 视频轻量 DPI — 明文 H.264/RTP 验证套件

RG660MK 仅负责定向抓包; Ubuntu PC 负责 RTP 重组、H.264 NALU 结构检查与指标输出。
本套件为阶段A(本地明文验证, 已 PASS)与阶段B(5G/ccmni2, PENDING)。

## 目录
- src/rtp_core.py        RTP/H.264 核心解析状态机(在线/离线共用)
- src/analyze_offline.py 离线分析单个 pcap
- src/stream_online.py   在线流式解析(stdin读pcap, CLOCK_MONOTONIC_RAW双延迟)
- src/compare_pcaps.py   双pcap匹配(SSRC+扩展序列号+payload哈希)
- src/udp_sink.c         RG侧UDP吸收器源码; bin/rg_udp_sink 为aarch64静态编译产物
- tests/test_rtp_core.py 零依赖回归测试(python3直接跑, 19项)
- run_phaseA_capture.sh  阶段A双点抓包+发流+在线解析编排
- run_phaseA_analyze.sh  阶段A离线分析汇总
- config/synthetic_h264.sdp  PT96->H264/90000 映射
- report/                各阶段报告与指标JSONL/CSV
- pcap/  logs/           抓包与日志(权限0600)

## 指标计数单位(严格区分)
- rtp_packets: RTP包
- fu_a_fragment_pkts / idr_fu_fragments: FU-A分片(RTP包)
- completed_idr_nalus / incomplete_idr_nalus: 完整/未完成 NALU
- keyframes / access_units_total: 视频帧(访问单元)
关键帧数 ≈ 发送秒数(GOP=30,30fps时 秒数=帧数/30... 实为 秒数×30/GOP)。

## 运行(阶段A 复现)
1. 建立一次性 SSH ControlMaster(不写设备公钥):
   SOCK=/tmp/rg660mk-rtp-control
   ssh -M -S "$SOCK" -o ControlPersist=2h -o ServerAliveInterval=30 -fN root@192.168.1.1
2. PC tcpdump 免sudo: sudo setcap cap_net_raw,cap_net_admin+eip /usr/bin/tcpdump
   (完成后可 sudo setcap -r /usr/bin/tcpdump 撤销)
3. bash run_phaseA_capture.sh   # 注意: 单次>2min, 生产化应后台+轮询
4. bash run_phaseA_analyze.sh
5. python3 tests/test_rtp_core.py   # 回归测试

## 阶段B 执行前提(PENDING)
需用户自有/授权的公网 Ubuntu/VPS, 提供公网IP+RTSP/RTP端口(不需密码)。
本机LAN地址(含RG经RA下发的全局IPv6)不可用: RG转发不经ccmni2。见 report/phaseB_status.md。
执行时严格: 先存 ip rule/route table all/wg show/nft ruleset ->
仅对 <IP>/32 建临时绕行(找未用优先级) -> trap自动回滚 -> 不持久化OpenWrt ->
ip route get 与抓包双证走 via 10.49.150.233 dev ccmni2 且未进WireGuard -> 才开测。
任一验证失败立即回滚。

## 安全与留存
pcap/SDP/日志权限0600, 仅存本机, 不上传。未执行ssh-copy-id, 未改authorized_keys。
SSH密码仅交互输入, 不写脚本/日志/环境变量。测试后删除 RG /tmp/rg_udp_sink。
