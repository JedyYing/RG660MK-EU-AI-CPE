# RG660MK-EU AI CPE Demo：Hermes 基于 USB 算力棒的移植实施文档 V2.2

- 编制日期：2026-08-24
- 目标平台：Quectel RG660MK-EU
- Agent 基线：Hermes Agent v0.19.0（设备已安装）
- 首选算力棒：Hailo-10H / ASUS UGen300 8GB
- 执行目录：当前工作区 `AI_CPE_Demo/`
- 设备部署根目录：`/data/ai_cpe/`

> 核心变化：Hermes 是 RG660MK-EU 上唯一的会话、工具编排和消息网关主体；USB 算力棒只提供本地推理。不得重装 Hermes，不得让 NPU Runtime 直接修改 CPE 网络配置，不得在 USB Host/SuperSpeed 未实测通过时安装驱动或采购定型。

## 0. 结论先行与现场状态

截至 2026-08-24，迁移的软件接口、Hermes skill、Gate 脚本和回滚脚本已经形成，但硬件链路不能进入驱动安装阶段：

| 项目 | 实机证据 | 判定 |
|---|---|---|
| 系统 | OpenWrt 23.05.0，Linux 5.15.134，aarch64 Cortex-A55 | 已确认 |
| 资源 | 约 1.49 GB RAM、无 Swap；`/data` 2.7 GB，约 2.0 GB 可用 | 受限，必须单并发和限内存 |
| CPE 数据面 | `ccmni3=10.42.13.53/29`，默认路由存在；公网 ICMP、DNS 正常 | 短时检查通过 |
| USB 能力声明 | DT：`dr_mode=otg`、`maximum-speed=super-speed-plus`，控制器 `mediatek,mtu3` | 仅能力证据 |
| USB 当前状态 | UDC `configured`，`current_speed=high-speed`；无 Host root bus/外设枚举 | Gate 0 `BLOCKED` |
| Hermes | `/data/ai_cpe/hermes`，v0.19.0，gateway PID 12701，Feishu 已配置 | 进程基线通过 |
| 模型调用 | 自定义 provider 返回 HTTP 429，总额度超限 | Gate 2 `BLOCKED` |
| Hailo/UGen300 | 未接入、未枚举、未装 Runtime | 按 Gate 规则未执行 |

“设备树支持 OTG/SS+”不等于“当前载板外露口已处于 Host/10Gbps”。当前 ADB 正占用该控制器的 Device/UDC 方向，不能擅自切换；切换前必须先建立 RJ45 或串口带外管理并确认载板布线、供电和 role 控制方法。

## 1. 目标与非目标

### 1.1 目标

1. 保持 RG660MK-EU 的 5G CPE 完整稳定，路由注册、WAN、RJ45 LAN、DHCP、DNS、NAT、Firewall 的优先级最高。
2. 保留设备现有 Hermes 安装、配置、会话、Feishu 网关和模型提供商设置。
3. 通过统一 AI Service 将 Hailo Runtime 与 Hermes 隔离，提供视觉、音频和指标 API。
4. 迁移摄像头→视觉→MQTT/Home Assistant/灯→Hermes 回复的闭环，并可选本地 TTS。
5. 每个 Gate 独立输出命令、原始日志、证据、PASS/FAIL/BLOCKED、影响和下一步决策。
6. AI 失败时允许只停止 AI；Hermes 失败时允许单独停止 Hermes；任何降级都不能影响 CPE 网络。

### 1.2 非目标

- 不在第一阶段重做 RG660MK-EU 固件或内核。
- 不重装或迁移现有 Hermes 环境。
- 不默认 Wi-Fi 8、蓝牙、片内 AI NPU 或 Android APK Runtime 可用。
- 不把 Qualcomm QNN/HTP 二进制当作可直接迁移产物。
- 不修改网络核心配置、固件启动、分区或未知 AT 参数。
- 不以理论 40 TOPS 推导系统 FPS；只承认实测端到端指标。

