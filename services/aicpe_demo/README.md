# AI CPE 十大功能一键演示套件

**用途**：双击即可自动遍历运行 AI CPE 十项智能化功能，过程**实时可视化**（进度卡片 + 现场摄像头画面 + 滚动日志），
用于①版本质检自测 ②向领导/客户演示。

## 一键运行（在电脑桌面双击）
- `AI_CPE功能演示.desktop` —— 双击后自动：连设备（免密；优先 IPv6 直连 ULA `fdc3:8285:b409::1`）→ 在设备上启动新一轮演示 → 自动打开浏览器看板
- 终端窗口会保持打开显示状态（可随时关闭；仅当回退到链路本地地址时看板经窗口隧道转发，需保留窗口）
- 无桌面环境：`bash run_demo.sh`；测流程加 `--no-open`（不开浏览器）；静音质检加 `--no-audio`
- 连接说明：USB 直连优先走 IPv6（避开 192.168.1.0/24 与公司网/飞连静态路由的冲突）；看板服务为 IPv4+IPv6 双栈监听

## 看板内容
- 10 张功能卡片：实时状态（待运行/运行中/通过/失败/跳过）+ 关键证据 + 耗时
- 右侧：**C270 现场实时画面** + 滚动日志
- 顶部进度条；完成后显示总结横幅与报告路径

## 十项功能是怎么验的（都走生产路径，不造假）

| # | 功能 | 实测内容 |
|---|---|---|
| 1 | AI 智能语音对话 | 服务存活 + 常驻 ASR(:6006) + 现场录音转写 + 大模型对话（在线/本地可切）+ TTS 播报(CM564) |
| 2 | AI 视觉检测 | 抓帧 → YOLOv8n 目标检测 + YOLOv8n-pose 姿态 → 坐姿几何阈值判定 + 人脸身份（Immich） |
| 3 | 本地自然语言图片检索 | 自然语言指令（云海/泰晤士/最近/日期）→ Immich 检索 → 命中文件清单；零命中不兜底 |
| 4 | 终端播放视频卡顿诊断 | **视频流抽测**（帧率/码率）+ 单帧时延 → 诊断结论；DPI 解析 RTSP/RTP 流定位卡顿 |
| 5 | 智能家居控制 | MQTT 控制面 + Matter 真灯（node 10）/ 涂鸦兜底；三轮回读一致才判 pass，`proxy=true` 一律 fail |
| 6 | AI 智能选网 | QoE 驱动的蜂窝选网决策 · Shadow 观察（零写操作），邻区/候选实时可见 |
| 7 | MiniNAS 存储 | x86 存储底座（机身盘 + SMB/NFS + 相册 + CasaOS）只读巡检；SMB/NFS 端口探针 |
| 8 | CasaOS统一管理界面 | App Store / Files / Home Assistant / Immich / 每日巡检 五个入口可达性 |
| 9 | AI 视频找帧（自然语言） | 自然语言描述 → 本地 vindex 检索 → 截取目标帧；示例：演唱会视频找广州塔（命中第 9 秒） |
| 10 | 本地算力 · DeepSeek 7B | MT200（QCS9075）NPU 上的 `deepseek-7b-npu` 真实推理一次，记录端到端耗时/字符数/占空比 |

## 依赖与口径
- 语音对话依赖推理通道（在线 `ai.phicotek.com` 或本地 MT200）；图片检索/相册依赖 Immich；视频找帧依赖本地索引 `/data/vindex/`。
- 画面有人/无人、坐姿正常/异常、人脸未匹配、身份未匹配均属**场景状态**；只要链路执行成功即 PASS。
- 第 5 项只有在真实执行器（`matter` / `tuya`）三轮回读一致时判 pass；板载 LED 代理不算。
- 第 10 项在 MT200 离线时记 `skip`（环境不可用，不计失败），不会把整轮演示拖红。
- 第 5 项可按现场清单在 `/data/ai_cpe/demo_expected_skips.json` 标记预期跳过。

## 报告产出
- Markdown：`reports/AI_CPE_功能演示报告_<时间戳>.md`（含逐项证据）
- JSON：`reports/AI_CPE_功能演示_<时间戳>.json`（供二次统计）

## 设备侧文件
- 主程序：`/data/ai_cpe/hermes/home/aicpe_demo/aicpe_demo.py`（procd 服务 `aicpe-board`，8099）
- 视频找帧工具：`/data/ai_cpe/tools/vsearch.py` → 仓库副本 `services/aicpe_demo/tools/vsearch.py`
- 索引：`/data/vindex/concert/`（frames + index.json）
