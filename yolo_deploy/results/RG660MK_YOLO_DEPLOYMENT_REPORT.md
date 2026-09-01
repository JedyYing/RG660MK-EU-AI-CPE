# RG660MK-EU YOLO 姿态/坐姿检测部署报告

- 日期:2026-08-31
- 目标设备:RG660MK-EU(MediaTek T930,Cortex-A55 ×4 同构,aarch64,OpenWrt/musl)
- 编译主机:Ubuntu 22.04 x86_64(sh-d-l-000766d)
- 模型路线:**pose-only**(仅 yolov8n-pose,不加载第二个 yolov8n 检测模型)
- 总体状态:**PARTIAL**(PC 侧全链路打通并验证一致;设备端 A/B/C 因 SSH/adb 凭据缺失被阻塞)

---

## 0. 结论速览

- 已在 PC 端完成:资料盘点、算法核验、模型结构核对、**pose-only C++ 程序实现**、host 与 **aarch64 全静态交叉编译**、ABI 校验、**10 场景 PC 基线**与解码正确性验证、性能可扩展性验证、完整部署包与四段自动化脚本。
- **唯一阻塞点**:无法登录 RG660MK(SSH `publickey,password` 均被拒;adb 无设备)。之前接入依赖的临时 key `/tmp/kiro_rg660_usb_host_20260825_ed25519` 已被清理。设备端静态验证(阶段A真机)、摄像头性能(阶段B)、稳定性(阶段C)与持久化安装均需先恢复访问。
- 二进制为**完全静态、AArch64、无 Vulkan/OpenMP/OpenCV/Python 依赖**,ABI 特征与此前在该机成功运行的 `rg660mk_c270_snapshot` 一致;接入后可直接推送执行。

---

## 1. 实际执行过的关键命令(节选)

```
# 环境/资料
uname -a; adb devices -l; lsusb
find $HOME -name test_face_posture.py -o -name 'model.ncnn.*' ...
sha256sum <模型/源码>                     # -> evidence/model_sha256.txt

# 设备连通
ping -c2 192.168.1.1                       # 通,RTT ~2ms
ssh -v root@192.168.1.1                    # Permission denied (publickey,password)

# 模型重导出(320/416/640,独立目录,原640按SHA256字节还原)
python3 -c "from ultralytics import YOLO; YOLO('yolov8n-pose.pt').export(format='ncnn', imgsz=320, half=False)"

# host 编译 + 基线
g++ -O2 -std=c++17 pose_detector.cpp app_image.cpp libncnn.a -fopenmp -lpthread -o pose_image_host
./pose_image_host <param> <bin> <scene>.jpg --size 320 --json pc_baseline_320.jsonl

# aarch64 交叉编译(OpenWrt musl 工具链, 全静态)
aarch64-openwrt-linux-musl-g++ -O3 -mcpu=cortex-a55 -std=c++17 -static \
  pose_detector.cpp app_image.cpp libncnn.a \
  -Wl,--start-group -lstdc++ -lgcc -lgcc_eh -lpthread -lm -Wl,--end-group -o pose_image_arm64
file/readelf -h/-l/-d pose_image_arm64     # AArch64, statically linked, 无动态依赖

# musl 重编 libusb/libuvc(原库为 glibc,含 __*_chk 与 musl 不兼容)
./configure --host=aarch64-openwrt-linux-musl CC=...-gcc --enable-static --disable-udev
aarch64-openwrt-linux-musl-gcc -c libuvc/src/*.c ... ; ar rcs libuvc.a
```

## 2. 设备证据

| 项 | 值 | 来源 |
|---|---|---|
| 网络可达 | 192.168.1.1 ping 通,RTT ~2ms,经网卡 enx9c69d3c6b35d | logs/device_probe.log |
| SSH | TCP/22 开放,认证失败 `publickey,password` | ssh -v |
| adb | 无设备(未在 gadget 模式) | adb devices |
| USB | lsusb 无 Quectel(2c7c) | lsusb |
| 架构(既有二进制推断) | AArch64,静态 musl,与 rg660mk_c270_snapshot 一致 | file/readelf |

