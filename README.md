# RG660MK 视觉能力部署（YOLO + Image + Immich）

在 Quectel RG660MK AI CPE 上部署的拍照 → YOLOv8 推理 → 质检/人脸/坐姿检测 → Immich 上传的完整链路。

## 系统拓扑

```
Ubuntu 笔记本 (192.168.1.244, 跑 Immich)
    └─ 以太网
RG660MK AI CPE (192.168.1.1, OpenWrt 23.05 / aarch64)
    └─ USB Hub → Logitech C270 (046d:0825)
```

## 组件

| 组件 | 位置 | 说明 |
|------|------|------|
| `vision_runner` | 设备 /data/ai_cpe/demo/bin/ | NCNN 推理服务（用户预编译，stdin/stdout JSON API）|
| `yolov8n` (NCNN) | 设备 /data/ai_cpe/demo/ai_models/ | COCO 80 类目标检测（质检用）|
| `yolov8n-pose` (NCNN) | 同上 | 17 关键点人体姿态（人脸可见性/坐姿检测）|
| `rg660mk_c270_snapshot` | 设备 diag/ | libuvc 静态工具，C270 拍照（用户预编译）|
| Immich | 笔记本 :2283 | 照片服务器（旧版 /api/assets 接口）|

## 脚本说明

### scripts/photo_pipeline.py — 一键拍照→推理→上传
```
[PASS] 拍照: photo_xxx.jpg (27564B)
[PASS] YOLO detect: 801ms [chair(0.63)]
[PASS] YOLO pose: 826ms [0 人]
[PASS] Immich 上传: asset=bc77c73c-...
```

### scripts/posture_check.py — 每小时坐姿巡检（cron watchdog）
- 拍照 → pose 推理 → 人脸可见性（鼻+眼关键点 ≥0.45）→ 有人脸自动上传 Immich
- 坐姿三检：左右对称（肩倾 >18%）、前倾（头肩比 <0.12）、头太高（>0.75）、躯干侧倾（>35%）
- stdout 非空 = 警告（调度器原样投递）；空 = 静默；exit≠0 = 工具故障告警

### scripts/uvc_capture.py — 用户态 UVC 采集（反向工程，备用）
- 该内核 CONFIG_MEDIA_SUPPORT 未启用，无 uvcvideo；此脚本用 usbfs ioctl 直驱 C270
- 记录 usbfs 关键坑（REAPURB 编号、ISO 单包上限、MTK xhci drop_ep_quirk）
- 生产路径请优先用 rg660mk_c270_snapshot

### scripts/t1_*.py — 视频链路 Tier-1 测量工具
- t1_throughput.py: 1s 采样接口计数器（均值/峰值/波动系数）
- t1_pcap_analyze.py: 零依赖 pcap 解析（TCP 重传/RTT/零窗口/拥塞骤降/SNI/分片节奏）
- t1_run.py / t1_watch.py: 测量编排与手机回连守望

## 快速开始（在 RG660MK 上）

```sh
# 一键拍照+推理+上传
/data/ai_cpe/hermes/venv/bin/python3.12 scripts/photo_pipeline.py

# 坐姿巡检（cron 每小时整点, no_agent watchdog）
/data/ai_cpe/hermes/venv/bin/python3.12 scripts/posture_check.py
```

## 已知限制

- 人脸检测 = 人脸可见性（pose 关键点），非身份识别
- 坐姿为 2D 启发式，正对镜头最准，侧身可能误报
- 阈值（0.45/0.18/0.12/0.75/0.35）为初值，需真实数据调优

详见 docs/architecture.md 与 docs/report_*.pdf