## 2. 修正后的系统架构

```text
5G SA/NSA
   │
RG660MK-EU CPE 网络面（最高优先级，AI 不得修改）
   ├── RJ45/Wi-Fi LAN 客户端
   ├── Hermes Agent v0.19.0
   │     ├── 对话/云模型
   │     ├── Feishu/后续 MQTT 与 Home Assistant 工具
   │     └── rg660mk-local-ai skill
   │               │ loopback JSON API
   │               ▼
   ├── AI Service :127.0.0.1:8765
   │     ├── /health /metrics
   │     ├── /vision/detect /vision/face
   │     ├── /audio/asr /audio/kws /tts
   │     └── request_id、timeout、错误码、资源限额
   │               │
   ├── HailoRT / 模型 Runtime（Gate 3 后才允许安装）
   │               │
   └── USB Host + 有源 USB 3.x Hub
         ├── Hailo-10H / UGen300
         ├── UVC Camera
         ├── USB Mic + Speaker
         └── 可选 BLE Dongle
```

进程优先级：`cpe/network` > `hermes` > `ai-service` > `ai-runtime` > `capture/audio`。AI 进程必须可停、限内存、限并发；Hermes 不直接打开 `/dev/bus/usb` 或加载 NPU 驱动。

## 3. 设备目录与进程边界

现有目录保持不动：

```text
/data/ai_cpe/hermes/
├── hermes.sh
├── venv/
├── wheels/
├── home/
└── .hermes/              # 配置、会话、日志、skills；含敏感信息
```

新增内容只部署到：

```text
/data/ai_cpe/demo/
├── ai_runtime/
├── ai_models/
├── services/
│   └── hermes_ai_tool.py
├── config/
│   └── ai-service.json
├── media/
├── logs/
├── run/
├── tests/
└── rollback/
```

Hermes skill 目标路径：

```text
/data/ai_cpe/hermes/.hermes/skills/embedded/rg660mk-local-ai/SKILL.md
```

配置与日志不得打印 provider key、Feishu secret、token 或完整用户数据。设备端可写数据放 `/data`；根 overlay 仅约 21 MB，不得用于模型、Runtime 或长期日志。

## 4. 统一 AI Service 契约

```text
GET  /health
POST /vision/detect
POST /vision/face
POST /audio/asr
POST /audio/kws
POST /tts
GET  /metrics
```

### 4.1 通用请求

```json
{
  "request_id": "uuid",
  "timeout_ms": 15000,
  "input": {"path": "/data/ai_cpe/demo/media/frame.jpg"},
  "options": {}
}
```

### 4.2 通用成功与失败

```json
{"ok":true,"request_id":"uuid","result":{},"latency_ms":85}
```

```json
{
  "ok": false,
  "request_id": "uuid",
  "error": {"code": "MODEL_UNAVAILABLE", "message": "detect model is not loaded"}
}
```

错误码至少包含：`INVALID_ARGUMENT`、`AI_SERVICE_UNAVAILABLE`、`MODEL_UNAVAILABLE`、`RUNTIME_UNAVAILABLE`、`USB_DISCONNECTED`、`TIMEOUT`、`RESOURCE_LIMIT`。Hermes 只能依据 `ok` 和业务结果判定成功，不能只看 CLI 退出码。

本移植包中的 `services/hermes_ai_tool.py` 已实现 loopback 限制、2 MB 响应上限、有限超时、request_id 和结构化错误；在 AI Service 尚未启动时会明确返回 `AI_SERVICE_UNAVAILABLE`。

## 5. Hermes 替换设计

### 5.1 已确认基线