> 设备只读探测项(uname/cpuinfo/free/df/thermal/video 节点/摄像头格式)已在 `scripts/00_device_probe.sh` 就绪,SSH 恢复后一键采集,自动追加到 device_probe.log。CPU/内存/存储/温度节点等字段待真机回填。

## 3. 源码与模型

- 原算法:`yolo_demo/test_face_posture.py`(PC 端 ultralytics,双模型 yolov8n + yolo11n-pose,摄像头 40 帧)。坐姿几何(肩倾>10°、head_drop>-0.15、|head_forward|>0.35、trunk>12°)与人脸框(头部关键点外扩 1.6×+10px)已 **1:1 移植**到 C++ 并保持阈值。
- 部署模型:`yolov8n-pose`(Ultralytics 8.3.0,COCO-17,pose 任务)。原始 640 导出模型 SHA256 见 evidence,**已按字节还原,未被覆盖**。
- 关键发现:原 640 NCNN 导出把 anchor grid **硬编码为 640**,喂 320/416 时 `extract` 返回 rc=-1。故按"优先 320/416"要求,用同一 `.pt` **重新导出 320/416/640 三套**(独立目录 package/models/),320 输出 [56,2100]、416 输出 [56,3549]、640 输出 [56,8400],均解码正确。

## 4. 编译器 / NCNN / 模型版本

| 组件 | 版本/配置 |
|---|---|
| 交叉工具链 | OpenWrt SDK 23.05.0 armsr-armv8,GCC 12.3.0,**musl** |
| NCNN | 1.0.20260827(源码 ncnn-20260526-full-source.zip) |
| NCNN 编译 | Release,`-O3 -mcpu=cortex-a55`,**NCNN_VULKAN=OFF**,静态库,NCNN_THREADS=ON。OpenMP_*_FLAGS=NOTFOUND ⇒ 实际走 pthread 线程池,**二进制无 libgomp 依赖** |
| Ultralytics/PyTorch/pnnx | 8.3.0 / torch 2.12.1 / pnnx(随 ncnn 1.0.20260526 whl) |
| 运行期选项 | `use_vulkan_compute=false`,num_threads 可配 1..4,FP32,lightmode=on |

## 5. ABI 与依赖检查

```
pose_image_arm64 : ELF 64-bit AArch64, statically linked, stripped
pose_camera_arm64: ELF 64-bit AArch64, statically linked, stripped
readelf -l : 无 INTERP 段        => 静态链接 ✅
readelf -d : 无 NEEDED           => 无动态依赖 ✅
依赖扫描   : 无 Vulkan/OpenMP/OpenCV/Python/Torch ✅
```
- 摄像头依赖 libusb/libuvc:原提供版本为 **glibc** 编译(含 `__snprintf_chk/__open_2`),与 musl 工具链链接失败;已用 musl 工具链**重新编译**为静态库(无 chk 符号),成功链入 `pose_camera_arm64`。

## 6. 静态图片一致性(阶段A)

- **PC 侧已完成**:10 场景基线 `results/baseline_comparison.json`(无人=0、单人=1、多人=2、遮挡仍检出),解码/坐姿/人脸区域/NMS 均正确(多人场景输出 2 个不重叠框)。空场景假阳(score≤0.31)由 **conf 阈值 0.35** 干净抑制,真人 score~0.91 不受影响。
- **设备侧待执行**:`scripts/10_stageA_static_ab.sh` 会推送 `pose_image_arm64`+模型+同批场景图到设备推理,回取后用 `compare_baseline.py` 逐图判定(人数一致 / 框 IoU≥0.90 / 关键点误差≤1%宽高 / 坐姿标签一致)。因设备阻塞尚未运行。

