---
name: rg660mk-device-control
description: "Use when controlling RG660MK CPE local devices — 灯泡/摄像头拍照/YOLO视觉/坐姿巡检/网络诊断/智能音箱播报/语音对话."
version: 1.2.0
---

# RG660MK 本地设备控制（飞书 → Hermes → 设备）

本 skill 定义 RG660MK 上本地设备能力的调用方式。用户通过飞书对本 bot 下指令
（开灯/关灯/拍照/看画面/检测/查网络/让音箱说话等）时，按下面命令执行。

## 路径速查（全部已部署）

| 能力 | 入口 |
|---|---|
| 灯泡开关/状态 | `python3 /data/ai_cpe/bulb_control.py on\|off\|toggle\|status` |
| 拍照 (C270) | `/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot` → 输出到 `/tmp/RG660MK_C270.jpg` |
| 一键拍照→YOLO→Immich | `python3 /data/ai_cpe/hermes/home/photo_pipeline.py` |
| 人脸识别（身份） | `python3 /data/ai_cpe/face_recognize.py`（走 Immich 人脸识别） |
| 视觉 AI (HTTP) | `python3 /data/ai_cpe/demo/services/hermes_ai_tool.py <action> --payload '<json>'` |
| 坐姿巡检 | `python3 /data/ai_cpe/hermes/home/posture_check.py` |
| 网络诊断 | `python3 /data/ai_cpe/netdiag.py`（或 `... netdiag.py clients`） |
| 智能音箱 TTS 播报 | `/data/ai_cpe/tts_say.py <文本>` |
| 麦克风录音 | `arecord -D plughw:2,0 -f S16_LE -r 16000 -c 1 /tmp/rec.wav` |

## 1. 灯泡（Tuya 云 OpenAPI）

```sh
python3 /data/ai_cpe/bulb_control.py on       # 开灯
python3 /data/ai_cpe/bulb_control.py off      # 关灯
python3 /data/ai_cpe/bulb_control.py toggle   # 翻转
python3 /data/ai_cpe/bulb_control.py status   # 查询状态
```

- 凭证从环境变量 `TUYA_ACCESS_ID/SECRET` 或 `/data/hermes/.hermes/.env` 读取。
- 灯泡 devId `6cef15216413d61f09c6u3`，DP 码 `switch_led`，仅 2.4G SSID「jedy」。
- 返回 `success:true` = 指令已下发。设备离线/签名错误会返回 error，不要臆测成功。

## 2. 拍照（Logitech C270）

```sh
/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot
```

- 无参数，拍完输出到 `/tmp/RG660MK_C270.jpg`（640x480 MJPEG）。
- stdout 出现 `PHOTO_OK=/tmp/RG660MK_C270.jpg` 即成功。
- 后续要给用户看/做视觉分析，先 `cp /tmp/RG660MK_C270.jpg /data/ai_cpe/demo/media/<名字>.jpg`
  （见第 3 节 media_roots 约束），或直接跑 photo_pipeline.py 一条龙。

## 3. 视觉 AI（YOLO detect/pose + 人脸可见性）

服务 = `ai_service.py`，回环 HTTP `127.0.0.1:8765`（procd 托管，`/etc/init.d/ai_service`）。
CLI 客户端 = `hermes_ai_tool.py`，action ∈ health/detect/face/posture/asr/kws/tts/metrics。

```sh
# 健康检查
python3 /data/ai_cpe/demo/services/hermes_ai_tool.py health

# 目标检测 / 姿态 / 人脸可见性
python3 /data/ai_cpe/demo/services/hermes_ai_tool.py detect --payload '{"input":{"path":"/data/ai_cpe/demo/media/xxx.jpg"}}'
python3 /data/ai_cpe/demo/services/hermes_ai_tool.py posture --payload '{"input":{"path":"/data/ai_cpe/demo/media/xxx.jpg"}}'
```

**关键约束：**
- `input.path` 必须是**绝对路径**且落在 `media_roots` = `/data/ai_cpe/demo/media/` 内，
  否则 ai_service 拒绝（HTTP 400）。拍照产物在 `/tmp`，务必先 cp 进 media 目录。
- 返回 JSON：`{"ok":true,"result":{...}}`。detect → `detections[]`(class_name/score/bbox)；
  pose → `persons[]`(bbox+score+17关键点)；posture → 坐姿角度判断。失败时 `ok:false` 带 error。
- 推理约 800ms/图（CPU 2 线程），超时用 `--timeout 30`。
- 服务挂了：`/etc/init.d/ai_service restart`（不要手动 nohup，会双实例）。

## 4. 一条龙（拍照→检测→上传 Immich）

```sh
python3 /data/ai_cpe/hermes/home/photo_pipeline.py
```

拍照 + YOLO detect/pose + 上传 Immich（`http://192.168.1.244:2283/api/assets`，key 在
`/data/ai_cpe/hermes/home/photos/.immich_key`）。适合用户说「拍一张看看」时用。

## 4b. 人脸识别（身份识别，走 Immich）

```sh
python3 /data/ai_cpe/face_recognize.py
```

