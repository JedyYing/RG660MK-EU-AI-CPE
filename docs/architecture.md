# 架构与接口文档

## vision_runner JSON API

请求（stdin, 单行 JSON ≤1MiB）：
```json
{
  "version": 1,
  "operation": "detect | pose",
  "input": {"path": "/abs/photo.jpg"},
  "models": {
    "detect": "/data/ai_cpe/demo/ai_models/yolov8n/model.ncnn.param",
    "pose": "/data/ai_cpe/demo/ai_models/yolov8n-pose/model.ncnn.param"
  }
}
```

响应（stdout）：
```json
{"ok": true, "result": {
  "image": {"width": 640, "height": 480},
  "inference_ms": 805.3,
  "detections": [{"bbox": {"x1","y1","x2","y2"}, "score": 0.7, "class_id": 56, "class_name": "chair"}],
  "count": 1
}}
```
pose 操作返回 `persons`（bbox + score + 17 keypoints，COCO 序：0鼻 1左眼 2右眼 3左耳 4右耳 5左肩 6右肩 7左肘 8右肘 9左腕 10右腕 11左髋 12右髋 13左膝 14右膝 15左踝 16右踝）。

性能：~800ms/图（4×Cortex-A55, NCNN CPU 推理）。

## Immich 上传

```
POST http://192.168.1.244:2283/api/assets
Header: x-api-key: <KEY> / Accept: application/json
multipart: assetData=@file, deviceAssetId, deviceId, fileCreatedAt, fileModifiedAt
→ {"id": "<uuid>", "status": "created"}
```

## 坐姿检测规则（posture_check.py）

| 指标 | 计算 | 阈值 | 判定 |
|------|------|------|------|
| 左右对称 | \|肩左y-肩右y\| / 肩宽 | >0.18 | 肩部倾斜 |
| 前倾 | (肩中点y-鼻y) / 肩宽 | <0.12 或鼻低于肩线 | 低头/前倾 |
| 头太高 | 同上 | >0.75 | 头部后仰 |
| 躯干侧倾 | \|肩中点x-髋中点x\| / 肩宽 | >0.35 | 侧倾 |

人脸可见性：鼻 + 至少一眼 关键点置信度 ≥ 0.45。

## 定时任务

- 调度：`0 * * * *`（每小时整点），Hermes cron no_agent watchdog 模式
- 静默语义：无人脸 → 不输出不打扰；有人脸 → 上传 Immich；异常 → 输出中文警告由调度器投递
- 失败语义：exit≠0 → 调度器投递错误告警
- 照片留档：photos/archive/hourly_*.jpg

## 设备限制备忘

- 内核无 MEDIA_SUPPORT/uvcvideo/V4L2（/dev/video* 不存在）
- usbfs 用户态 UVC 要点：USBDEVFS_REAPURB=_IOW('U',12)=0x4008550c；
  REAPURB arg 回写提交结构指针；HS ISO 单包 ≤ maxp×mult；
  MTK xhci drop_ep_quirk：URB 缺口→buffer overrun→端点丢弃（已入队 URB 仍完成）
- opkg 官方源对本定制板卡 404；read_file/write_file 工具文件视图受限；terminal 的 /bin/sh 异常
