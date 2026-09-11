# RG660MK KWS 唤醒提速 + Home Assistant 接入 — 部署文档（v3 最终版）

更新时间：2026-09-11

## 一、结论摘要

| 功能 | 部署状态 | 实现方式 | 备注 |
|------|---------|---------|------|
| KWS 唤醒提速 | ❌ 阻塞 | sherpa-onnx binary 是 glibc，RG660MK 是 musl | 需交叉编译或换方案 |
| MQTT broker | ✅ 已部署 | docker eclipse-mosquitto:2（本机） | 端口 1883 |
| RG660MK MQTT 桥接 | ✅ 已部署 | mqtt_bridge.py（procd daemon 自启） | 灯泡双向控制已验证 |
| Home Assistant | ✅ 已部署 | docker homeassistant/home-assistant:stable | 端口 8123，MQTT Discovery 已发现灯泡 |
| HA ↔ RG660MK 双向控制 | ✅ 已验证 | HA UI toggle → MQTT → bulb_control.py → 状态回传 | 延迟 < 1s |

## 二、KWS 唤醒提速 —— 阻塞

SG560D 方案：`sherpa-onnx-kws-zipformer-zh-en-3M`（ONNX KWS，Android Kotlin 封装）。

RG660MK 阻塞点：
- sherpa-onnx PyPI 只有 `manylinux_aarch64`（glibc），**无 musllinux wheel**
- prebuilt binary 依赖 `libc.so.6`、`ld-linux-aarch64.so.1`（glibc），RG660MK (musl) 执行报 `not found`
- 可行路线：交叉编译（工作量大）、等官方 musl wheel、或接受现状（whisper ~10s 延迟可用）

## 三、Home Assistant —— 已部署 + 已验证

### 3.1 组件清单

| 组件 | 位置 | 端口 | 状态 |
|------|------|------|------|
| MQTT broker | docker `eclipse-mosquitto:2` | 1883 (MQTT), 9883 (HTTP API) | ✅ |
| Home Assistant | docker `homeassistant/home-assistant:stable` | 8123 | ✅ |
| RG660MK MQTT 桥接 | `/data/ai_cpe/mqtt_bridge.py` (procd daemon) | — | ✅ |
| 灯泡 | RG660MK Bulb（MQTT Discovery 自动发现） | — | ✅ |

### 3.2 MQTT 主题

| 主题 | 方向 | 内容 |
|------|------|------|
| `homeassistant/light/rg660mk_bulb/config` | RG660MK → HA | Discovery 配置（retain） |
| `rg660mk/bulb/state` | RG660MK → HA | 灯泡状态 ON/OFF（retain） |
| `rg660mk/bulb/set` | HA → RG660MK | 开关命令 ON/OFF |
| `rg660mk/bulb/available` | RG660MK → HA | online/offline（retain） |

### 3.3 双向控制验证

1. HA UI toggle OFF → MQTT publish `rg660mk/bulb/set` = "OFF" → RG660MK mqtt_bridge → `bulb_control.py off` → 灯泡关 → publish state "OFF" → HA 更新
2. HA UI toggle ON → 同上反向 → 灯泡开
3. Activity log 记录完整（含时间戳）

### 3.4 登录信息

- URL: http://192.168.1.244:8123
- 用户名: admin
- 密码: admin123

### 3.5 容器管理

```sh
# 启动
docker start homeassistant
docker start mqtt

# 停止
docker stop homeassistant
docker stop mqtt

# 日志
docker logs -f homeassistant
docker logs -f mqtt
```

## 四、Hermes 交接

| 能力 | Hermes 是否可调 | 说明 |
|------|----------------|------|
| MQTT 灯泡控制 | ✅ | mqtt_bridge.py daemon 常驻，Hermes 可直接 pub/sub |
| Home Assistant API | ✅ | http://192.168.1.244:8123/api/ + long-lived token |

## 五、结论

- **Home Assistant 已完全打通**：MQTT broker + HA 容器 + RG660MK 桥接 + 双向控制验证通过
- **KWS 唤醒提速**：阻塞在 glibc vs musl，当前 whisper 全量转写 ~10s 可用
- **人脸识别**：已部署（走 Immich buffalo_s，前次会话完成）
- **Matter**：`matter-network-manager-app` 在 RG660MK 运行中（无 chip-tool）
