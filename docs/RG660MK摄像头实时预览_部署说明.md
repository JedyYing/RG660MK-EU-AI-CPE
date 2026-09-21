# RG660MK 摄像头实时预览 — 部署说明

## 一句话结论

Logitech C270 接在 RG660MK 上,已通过**用户态 libuvc 方案**跑通实时网页预览:同一局域网内任意浏览器打开 `http://192.168.1.1:8090/` 即可看到实时画面。服务已配置开机自启,CPU 占用约 0.8%、内存约 12MB。

## 为什么不用标准方案(mjpg-streamer / ffmpeg)

排查发现设备内核**没有编译媒体子系统**(`CONFIG_MEDIA_SUPPORT is not set`),V4L2 既非内建也无模块,所以 C270 永远不会出现 `/dev/video0` 节点。而 mjpg-streamer、ffmpeg、OpenCV 全都依赖这个节点,在当前固件下无法使用。加之这是 Quectel 定制的 gem6xxx target,OpenWrt 官方源与归档站均无对应内核模块(全 404),在线 opkg 也连不上,靠装包补不回来。

采用的方案是 **libuvc(基于 libusb 的用户态 UVC 驱动)**:直接从 USB 抓取 C270 原生输出的 MJPEG 帧,完全绕过内核 V4L2,再由内置的轻量 HTTP 服务以 `multipart/x-mixed-replace` 推给浏览器。纯 JPEG 直通、不解码不转码,所以开销极小。程序为静态链接的 aarch64-musl 单文件,无任何动态库依赖。

## 部署内容

| 项 | 位置 |
| --- | --- |
| 预览程序(静态单文件,323KB) | 设备 `/data/camview/camview`(持久分区) |
| 开机自启脚本(procd) | 设备 `/etc/init.d/camview`,已 `enable`(`S95camview`) |
| 源码与交叉编译脚本 | 工作区 `rg660mk_work/camview/` 下 |

程序放在 `/data` 持久分区而非 `/tmp` 或 `/overlay`,重启不丢失(`/overlay` 当前仅剩约 780KB,不宜放大文件)。

## 使用方法

- **看画面**:同网段的电脑/手机浏览器打开 `http://192.168.1.1:8090/`,自动显示实时画面。
- **纯视频流**(可给 VLC、其它页面 img 标签嵌入):`http://192.168.1.1:8090/stream`
- **抓单张截图**:`http://192.168.1.1:8090/snapshot`(返回一张 JPEG)

LAN 区域防火墙 input 策略为 ACCEPT,无需额外开端口。

### 常用运维命令(SSH 登录设备后)

- 启停:`/etc/init.d/camview start` / `stop` / `restart`
- 关闭开机自启:`/etc/init.d/camview disable`
- 换参数(如分辨率、端口):编辑 `/etc/init.d/camview` 里的 `PORT`,或改 `procd_set_param command` 行追加 `--width 1280 --height 720 --fps 10`

程序支持的参数:`--port`(默认 8090)、`--width`/`--height`(默认 640×480)、`--fps`(默认 15)、`--vid`/`--pid`(默认 C270 的 046d:0825)。C270 在 1280×720 下帧率较低,若追求流畅建议维持 640×480。

## 验证结果

- USB 枚举:C270 稳定识别为 `046d:0825`。
- 采集:HTTP 抓帧返回合法 640×480 JPEG,画面清晰、曝光正常(实拍办公室场景)。
- 连续流:`/stream` 3 秒内约 30 帧(≈10 fps),连续无卡死。
- 资源:CPU 约 0.8%,内存约 12MB。
- 自启:`/etc/rc.d/S95camview` 链接已建立。

## 已知边界

- **摄像头独占**:libuvc 直接抓 USB,同一时刻只能有一个程序打开 C270。之前那套姿态检测程序(`pose_camera`)若同时运行会与本服务抢占设备,需二选一。
- **传输未加密**:MJPEG 走明文 HTTP,仅限可信局域网使用;若要跨网访问需自行加反向代理与鉴权。
- 若插拔摄像头或换设备,`--vid/--pid` 可能需相应调整。
- 工作区根目录早期的 `摄像头预览服务.py` / `摄像头预览.html` 是针对开发机本地摄像头的旧方案,与本次设备端部署无关,可忽略。