- 启动器：`/usr/bin/hermes -> /data/ai_cpe/hermes/hermes.sh`
- 环境：`HOME=/data/ai_cpe/hermes/home`，`HERMES_HOME=/data/ai_cpe/hermes/.hermes`
- 入口：`/data/ai_cpe/hermes/venv/bin/hermes`
- 网关：`hermes gateway run`，当前为手工后台进程，不是 system service
- 模型：`deepseek-v4-pro`，自定义 provider
- 消息平台：Feishu 已配置
- 当前命令 allowlist：`execute_code`

### 5.2 编排方式

`hermes-skill/rg660mk-local-ai/SKILL.md` 使用设备已验证的 Hermes skill frontmatter 格式。由于当前 OpenWrt 终端工具可能退化，skill 要求通过 `execute_code + subprocess.run` 调用 `hermes_ai_tool.py`，并遵守：

1. 第一次推理前先 `/health`。
2. 本地 AI 不可用时，保持普通云端对话和消息网关运行。
3. 推理失败至多补一次 health 检查，不循环重试。
4. MQTT/HA 动作必须以真实响应确认，不能以意图识别成功代替执行成功。
5. TTS 仅在 USB Audio 健康时启用。

### 5.3 已发现的 Hermes 验收陷阱

实机最小请求收到 HTTP 429，但 Hermes v0.19.0 的 one-shot 进程仍返回退出码 0。因此 `scripts/gate2_hermes_baseline.sh` 使用“精确哨兵回复”作为通过条件，并将配额错误归类为 `BLOCKED`。

`hermes doctor` 会执行 29 项联网检查，并可能自动调用 pip 安装可选依赖，不符合只读 Gate。不得在基线 Gate 中运行；本次超时遗留进程已停止，Hermes gateway 未受影响，`boto3` 未安装。

## 6. USB、Camera、Audio、BLE 约束

1. Gate 0 必须同时证明：Host 正在运行、SuperSpeed root bus 可见、算力棒枚举速度至少 5000M；设备树声明不能代替运行态证据。
2. 当前 ADB 链路是 UDC high-speed。若同一物理控制器切 Host，ADB 可能断开；必须先准备 RJ45/串口管理。
3. 首选有独立 12V 供电、至少四口、USB 3.2 Gen2/10Gbps 的有源 Hub，避免 RG660MK-EU 直接给 NPU、Camera、Audio、BLE 供电。
4. Camera 首轮 720p/MJPEG、15fps；先测单摄像头，再加 NPU，再加 Audio。
5. USB Audio 优先 UAC Mic+Speaker，先做 20 秒录放，再做连续对话。
6. BLE 仅使用独立 USB Dongle；不假定板载 Bluetooth 可用。
7. 若载板只有 USB 2.0 或无法稳定 Host，立即采用 RJ45 外置 AI 主机，不修改 CPE 主路径。

## 7. 模型迁移原则

原设备的 QNN/HTP Runtime、DSP/HTP 二进制、APK JNI 产物不直接复用。每个模型必须保留：

1. 原框架、输入输出、预处理、后处理和测试样本。
2. 可交换格式（优先 ONNX，其次 TFLite/框架原生）。
3. Hailo 编译链版本、校准集、量化参数、HEF/目标 artifact SHA-256。
4. 原平台与 Hailo 的准确率、mAP、关键误差样例。
5. RG660MK-EU 上的 FPS、P50/P95 延迟、CPU、内存、温度和 USB 错误。
6. 回滚到云 API 或 RJ45 外置 AI 主机的路径。

推荐顺序：人脸/检测 → VAD/KWS → ASR → 其他。CPU 负责 MJPEG 解码、resize、色彩转换、归一化、NMS 和业务规则；若 CPU 抖动，优先降分辨率/帧率。

## 8. 分阶段 Gate

