# RG660MK 智能音箱语音助手（voice-assistant）

RG660MK AI CPE 上的离线语音对话助手：唤醒词 → 录音 → whisper 本地 ASR → 三路分流 → TTS 播报。可语音控制 Tuya 灯泡、查询时间/天气、拍照/上传 Immich、人脸检测、坐姿检测，复杂问题投递给边缘 Hermes Agent。

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
  ├─ 时间/日期            → 本地即刻回答（不调 LLM）
  ├─ 天气/灯泡/拍照/上传/人脸/坐姿 → 本地关键词 + 工具执行
  ├─ LLM 函数调用兜底       → 关键词没匹配（被误听）时，用 LLM function calling 理解意图
  └─ 复杂问题             → 投递 hermes chat -q（边缘完整 Agent）
  │
  ▼
TTS: edge-tts(mp3) → mpg123 → aplay → 绿联 CM564 3.5mm 耳机口 (plughw:2,0)
```

## 音频硬件

| 设备 | ALSA 卡 | 用途 | 备注 |
|------|---------|------|------|
| 绿联 UGREEN CM564 | card2 (`plughw:2,0`) | **麦克风 + 放音** | USB 音箱+内置麦克风一体机；无内置喇叭，音频从 3.5mm 耳机口出；内置 mic 信号弱，需软件 ×4 增益；USB **full-speed**（12Mbps）|
| Logitech C270 | card1 (`plughw:1,0`) | 摄像头拍照 + 备用麦克风 | USB **high-speed**；拍照用 `rg660mk_c270_snapshot` |

- 录音 `REC_DEV` 默认 `plughw:2,0`；放音 `PLAY_DEV` 默认 `plughw:2,0`。
- 硬件增益：`amixer -c 2 cset numid=6 255`。环境噪声 RMS≈29~43，×4 增益后 ≈172，阈值 `RMS_THRESHOLD=200`。
- ⚠️ C270 麦克风（plughw:1,0）识别更差（连「你好」都听不出），不要切换过去。

## 组件与设备路径

| 组件 | 路径 | 说明 |
|------|------|------|
| `voice_assistant.py` | `/data/ai_cpe/voice_assistant.py` | 主程序（venv python 3.11）|
| `vision_control.py` | `/data/ai_cpe/vision_control.py` | 人脸检测/坐姿检测（拍照+pose 推理）|
| `bulb_control.py` | `/data/ai_cpe/bulb_control.py` | Tuya 灯泡控制（纯 stdlib）|
| `tts_say.py` / `netdiag.py` | `/data/ai_cpe/` | 独立 TTS / 网络诊断 |
| whisper-cli | `/data/ai_cpe/whisper/whisper-cli` | whisper.cpp 1.9.3 |
| 模型 | `/data/ai_cpe/whisper/models/ggml-base.bin` | base 141MB（~9.6s/5s 片段）|
| C270 拍照 | `/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot` | libuvc 静态工具 |
| 一条龙上传 | `/data/ai_cpe/hermes/home/photo_pipeline.py` | 拍照→YOLO→上传 Immich |
| vision_runner | `/data/ai_cpe/demo/bin/vision_runner` | NCNN 推理（detect + pose）|
| procd init | `/etc/init.d/voice_assistant` | 开机自启 + respawn |

## 三路分流逻辑（`route(text)`）

按本地关键词优先级分流；**关键词没命中时先走 LLM 函数调用兜底（抗误听），再转 Hermes**：

1. **时间/日期**：完整的日期/时间问句（如“现在几点”“今天星期几”）→ 本地 `get_time_now()`；不再由“今天”“现在”单独触发。实际优先级为设备控制 → 天气 → **行情** → 日期时间 → 本地计算器 → LLM/Hermes。
2. **天气**：`天气/气温/温度/下雨/下雪/几度/冷不冷/热不热` → 提取城市 → `wttr.in`，失败后回退 Open-Meteo（HTTPS）。
2b. **行情**（L1 实时联网，与天气同层）：`股价/股票/行情/收盘/开盘/涨跌/涨幅/大盘/涨停/市值…`，或出现已知股票名/6 位代码 → `resolve_symbol()` 解析代码 → 腾讯 `qt.gtimg.cn`，失败回退新浪 `hq.sinajs.cn`。
   - 代码解析三级：显式代码（`603236`/`sh603236`）→ 本地别名表（含“移远通信”及 ASR 形近误听“亿元通信/一元通信”）→ smartbox 联网检索（`smartbox.gtimg.cn`，注意其返回是 ASCII `\uXXXX` 转义、不是 GBK）。
   - 播报措辞按行情时间戳区分：15:00 后或更早日期说“收盘”，盘中说“最新价”；名称用接口返回的正式名，避免把误听的名字念出来。
   - 名称解析不出来时**不谎称查过**，直接转 Hermes。
3. **灯泡**：`灯` → `开`→on / `关`→off → `bulb_control.py`。
4. **拍照**：`拍照/照相/拍一张/拍个照/拍个照片`
   - 含 `上传/传到/服务器/immich/相册/同步/保存` → `photo_pipeline.py` 一条龙（拍照→检测→传 Immich）
   - 否则 → 简单快拍 `rg660mk_c270_snapshot`
5. **人脸/坐姿检测**（`vision_control.py`）：
   - `坐姿/姿势/体态` → 坐姿检测
   - `人脸/脸检测/脸识别` → 人脸检测
   - **`检测/检查` 兜底** → 默认人脸检测（因为「人脸」「坐姿」名词常被误听成「冷凉」「人年」等，但「检测」两字稳定）
6. **LLM 函数调用兜底**：以上都没命中 → `llm_tools()` 用 function calling 理解意图（工具：get_weather / **get_stock_quote** / control_bulb / take_photo / detect_face / detect_posture）。
7. **复杂问题**：先使用 LLM 直接答案；无结果再调用 `hermes chat -q`。默认语音模型 `deepseek-v4-flash`（可用 `VOICE_LLM_MODEL` 覆盖）；等待提示由统一双超时管理。

> ⚠️ **模型的“我查不了”不是答案**。实测 deepseek-v4-flash 对股价/汇率/油价/新闻/车票一律回绝（“我暂时没法查询实时汇率…”）。这类文本若直接播报，就等于把 Hermes 兜底短路了 —— 用户听到的是一句回绝，而不是本可以查到的结果。因此 `dispatch()` 有两道闸：
> - `is_capability_refusal()`：识别回绝措辞 → 转 Hermes；
> - `needs_live_data()`：实时数据类问题**不带工具作答**就有编造数字的风险（比回绝更糟）→ 一律转 Hermes，绝不念出模型凭记忆写的数字。
>
> 另：`LLM_OR_HERMES` **不再享受 7 秒快速失败**（可能升级到 Hermes，实测约 29 秒），改用 §4 的 30/60 双超时。

> 「拍照上传」这类**有现成脚本的确定任务不要走 Hermes**——Hermes 在边缘设备跑完整 agent 要 2.5 分钟+，本地 photo_pipeline 几秒完成。

## 唤醒与持续对话

- 唤醒词「你好小皮」，**实际触发词是「你好」**——whisper 把「小皮」尾音可靠地误听成「夏丁/下爹/夏皮」（p→d 声母丢失），只有「你好」两字稳定识别，所以按「你好」触发。
- **持续对话模式**：唤醒后可连续下指令；24 秒未检测到语音或说退出词时回待机。唤醒与问题同句时直接处理问题。仅句首称呼触发，兼容“你好”及已知尾音误识别，不以“皮／吉”等单字触发。
- whisper 的噪音幻觉输出（`( ˘ω˘ )`、`( 字幕:J Chong )` 等带括号的）在 transcribe() 里直接过滤，避免掉进 Hermes 卡 3 分钟。

## 灯泡控制（Tuya 云 OpenAPI）

- MOES 灯泡 devId `6cef15216413d61f09c6u3`，Tuya 云 OpenAPI（新版签名）。
- 凭据在 `/data/hermes/.hermes/.env`（`TUYA_ACCESS_ID`/`TUYA_ACCESS_SECRET`）。开关实测 ~1.3s。

## 部署（procd）

```sh
procd_set_param command /data/hermes/venv/bin/python /data/ai_cpe/voice_assistant.py
procd_set_param env HOME=/data/hermes/home LD_LIBRARY_PATH=/data/ai_cpe/whisper/lib
procd_set_param respawn
```

- 重启只能 `/etc/init.d/voice_assistant restart`（procd 自动 respawn，手动 nohup 会双实例抢音频）。
- 日志：`logread | grep 'python['`（`[listen]`=待机转写、`>>> WAKE`=唤醒、`[said]`=命令、`[reply]`=回复、`[tool]`=工具执行）。

## 关键调试记录（2026-09-10）

1. **「把灯关掉」3 分钟无响应**：根因是唤醒词「小皮」被误听成「夏丁/下爹」，唤醒不触发。改为「你好」触发词。
2. **麦克风方向**：C270（plughw:1,0）识别更差（连「你好」都听不出）；CM564 稳定识别「你好」，保留 CM564。
3. **「拍照上传」卡 Hermes**：Hermes 在边缘设备跑完整 agent 要 2.5 分钟+。改为本地 `photo_pipeline.py` 一条龙（几秒完成）。
4. **「人脸/坐姿检测」被误听**：whisper 把「人脸」听成「冷凉」、把「坐姿」听成「嗯」。加「检测」兜底 + 强化 LLM 提示词后，`做冷凉检测` 也能正确路由到人脸检测。
5. **USB 音频异常态**：长乱码 TTS 会拖垮 CM564 USB 音频，复位修复：`rmmod snd_usb_audio` → `modprobe snd-usb-audio` → `amixer -c 2 cset numid=6 255`。`speak()` 已加长度截断（>120）+ 乱码过滤。
6. **whisper 速度**：base 模型干净环境下 ~9.6s/5s 片段（之前测的 24s 是被并发进程干扰）。tiny-q5_1 只快 ~15%（瓶颈在频谱计算而非模型大小），暂未换模型。

## 2026-09-16 补充修复

设备已部署并校验，详见 [修复与验证记录](修复与验证记录_20260916.md)。VAD 按 ×4 后的能量判断、输出 WAV 再做一次实际增益；最短有效语音 180ms，保留 150ms 尾音。ASR 合并全部有效转写行，失败返回空，录音开始前清除旧文件。

## 2026-09-18 修复：实时行情与“拒绝即终答”

用户反馈「问：请查看移远通信的收盘价 / 答：我查不了股市行情」。设备实测根因有三层：

1. `dispatch()` 把模型的回绝当成最终答案直接播报（`need_hermes=False`，1.5 秒返回），**Hermes 从未被调用** —— 但实测 Hermes 能答对（走 `execute_code` 抓腾讯/新浪接口），耗时约 29 秒。
2. 设计文档 §2.1 的 L1「实时联网」层只实现了天气，行情没有处理器，只能落到 L2。
3. **即使修好路由也还是坏的**：`hermes_ask()` 返回的 stdout 开头是英文推理框（`┌─ Reasoning ─┐`），`speak()` 先截断 120 字符再做中文占比过滤 → 实测 `cn=4 ratio=0.03 → filtered=True`，用户会听到“回复内容暂时无法播报”。新增 `extract_hermes_answer()` 从末尾取连续中文段落解决。

对应改动见 [修复与验证记录](修复与验证记录_20260916.md) 第四轮。回归测试 `tests/test_audio_pipeline.py::RealTimeQuoteTests`。

## 2026-09-20 新疆口音识别优化（P0/P1 逻辑层）

对应《RG660MK 智能音箱新疆口音识别优化设计 V1.0》，落地口音专项的**离线逻辑层**，不改动业务路由。测试见 [测试报告](新疆口音优化_测试报告_20260920.md)。

- **`accent_correct.py`（新增，纯 stdlib）**：口音近音纠错 + 热词偏置。
  - 口音模糊音节等价类：翘舌/平舌合并、前后鼻音合并、n/l 合并、f/h 相混（设计 §6.3 待验证混淆维度）；
  - `correct_command()`：音节级编辑距离 ≤1 的**受限纠错**，只把疑似口音误识别的控制类短句拉回已知命令（如「打开客厅登」→「打开客厅灯」、「关闭空挑」→「关闭空调」），可解释可回滚，绝不改开放域问答（§14 红线）；
  - `whisper_prompt()`：热词偏置词表。**默认关闭**（`VOICE_ASR_PROMPT=1` 启用）——2026-09-20 真机 A/B：全量热词会把含混语音解码成热词碎片（「你好小皮」→「阿梅，鲁，河田」）且每次 +2.2s；待真实口音录音 A/B 通过后方可启用（§6.1/§9）。
- **`classify()` 集成**：口音纠错挂在**最末兜底位**——标准普通话意图先命中就直接返回，绝不进纠错，保护基线不退化（§2）。
- **新疆地名**：CITIES 与天气链路补入乌鲁木齐/喀什/伊犁/克拉玛依/吐鲁番等 14 地名。
- ⚠️ **边界**：本次为离线逻辑验证。CER/命令准确率的量化改善、混淆规则阈值校准，需按设计 §7 用真实新疆口音录音在设备上验收——当前混淆规则均为**待真机混淆矩阵校准的初值**。

## 已知问题 / 待办

- whisper base ~9.6s/段，唤醒/命令仍有明显延迟；提速方向：缩短录音窗口（5s→3s）或录音与转写流水线并行，而非换模型。
- 拍照上传的「检测到 N 个人」计数偶有 YOLO 误检（2 人 vs 1 人），已从播报中移除计数。
- 「坐姿检测」个别发音会被 whisper 完全漏听（转成「嗯」/空），属语音识别固有问题。
- 人脸检测无专用模型（`face_detect=null`），用 pose 关键点（鼻子+眼睛）近似判断人脸可见性。
