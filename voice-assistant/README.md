# RG660MK 智能音箱语音助手（voice-assistant）

RG660MK AI CPE 上的离线语音对话助手：唤醒词 → 录音 → whisper 本地 ASR → 三路分流 → TTS 播报。可语音控制 Tuya 灯泡、查询时间/天气、调用摄像头拍照，复杂问题投递给边缘 Hermes Agent。

## 系统拓扑

```
用户说话
  │
  ▼
绿联 CM564 内置麦克风 (plughw:2,0, USB full-speed, 信号弱需 ×4 数字增益)
  │  arecord 48kHz S16_LE 单声道
  ▼
whisper.cpp (ggml-base.bin) 本地转写  ← 唯一 ASR，离线
  │
  ▼
三路分流 (voice_assistant.py)
  ├─ 时间/日期         → 本地即刻回答（不调 LLM）
  ├─ 天气/灯泡/拍照    → 本地关键词 → 工具执行（wttr.in / Tuya / C270）
  └─ 复杂问题          → 投递 hermes chat -q（边缘完整 Agent）
  │
  ▼
TTS: edge-tts(mp3) → mpg123 → aplay → 绿联 CM564 3.5mm 耳机口 (plughw:2,0)
```

## 音频硬件

| 设备 | ALSA 卡 | 用途 | 备注 |
|------|---------|------|------|
| 绿联 UGREEN CM564 | card2 (`plughw:2,0`) | **麦克风 + 放音** | USB 音箱+内置麦克风一体机；无内置喇叭，音频从 3.5mm 耳机口出；内置 mic 信号弱，需软件 ×4 增益；USB **full-speed**（12Mbps）|
| Logitech C270 | card1 (`plughw:1,0`) | 摄像头拍照 + 备用麦克风 | USB **high-speed**；拍照用 `rg660mk_c270_snapshot` |

- 录音设备 `REC_DEV` 默认 `plughw:2,0`（CM564 内置 mic）；放音 `PLAY_DEV` 默认 `plughw:2,0`（CM564 耳机口）。
- 硬件增益：`amixer -c 2 cset numid=6 255`（Mic Capture Volume 拉满）。
- 环境噪声基线：raw RMS≈29~43，×4 增益后 ≈172，唤醒判定阈值 `RMS_THRESHOLD=200`。

## 组件与设备路径

| 组件 | 路径 | 说明 |
|------|------|------|
| `voice_assistant.py` | `/data/ai_cpe/voice_assistant.py` | 主程序（venv python 3.11）|
| `bulb_control.py` | `/data/ai_cpe/bulb_control.py` | Tuya 灯泡控制（纯 stdlib）|
| `tts_say.py` | `/data/ai_cpe/tts_say.py` | 独立 TTS 播报工具 |
| `netdiag.py` | `/data/ai_cpe/netdiag.py` | 网络诊断工具 |
| whisper-cli | `/data/ai_cpe/whisper/whisper-cli` | whisper.cpp 1.9.3 |
| 模型 | `/data/ai_cpe/whisper/models/ggml-base.bin` | base 141MB |
| mpg123 | `/data/ai_cpe/bin/mpg123` | mp3 解码（交叉编译）|
| C270 拍照 | `/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot` | libuvc 静态工具 |
| procd init | `/etc/init.d/voice_assistant` | 开机自启 + respawn |

## 三路分流逻辑

`route(text)` 按本地关键词优先级分流，不依赖 LLM 判断：

1. **时间/日期**：命中 `几点/时间/日期/几号/星期/今天/现在/什么时候` → 本地 `get_time_now()`。
2. **天气**：命中 `天气/气温/温度/下雨/下雪/几度/冷不冷/热不热` → 提取城市 → `wttr.in`。
3. **灯泡**：命中 `灯` → `开`→on / `关`→off / 否则 status → `bulb_control.py`。
4. **拍照**：命中 `拍照/照相/拍一张/拍个照/拍个照片` → C270 snapshot。
5. **兜底**：以上都不中 → 投递 `hermes chat -q`（边缘 Agent），先播「请稍等」。