C270 拍照 → 上传 Immich → Immich ML（InsightFace buffalo_s）自动识别身份 → 返回匹配到的人名。
适合用户说「人脸识别 / 识别一下 / 这是谁」时用。

- 依赖本机 Immich（192.168.1.244:2283）的人脸识别（`facialRecognition.enabled=true`）。
- 已识别的人需在 Immich 里命名（`PUT /api/people/{id}` `{"name":"..."}`），否则返回「未命名的人」。
- 无人/未识别时返回「没有识别到人脸」。识别约需 6~8s（ML 异步）。

## 5. 坐姿巡检

```sh
python3 /data/ai_cpe/hermes/home/posture_check.py
```

语义：无人脸 → stdout 空 exit0；有人脸 → 上传 Immich；坐姿异常 → stdout 打印警告文本。
用户问「现在坐姿/有没有人在」时用它。cron 每小时已在跑（`/etc/crontabs/root`）。

## 6. 网络诊断

```sh
python3 /data/ai_cpe/netdiag.py            # 接口/路由/蜂窝 + 客户端全景
python3 /data/ai_cpe/netdiag.py clients    # 只看已连接客户端（MAC/IP/厂商/信号）
```

用于回答「谁连了我的网」「信号怎么样」「WAN 通不通」。每客户端 MAC 会查
macvendors.com 得厂商（20:f1:b2=Tuya，首字节 bit0x02 置位=随机隐私 MAC）。

## 7. 智能音箱 TTS 播报（绿联 CM564 USB 音箱）

```sh
/data/ai_cpe/tts_say.py 你好，我是智能音箱       # 直接参数
echo "文本" | /data/ai_cpe/tts_say.py             # 从 stdin
```

- 链路：edge-tts（云端，晓晓中文女声）→ mp3 → mpg123 解码 → aplay 播放到 `plughw:2,0`（绿联音箱）。
- 用户说「播报/说话/读一下/音箱说 XX/用音箱说 XX」时用它。飞书文字 → 本 bot → tts_say.py → 音箱出声。
- 录音（音箱自带 mic hw:2,0，或 C270 摄像头 mic hw:1,0）：
  `arecord -D plughw:2,0 -f S16_LE -r 16000 -c 1 /tmp/rec.wav`（Ctrl-C 停止）。
- 依赖已部署：`/data/ai_cpe/bin/mpg123`、edge-tts 在 `/data/hermes/venv`、内核模块
  `snd-usb-audio`（开机自动加载，见 `/etc/modules.d/31-sound-usb-audio`）。
- 故障排查：音箱没声先 `cat /proc/asound/cards` 确认 card2=UGREEN CM564；模块没加载
  `insmod /lib/modules/5.15.134/snd-usb-audio.ko`。

## 8. 语音对话助手（唤醒词"你好小皮"）

procd 服务 `voice_assistant`（常驻，开机自启），入口 `/data/ai_cpe/voice_assistant.py`。

- **唤醒**：对 C270 摄像头 mic 说「你好小皮」→ 触发（whisper ASR 检测唤醒词，识别到「小皮」即唤醒）。
- **对话**：唤醒后说话 → whisper 转写 → LLM(deepseek-v4-pro) → edge-tts 播报，循环。
- **退出**：说「休息/睡觉/再见/晚安」，或静音 24 秒自动退出回到监听。
- 语音输入用 C270 mic(hw:1,0)；绿联音箱的 mic 无信号（CM564 是 3.5mm 外接 mic 口，未插麦克风）。
- 依赖已部署：`/data/ai_cpe/whisper/whisper-cli` + `models/ggml-base.bin`（141MB）、
  `lib/`（libwhisper/libggml 动态库，需 `LD_LIBRARY_PATH=/data/ai_cpe/whisper/lib`）、
  `/data/ai_cpe/bin/mpg123`、edge-tts。
- 服务管理：`/etc/init.d/voice_assistant restart`；日志 `logread | grep voice`。
- 识别延迟 ~9s/句（base 模型 CPU encode 7.7s），唤醒全链路 ~13s，属正常，别误判为无响应。

## 坑

- **Hermes 的 terminal 工具 shell 已坏**：`/bin/sh` 是 busybox 软链，terminal 用 login shell 调命令会报
  `/bin/sh: builtin: not found`（exit 126，任何命令都如此）。设备命令改用 `execute_code` +
  `subprocess.run(["/bin/busybox","sh","-c", cmd])` 执行（已验证可行），不要直接用 terminal 工具。
- **busybox 无 `pkill`**，杀进程按 PID：`for pid in $(ps w|grep '<pat>'|grep -v grep|awk '{print $1}'); do kill -9 $pid; done`。
- **`lsusb` 可能缺失**，识别 USB 设备用 `for d in /sys/bus/usb/devices/*/; do cat $d/idVendor $d/idProduct $d/product; done`（C270=046d:0825，绿联音箱=2b89:0234）。
- 灯泡凭据/Immich key 是敏感信息，回给用户时不要贴明文。
- 所有设备脚本都在 `/data/ai_cpe/`，Hermes 以 root 运行可直接访问，无需 sudo。
- **TTS 播放是同步阻塞的**（edge-tts 合成 + 播放约几秒），tts_say.py 会等到播完才返回；长文本更久，别设过短超时。
