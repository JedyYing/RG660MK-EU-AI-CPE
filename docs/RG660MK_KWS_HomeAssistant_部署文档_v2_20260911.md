# RG660MK KWS 唤醒提速 + Home Assistant 接入 — 部署文档（v2）

更新时间：2026-09-11

## 一、结论摘要

| 功能 | 部署状态 | 实现方式 | 备注 |
|------|---------|---------|------|
| KWS 唤醒提速 | ❌ 阻塞 | sherpa-onnx binary 是 glibc，RG660MK 是 musl | 需交叉编译或换方案 |
| MQTT broker | ✅ 已部署 | docker eclipse-mosquitto:2（本机） | 端口 1883 |
| RG660MK MQTT 桥接 | ✅ 已部署 | mqtt_bridge.py（procd daemon） | 灯泡双向控制已验证 |
| Home Assistant | ⏳ 进行中 | docker homeassistant/home-assistant（拉取中） | 待镜像下载完成 |

---

## 二、KWS 唤醒提速 —— 阻塞（glibc vs musl）

### 2.1 SG560D 方案

SG560D 用的是 `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`（ONNX KWS 模型），Android Kotlin 包装（WakeService.kt）。

### 2.2 RG660MK 阻塞点

- **sherpa-onnx 没有 musllinux wheel**：PyPI 只有 `manylinux2014_aarch64`（glibc），没有 `musllinux_aarch64`。
- **prebuilt binary 是 glibc 动态链接**：`libsherpa-onnx-c-api.so` NEEDED `libc.so.6`、`ld-linux-aarch64.so.1` 等 glibc 库，RG660MK (musl) 跑不了。
- 实测推送 binary 到 RG660MK，执行报 `not found`（找不到 glibc loader）。

### 2.3 可行路线

| 方案 | 复杂度 | 备注 |
|------|--------|------|
| 交叉编译 sherpa-onnx（aarch64-linux-musl toolchain）| 高 | 需要 onnxruntime 的 musl 版本 |
| 在 RG660MK 上跑 glibc 兼容层（拷贝 ld-linux + libc.so.6 + libstdc++ 等）| 中 | 可能冲突 |
| 换其他轻量 KWS 库（如 Picovoice Porcupine、Snowboy）| 中 | 需 license 或自建 |
| 接受现状（whisper 全量转写 ~10s 延迟）| 低 | 当前可用 |

**建议**：接受现状，或等 sherpa-onnx 出 musl wheel。

---

## 三、Home Assistant 接入 —— 进行中

### 3.1 已部署

| 组件 | 位置 | 状态 |
|------|------|------|
| MQTT broker | 本机 docker `eclipse-mosquitto:2` | ✅ 运行中，端口 1883 |
| RG660MQ MQTT 桥接 | `/data/ai_cpe/mqtt_bridge.py` (procd daemon) | ✅ 运行中 |
| MQTT Discovery | `homeassistant/light/rg660mk_bulb/config` | ✅ 已发布 |
| 灯泡双向控制 | `rg660mk/bulb/set` (订阅) + `rg660mk/bulb/state` (发布) | ✅ 已验证 |

### 3.2 mqtt_bridge.py 用法

```sh
python3 /data/ai_cpe/mqtt_bridge.py status    # 单次：发布 Discovery + 状态
python3 /data/ai_cpe/mqtt_bridge.py daemon    # 常驻：订阅命令 + 发布状态 + 心跳
```

procd init：`/etc/init.d/mqtt_bridge`（已 enable + start）。

### 3.3 Home Assistant 容器（拉取中）

```sh
docker pull homeassistant/home-assistant:stable
docker run -d --name homeassistant --restart unless-stopped \
  -p 8123:8123 -v /home/jedyying/ha_config:/config \
  -e TZ=Asia/Shanghai homeassistant/home-assistant:stable
```

### 3.4 下一步（HA 镜像拉完后）

1. 访问 `http://192.168.1.244:8123` 完成 HA 初始化。
2. 配置 MQTT integration（broker: `host.docker.internal` 或本机 IP `192.168.1.244`）。
3. HA 自动发现 `RG660MK Bulb`（通过 MQTT Discovery）。
4. 双向控制验证：HA UI 开关 → MQTT → RG660MK → `bulb_control.py`。

---

## 四、总结

- **KWS 唤醒提速**：阻塞在 glibc vs musl，当前 whisper 全量转写 ~10s 可用。
- **Home Assistant**：MQTT 层已打通（双向控制已验证），HA 容器拉取中，待初始化。
- **人脸识别（身份识别）**：已部署（走 Immich buffalo_s，前次会话完成）。