> 说明：代码里也保留了 LLM function-calling 路径（`llm_tools`，天气/灯泡/拍照走工具 LLM），但当前 `route()` 实际走的是上面的本地关键词分流，更快更稳。

## 唤醒词与 ASR

- 唤醒词：**「你好小皮」**（含容错「下皮/小屁/小pipi」等）。
- **关键调试结论（2026-09-10）**：whisper base 在这块弱麦上把「小皮」的尾音「皮」**可靠地误听**成「夏丁/下爹」等（p 声母丢失 → d），导致按「皮」字匹配唤醒词经常失败、用户要反复说。
  - 修复：唤醒判定增加 `"你好" in text` 作为触发词 —— whisper 每次都能正确识别「你好」，不依赖不可靠的「皮」尾音。
- 唤醒后播「在呢，请说」，进入 24s 活跃监听窗口（`ACTIVE_TIMEOUT`），连续静音超时则播「我先休息了」回到待机。

## 灯泡控制（Tuya 云 OpenAPI）

- MOES 灯泡 devId `6cef15216413d61f09c6u3`，走 Tuya 云 OpenAPI（新版签名算法）。
- 签名与错误码排查详见 skill `tuya-smart-devices`（`references/tuya-openapi-signing.md`）。
- 凭据在 `/data/hermes/.hermes/.env` 的 `TUYA_ACCESS_ID` / `TUYA_ACCESS_SECRET`。
- 开关指令实测 ~1.3s 发出。

## 部署（procd）

`/etc/init.d/voice_assistant`：

```sh
procd_set_param command /data/hermes/venv/bin/python /data/ai_cpe/voice_assistant.py
procd_set_param env HOME=/data/hermes/home LD_LIBRARY_PATH=/data/ai_cpe/whisper/lib
procd_set_param respawn
```

- 用 venv 的 python（有 edge_tts 等三方依赖）；`LD_LIBRARY_PATH` 指向 whisper 的 `libggml*.so`。
- 重启只能走 `/etc/init.d/voice_assistant restart`（procd 自动 respawn，手动 nohup 会起第二个实例抢音频设备）。
- 日志：`logread | grep 'python['`（`[listen]`=待机监听转写、`[said]`=活跃期命令、`[reply]`=回复、`>>> WAKE`=唤醒命中）。

## 关键调试记录（2026-09-10）

1. **「把灯关掉 3 分钟无响应」根因**：不是灯泡/路由问题（bulb off 实测 1.3s、路由正确），而是**唤醒词「小皮」被 whisper 误听成「夏丁/下爹」**，唤醒不触发，用户反复说 + 每段转写慢（~24s）累积成 3~6 分钟。
2. **麦克风方向**：切到 C270（`plughw:1,0`）反而更差（连「你好」都识别不出，全是 `( ˘ω˘ )`/`( 無法 動作 )` 噪音幻觉）；CM564 至少稳定识别「你好」。最终保留 CM564。
3. **USB 音频设备异常态**：长乱码 TTS 播放会拖垮 CM564 USB 音频（唤醒后误听/无响应），复位修复：
   `rmmod snd_usb_audio` → `modprobe snd-usb-audio` → `amixer -c 2 cset numid=6 255`。已在 `speak()` 加长度截断（>120 字符）+ 乱码过滤，防止再次长播报。
4. **whisper 速度**：base 模型在 4×A55 上 ~24s/5s 片段；q5_1/tiny 量化提速有限（~19s，瓶颈不在模型大小），暂未换模型。

## 已知问题 / 待办

- whisper base ~24s/段，交互仍有明显延迟；若要提速需从音频长度（缩短 `LISTEN_SECS`）或流水线（录音与转写并行）入手，而非仅换模型。
- 唤醒词「你好」触发较宽，可能被环境「你好」误触发（家庭场景可接受，待观察）。
- 拍照结果（`/tmp/RG660MK_C270.jpg`）尚未接入人脸/坐姿检测后的自然语言回执，目前仅播「拍照完成」。