| Gate | 内容 | PASS 条件 | 当前状态 |
|---|---|---|---|
| 0 | USB Host + SuperSpeed 运行态 | Host root bus；>=5000M；无反复断连 | `BLOCKED` |
| 1 | CPE 30 分钟基线 | 5G/WAN/DHCP/DNS/NAT 稳定；无 OOM/重启 | 未进入；短时 WAN/DNS 通过 |
| 2 | Hermes 基线 | ARM64/版本固定；网关、最小回复、30 分钟稳定 | `BLOCKED`：provider HTTP 429 |
| 3 | Hailo/UGen300 兼容 | 枚举、官方 runtime 安装、样例 100 次、拔插恢复 | 未进入 |
| 4 | 模型迁移 | 精度、FPS/P95、资源和回滚通过 | 未进入 |
| 5 | 多外设并发 | Hub+NPU+Camera+Audio 30 分钟，CPE 无回归 | 未进入 |
| 6 | 完整 Demo | 视觉→MQTT/HA→灯→Hermes 回复；异常可恢复 | 未进入 |

### Gate 0 决策

运行 `scripts/gate0_inventory.sh`。只允许在 `RESULT=PASS` 后进入 Gate 1。当前结果为 `BLOCKED`：DT 声明 OTG/SS+，运行态却是 Device/UDC high-speed，没有 Host root bus。

解除阻塞需要：

1. 确认载板哪个接口连接 `USB 3.2 Gen2 x1`，并确认 VBUS、CC/ID、role-switch、Hub 供电。
2. 通过 RJ45 或 DB9/UART 建立不依赖该 USB 控制器的管理通道。
3. 连接有源 USB 3.x Hub 和算力棒，重新采集 `/sys/bus/usb/devices/*/{idVendor,idProduct,speed}` 与 dmesg。
4. 若只能得到 480M，报告“性能风险”，不得宣称 SuperSpeed PASS。

### Gate 1

仅在 Gate 0 PASS 后执行：

```bash
cd AI_CPE_Demo
DURATION_S=1800 ./scripts/gate1_cpe_baseline.sh
```

测试期间 AI 服务保持关闭；记录默认路由、公网连通、可用内存、温度、OOM/watchdog/reset/USB 错误。

### Gate 2

先补充模型 provider 额度，再执行：

```bash
cd AI_CPE_Demo
./scripts/gate2_hermes_baseline.sh
```

精确返回 `HERMES_BASELINE_PASS` 才通过；HTTP 429 即使退出码为 0 也判 `BLOCKED`。不得运行会自动安装依赖的 doctor 全量检查。

### Gate 3～6

- Gate 3 前锁定 HailoRT ARM64/musl 或容器可用性、内核模块要求、Secure Boot/签名要求；没有官方兼容矩阵不得购买定型。
- Gate 3 只装 runtime 与官方样例，不先迁移业务模型。
- Gate 4 每次只迁移一个模型。
- Gate 5 固定接入顺序：有源 Hub → NPU → Camera → Audio → BLE。
- Gate 6 保留约 3 秒端到端初始目标，至少两轮灯控并验证异常断网、NPU 拔出和 Camera 拔出后的恢复。

## 9. 测试与验收

| 测试项 | 方法 | PASS 标准 |
|---|---|---|
| USB Host | sysfs/dmesg | Host role/root bus稳定，无反复 reset/disconnect |
| SuperSpeed | `/sys/bus/usb/devices/*/speed` | 算力棒对应设备 >=5000M |
| NPU Runtime | 官方样例/设备工具 | 识别成功；样例 100 次无错误 |
| Camera | v4l2 或采集服务 | 720p/MJPEG 30 分钟连续，无中断 |
| Audio | 录音/回放 | 20 秒录音清晰；无持续 underrun/overrun |
| Hermes | gateway + 精确哨兵 | 网关存活；收到期望文本；30 分钟无回归 |
| MQTT/HA | topic/payload/灯控 | 3 秒内动作；两轮一致 |
| CPE | ping/DNS/HTTPS/业务流 | AI 前后无断链；DHCP/NAT 正常 |
| 资源 | mem/thermal/dmesg/metrics | 无 OOM、watchdog、USB 错误；温度可接受 |
| 故障注入 | 断 NPU/Camera/网络 | AI 降级但 CPE、Hermes 可按设计保留 |

