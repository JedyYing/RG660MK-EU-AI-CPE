# RG660MK 人脸识别（身份识别）+ Home Assistant 接入 — 部署文档

生成时间：2026-09-10

## 一、结论摘要

| 功能 | 代码状态 | 阻塞点 | 能否直接部署 |
|------|---------|--------|-------------|
| 人脸识别（身份识别）| ✅ 代码已全部就绪 | ❌ 缺 2 个 NCNN 模型 + vision_runner 未实现 face 操作 | 否（需改 C++ + 建模）|
| Home Assistant 接入 | ⚠️ 仅设计 | ❌ 无 HA 实例 + 无 MQTT broker | 否（需外部环境）|

两个功能都属于「需要新建模型 / 外部基础设施」的工程，不是把现成代码部署过去就能跑通的。

---

## 二、人脸识别（身份识别）

### 2.1 已完成的部分（代码层 100% 就绪）

| 组件 | 位置 | 状态 |
|------|------|------|
| 人脸识别服务 | `ai_service.py` | ✅ Gallery 匹配（余弦相似度，阈值 0.52）、`_identify_faces()`、`match/add/remove` 全实现 |
| 图库管理 | `face_gallery.py` | ✅ `enroll`（录入人脸）/ `remove` / `list` 命令 |
| CLI 客户端 | `hermes_ai_tool.py` | ✅ `face` 动作（POST /vision/face）|
| 配置 | `ai-service.json` | ⚠️ `face_recognition` 段已配好，但 `face_detect: null`、`face_embed: null` |

### 2.2 阻塞点（两个，都需新建）

1. **vision_runner 未实现 face 操作**：
   `ai_runtime/src/vision_runner.cpp` 第 596 行：
   ```cpp
   if (operation != "detect" && operation != "pose")
       throw JsonError("operation must be detect or pose");
   ```
   目前只支持 YOLO 的 detect（COCO 80 类）和 pose（17 关键点）。人脸识别需要新增：
   - `face_detect`：人脸检测（RetinaFace / YOLOv5-face 等，输出人脸框）
   - `face_embed`：人脸特征提取（MobileFaceNet / ArcFace 等，输出 128~512 维 embedding）
   并实现对应的后处理解码（人脸检测的后处理与 YOLO 不同），再用工具链交叉编译 aarch64。

2. **两个 NCNN 模型缺失**：`face_detect` 和 `face_embed` 均未配置模型文件。需获取/转换：
   - 人脸检测模型 → NCNN（.param + .bin）
   - 人脸 embedding 模型 → NCNN（.param + .bin）

### 2.3 下一步路线（建议顺序）

1. 选型人脸检测模型（优先 RetinaFace-mobilenet，NCNN 生态成熟）。
2. 选型人脸 embedding 模型（优先 MobileFaceNet，128 维，轻量）。
3. 转 NCNN 格式（onnx2ncnn），放入 `/data/ai_cpe/demo/ai_models/`。
4. 改 `vision_runner.cpp`：新增 face 操作 + 两个模型的前向 + 后处理。
5. 交叉编译（见 quectel-module-deployment skill 的交叉编译坑）。
6. 回填 `ai-service.json` 的 `face_detect` / `face_embed` 模型路径。
7. 用 `face_gallery.py enroll <subject_id> <image>` 录入人脸。
8. 通过 `hermes_ai_tool.py face` 或飞书 → Hermes 验证身份识别。

---

## 三、Home Assistant 接入

### 3.1 现状

- 本机（192.168.1.244）无 Home Assistant 实例（端口 8123 关闭）。
- 本机无 MQTT broker：仅装了 `mosquitto-clients`（pub/sub 客户端），未装 broker 守护进程 `mosquitto`（安装需 sudo）。
- RG660MK 已有 Tuya 云（灯泡）、Matter 等设备接入，但未接入 HA。

### 3.2 推荐接入方案：MQTT Discovery

HA 生态的标准接入方式是 MQTT。RG660MK 作为 MQTT 客户端，把设备（灯泡等）通过
MQTT Discovery 暴露给 HA：

```
RG660MK ──(MQTT publish/subscribe)──> MQTT broker (mosquitto) <── Home Assistant
```

- 灯泡 → `homeassistant/light/rg660mk_bulb/config`（Discovery 配置）+ state/command 主题
- 摄像头/传感器 → 类似方式

### 3.3 阻塞点

1. 需要一台运行中的 MQTT broker（本机 `sudo apt install mosquitto`，或 RG660MK 上跑）。
2. 需要一个 Home Assistant 实例（本机 docker 或独立设备），并配置 MQTT integration。

### 3.4 下一步路线

1. 部署 MQTT broker（本机 apt 装 mosquitto，或 docker 起 eclipse-mosquitto）。
2. RG660MK 上写 MQTT 发布脚本（paho-mqtt，或纯 stdlib 最小 MQTT 客户端），发布灯泡/设备状态。
3. 部署 Home Assistant 实例，配置 MQTT integration，自动发现 RG660MK 设备。
4. 双向：HA 下发开关 → MQTT → RG660MK → `bulb_control.py`。

---

## 四、Hermes 交接状态

| 能力 | Hermes 是否可调 | 说明 |
|------|----------------|------|
| 人脸识别身份 | ⚠️ 接口已接、模型缺失 | `hermes_ai_tool.py face` 已接入 rg660mk-device-control skill，但缺模型返回错误 |
| Home Assistant | ❌ 未接 | 需先有 broker + HA 实例 |

---

## 五、结论

- 人脸识别的**代码链路已 100% 就绪**，唯一缺口是「2 个 NCNN 模型 + vision_runner 的 face 操作 C++ 实现」。
- Home Assistant 接入的**方案明确（MQTT Discovery）**，缺口是「broker + HA 实例」两个外部环境。
- 两者都不属于「一键部署」，需要新建模型/环境。本项目的语音、视觉、智能家居、运维四大块里，**除这两项外的 17 个功能点已全部落地**。
