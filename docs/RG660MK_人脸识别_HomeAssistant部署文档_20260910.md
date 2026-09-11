# RG660MK 人脸识别（身份识别）+ Home Assistant 接入 — 部署文档

生成时间：2026-09-10（更新 2026-09-11）

## 一、结论摘要

| 功能 | 部署状态 | 实现方式 | 备注 |
|------|---------|---------|------|
| 人脸识别（身份识别）| ✅ **已部署** | Immich 云人脸识别（InsightFace buffalo_s）| 复用本机 Immich，无需本地模型 |
| Home Assistant 接入 | ❌ 未接入 | MQTT Discovery（设计就绪）| 缺 HA 实例 + MQTT broker |

**人脸识别已落地**：走本机 Immich 的人脸识别（原本就启用 buffalo_s 模型），RG660MK 拍照→上传→查身份，识别到的人名通过语音播报。Home Assistant 仍需外部环境（HA 实例 + broker）。

---

## 二、人脸识别（身份识别）—— 已部署（Immich 方案）

### 2.1 为什么用 Immich 而不是本地模型

- RG660MK 本地做人脸 embedding 需新建 NCNN 模型 + 改 vision_runner（C++ 交叉编译），工程量大。
- SG560D 原项目的人脸识别本就是走 Immich 云平台（进展总览第 12 项「Imminich人脸识别平台」）。
- 本机 Immich 2.7.5 已启用 `facialRecognition`（模型 `buffalo_s`，minScore 0.7），已识别 2 个人。
- RG660MK 已有 Immich 上传链路（photo_pipeline.py），复用即可。

### 2.2 实现

新增 `face_recognize.py`（部署在 `/data/ai_cpe/face_recognize.py`）：

```
C270 拍照 → 上传 Immich（/api/assets）→ 等 ML 异步识别（~6~8s）→ 查 asset.people → 返回人名
```

- 已识别的人命名：`PUT /api/people/{id}` `{"name":"应金栋"}`（已命名 1 人）。
- 无人/未识别 → 返回「没有识别到人脸」。

### 2.3 已接入的调用入口

| 入口 | 方式 |
|------|------|
| 语音助手 | 说「人脸识别 / 识别一下 / 这是谁」→ `face_recognize.py` |
| Hermes | `rg660mk-device-control` skill 新增「人脸识别（身份）」 |

### 2.4 实测

`python3 face_recognize.py` → `识别到 1 个人：应金栋`（用户正对镜头时）。

---

## 三、Home Assistant 接入 —— 未接入（方案就绪）

### 3.1 现状与阻塞

- 本机（192.168.1.244）无 Home Assistant 实例（端口 8123 关闭）。
- 本机无 MQTT broker：仅装 mosquitto-clients（客户端），未装 broker 守护进程（安装需 sudo）。
- RG660MK 已有 Tuya 云（灯泡）、Matter 等设备接入。

### 3.2 推荐方案：MQTT Discovery

```
RG660MK ──(MQTT pub/sub)──> MQTT broker (mosquitto) <── Home Assistant
```

- 灯泡 → `homeassistant/light/rg660mk_bulb/config`（Discovery）+ state/command 主题
- 摄像头/传感器 → 类似方式

### 3.3 下一步路线

1. 部署 MQTT broker（本机 apt 装 mosquitto，或 docker eclipse-mosquitto）。
2. RG660MK 写 MQTT 发布脚本（paho-mqtt 或纯 stdlib 最小 MQTT 客户端）。
3. 部署 Home Assistant 实例，配置 MQTT integration，自动发现 RG660MK 设备。
4. 双向：HA 下发开关 → MQTT → RG660MK → bulb_control.py。

---

## 四、Hermes 交接状态

| 能力 | Hermes 是否可调 | 说明 |
|------|----------------|------|
| 人脸识别身份 | ✅ 可调 | `face_recognize.py` 已接入 skill，语音+Hermes 均可触发 |
| Home Assistant | ❌ 未接 | 需先有 broker + HA 实例 |

---

## 五、结论

- **人脸识别（身份识别）已通过 Immich 方案落地**，语音 + Hermes 均可调用，无需本地模型。
- **Home Assistant 接入**仍卡在外部环境（HA 实例 + MQTT broker），方案已明确（MQTT Discovery）。
- 至此，原 SG560D 项目 19 个功能点中，除 Home Assistant 外已全部落地（含人脸识别）。