## 10. 回滚

- 停止 AI：设备端执行 `/data/ai_cpe/demo/rollback/stop_ai.sh`。脚本只接受已知 pidfile 和命令名，保留 Hermes 与网络。
- 停止 Hermes：显式执行 `/data/ai_cpe/demo/rollback/stop_hermes.sh`。脚本只终止命令行匹配 `hermes gateway run` 的 PID。
- NPU/Capture/Audio 失败：停止对应 AI 进程并拔出外设；不改 CPE 网络。
- USB Host 不稳定：恢复原硬件连接并采用 RJ45 外置 AI 主机。
- 模型失败：切回上一个已验收 artifact 或显式启用云端替代；不得无提示静默切云。

禁止回滚脚本执行 `dd`、`mkfs`、`fdisk`、`parted`、刷机、恢复出厂、未知 AT、网络重载或广泛 `pkill`。

## 11. 采购门槛

当前状态下不得定型采购 Hailo-10H/UGen300。采购前必须获得：

1. 载板 USB Host/SuperSpeed 运行态证据。
2. 对方明确支持 RG660MK-EU 的 ARM64 OpenWrt/musl 环境，而非仅写“ARM Linux”。
3. HailoRT 版本、内核模块/用户态依赖、kernel 5.15 适配和样例。
4. 中国区供货、授权、退换与技术支持书面确认。
5. 有源 USB 3.x Hub、短线、UVC Camera、UAC Audio、USB 功率计；Jetson/工业 AI Box 只作 fallback。

## 12. 安全部署步骤（待 Gate 0/2 解锁后）

本地检查并打包：

```bash
cd AI_CPE_Demo
python3 -m py_compile services/hermes_ai_tool.py
python3 services/hermes_ai_tool.py health
```

在未安装 AI Service 时，第二条应返回结构化 `AI_SERVICE_UNAVAILABLE`，这是预期结果。

Gate 0、Gate 1 与 Gate 2 全部通过后，才允许部署接口层：

```bash
adb shell 'mkdir -p /data/ai_cpe/demo/services /data/ai_cpe/demo/config /data/ai_cpe/demo/rollback'
adb push services/hermes_ai_tool.py /data/ai_cpe/demo/services/
adb push config/ai-service.example.json /data/ai_cpe/demo/config/ai-service.json
adb push scripts/stop_ai.sh /data/ai_cpe/demo/rollback/stop_ai.sh
adb push scripts/stop_hermes.sh /data/ai_cpe/demo/rollback/stop_hermes.sh
adb shell 'chmod 0755 /data/ai_cpe/demo/services/hermes_ai_tool.py /data/ai_cpe/demo/rollback/*.sh'
```

skill 在变更前必须备份现有 Hermes 配置目录中的目标路径；部署后仅重启 Hermes gateway，不重启设备或网络。当前未执行这些设备写入，因为 Gate 0 和模型额度仍阻塞。

## 13. 最终判定

Hermes 替换的软件设计和可执行接口已完成；实机已确认 Hermes 安装、运行方式、Feishu、资源与 CPE 基线，并修复了原 Gate 流程中遗漏的 Hermes 独立基线。完整 USB NPU Demo 尚不能宣称完成，原因不是软件命名，而是两个可复现的外部阻塞：

1. USB 当前为 UDC high-speed，没有 Host/SuperSpeed 运行态证据。
2. Hermes 模型 provider 总额度超限，最小文本请求返回 HTTP 429。

在这两个条件解除前，正确动作是保持 CPE 与 Hermes 现状、保存本移植包并停止越级安装；若载板无法给出 Host/SuperSpeed，则按设计切换到 RJ45 外置 AI 主机。
