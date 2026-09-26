# camav -- 摄像头画面 + 麦克风实时音频 合流服务

把 RG660MK 上 C270 摄像头的 **MJPEG 画面** 和 **C270 自带麦克风的声音** 合成一路，
对外提供一个浏览器可直接打开的页面 —— 实现「远程同时看画面 + 听现场声音」。

## 端点

| 路径 | 说明 |
|---|---|
| `/` | 页面：MJPEG 画面 + 「🔊 点击开启声音」按钮 |
| `/stream` | MJPEG 视频流（转发 `camview:8090`） |
| `/snapshot` | 单帧 JPEG |
| `/audio` | 实时音频：S16LE 单声道 16kHz，chunked 传输 |
| `/status` | 运行状态：`dev= / bytes= / clients= / uptime=` |

默认监听 `0.0.0.0:8092`，与 `camview`(8090) 并存、互不影响。

## 关键设计

1. **音频常驻单例采集 + 广播**
   一个 `arecord` 长期运行，数据写入 2 秒环形缓冲；所有观看者共享同一路数据。
   * 踩过的坑：最初「每个 HTTP 请求起一个 arecord」，客户端断开后进程被杀不干净，
     残留进程会**独占麦克风**，导致后续请求全部 503（`Resource busy`）。
   * 现在设备失联/麦克风被抢会自动重连（每 0.6s 重试）。

2. **播放端自动追帧**
   页面 JS 用 Web Audio API 直接播放 PCM；若播放进度落后于当前时间，
   自动跳到当前时间点，避免延迟无限累积。

3. **chunked 传输编码**
   音频用 `Transfer-Encoding: chunked` 逐块下发，让中间链路（Cloudflare 隧道）
   尽早转发。实测稳态吞吐可达 32KB/s（16kHz 单声道 S16 的实时速率）。

## 依赖

* 设备侧：需要 **`/data/hermes/venv/bin/python`**（含 Pillow，v3 起用于画质/缩放处理）+ `arecord`（OpenWrt 自带）。**不需要 ffmpeg**。
  * ⚠️ 2026-09-26 修复：v3 起**不能**用系统 `python3` 启动（无 PIL，会 `ModuleNotFoundError`）；`camav.init` 已固定用 venv python，部署时请以仓库内 init 为准。
* 麦克风：`MIC_DEV` 环境变量，默认 `plughw:1,0`（card 1 = Logitech C270 HD WEBCAM）。
  可用逗号分隔多个设备做回退，例如 `MIC_DEV=plughw:1,0,plughw:2,0`。

## 2026-09-26 重新部署记录

- **服务化补齐**：设备上 `/etc/init.d/camav` 曾缺失（camav 一直在以手工 nohup 进程运行）——
  本次按本文档正式安装并 `enable`（`S99camav`，重启不丢）。
- **修复潜伏缺陷**：设备文件自 9/22 起已是 v3（需 PIL），但实际运行的是**旧代码的内存副本**
  （文件更新后从未重启过），所以一直"看起来正常"。本次重启暴露并修复（venv python）。
- **修复后状态**：视频 288px/q38/2fps、音频按设计 16kHz 满速（32KB/s 稳态，隧道实测一致）、
  快照 ~36KB、procd respawn 守护。

## 部署

```sh
# 设备上
cp camav.py /data/ai_cpe/hermes/home/
cp camav.init /etc/init.d/camav
chmod +x /etc/init.d/camav
/etc/init.d/camav enable
/etc/init.d/camav start

# 自检
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8092/snapshot
curl -s -m 3 -o /tmp/a.raw http://127.0.0.1:8092/audio && ls -l /tmp/a.raw
curl -s http://127.0.0.1:8092/status
```

## 对外暴露（本次用 Cloudflare 快速隧道）

```sh
# 笔记本（与设备同一局域网）
./cloudflared tunnel --url http://192.168.1.1:8092
# 输出的 https://xxx.trycloudflare.com 给远端同事，打开后点「开启声音」
```