## 7. 性能表

真机数值待阶段B回填(`results/benchmark.csv`)。下表为 **x86_64 host 参考**(仅证明多尺寸/多线程链路可用,**非设备性能**):

| 输入 | 1线程 | 2线程 | 4线程 |
|---|---|---|---|
| 320 | 39.0 ms | 26.6 ms | 22.4 ms |
| 416 | 66.6 ms | 46.1 ms | 37.9 ms |
| 640 | 158.2 ms | 104.2 ms | 129.7 ms |

- 320 相对 640 快约 4×,推荐首选 **320 + 2线程**。T930(A55×4 @ 通常 ~2GHz)单核约为该 x86 核 1/4~1/6,预计 320@2T 单帧推理约 120~250ms(**约 4~8 FPS**,满足 ≥1 FPS 目标的概率高),需真机实测确认。

## 8. 30 分钟稳定性(阶段C)

待真机执行。`scripts/30_stageC_stability.sh` 默认 size=320/threads=2 连续 1800s,每 30s 采样 VmHWM/温度/丢帧,并 ping 外网核查 CPE 业务未受影响;采集器与推理线程已解耦、只保留最新帧(丢积压)。验收门槛:无崩溃/OOM/内存持续增长/摄像头掉线;温度不逼近 trip point;320 下 ≥1 FPS;不影响 5G/Wi-Fi/路由。

## 9. 最终部署路径与启停

- 安装包:`package/`(bin + models/pose_320|416|640 + config/pose.conf + service/ + install.sh)
- 设备端安装(阶段A/B/C 通过后):`sh install.sh`(依 df 自动选 /data 或 /overlay,≥64MB;不放 /tmp;不覆盖既有 config)
- 启停:`/etc/init.d/pose_detect start` / `stop`;`enable` 才开机自启(默认不 enable)
- procd:`respawn 30 10 0` 断线带退避重试,`nice 10` 让路 CPE 主业务

## 10. 回滚方法

```
/etc/init.d/pose_detect disable
/etc/init.d/pose_detect stop
rm -f /etc/init.d/pose_detect
rm -rf <持久目录>/pose        # 例 /data/pose 或 /overlay/pose
```
全程未修改固件/未刷机/未重启设备/未动 5G/Wi-Fi/USB/路由;PC 端原 640 模型已按 SHA256 字节还原。无持久化改动残留(尚未安装到设备)。

## 11. 未完成项与真实阻塞原因

| 项 | 状态 | 阻塞/说明 |
|---|---|---|
| 设备只读探测(真机) | 待执行 | SSH 凭据缺失 |
| 阶段A 真机一致性 | 待执行 | 同上;脚本+比对器已就绪 |
| 阶段B 摄像头性能 | 待执行 | 同上 |
| 阶段C 稳定性+持久化 | 待执行 | 同上 |
| QEMU 本机跑 aarch64 | 放弃 | `qemu-user-static` 需 sudo 密码,非交互不可装 |

**解阻塞需要用户操作(二选一)**:
1. 用 USB 让设备进入 adb 模式,把新公钥写入设备 `/etc/dropbear/authorized_keys`(参考历史命令),或
2. 提供设备 root 口令 / 一把设备已授权的 SSH 私钥。

恢复后按序运行:`00_device_probe.sh` → `10_stageA_static_ab.sh` → `20_stageB_camera_bench.sh` → `30_stageC_stability.sh` → `package/install.sh`。全部 PASS 才可标 PASS 并 enable 服务。

---

## 附:命名与合规

- 人脸相关一律输出字段 `face_region`(**人脸区域估算**),基于鼻/眼/耳关键点几何外扩,**非人脸识别、不做身份识别**。
- 未使用"绑定大核";T930 为 4× 同构 A55,仅线程数调优。
- INT8 未启用(要求 FP32 正确后再量化,校准集须用真实摄像头图)。
