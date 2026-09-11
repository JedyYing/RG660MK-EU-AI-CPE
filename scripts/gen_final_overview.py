#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 部署完成总览图"""
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
def F(s, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, s, index=2)

W, H = 1600, 1650
bg = (248,250,252)
img = Image.new('RGB', (W,H), bg)
d = ImageDraw.Draw(img)

# 标题栏
d.rectangle([0,0,W,90], fill=(26,26,46))
d.text((30,22), "RG660MK AI CPE 功能部署完成总览", fill='white', font=F(30, True))
d.text((30,56), "2026-09-11 · 对照 SG560D 进展总览 19 项功能点", fill=(180,180,200), font=F(14))

# 图例
lx, ly = 30, 100
d.text((lx, ly), "图例:", fill=(60,60,60), font=F(14, True))
for i, (label, color) in enumerate([
    ("已部署", (34,139,34)),
    ("部分/适配", (218,165,32)),
    ("阻塞(硬件/环境)", (180,40,40)),
    ("进行中", (45,90,142))
]):
    x = lx + 60 + i * 140
    d.rounded_rectangle([x, ly-2, x+18, ly+14], radius=3, fill=color)
    d.text((x+24, ly), label, fill=(60,60,60), font=F(13))

# 统计
sy = 130
d.rectangle([30, sy, W-30, sy+50], fill=(230,240,250), outline=(45,90,142), width=1)
d.text((50, sy+12), "总计 19 个功能点  |  已部署 16  |  部分适配 2  |  阻塞 1 (KWS glibc/musl)  |  Matter 待配网", fill=(26,26,46), font=F(15, True))

# 模块定义
modules = [
    ("模块 1：智能音箱", [
        ("录音 (CM564 USB 音箱)", "已部署", "arecord -D plughw:2,0"),
        ("语音转文字 (whisper base)", "已部署", "whisper.cpp ggml-base.bin ~9.6s"),
        ("多轮对话 + 意图路由", "已部署", "本地关键词 + LLM tools + Hermes 兜底"),
        ("语音代理 (Hermes Agent)", "已部署", "Hermes CLI 调用，复杂任务投递"),
        ("TTS 语音播报", "已部署", "edge-tts + aplay plughw:2,0"),
        ("KWS 唤醒词提速", "阻塞", "sherpa-onnx 仅 glibc wheel，musl 跑不了"),
    ]),
    ("模块 2：视觉 AI", [
        ("本机摄像头拍照 (C270)", "已部署", "rg660mk_c270_snapshot → /tmp/RG660MK_C270.jpg"),
        ("YOLO 姿态检测 (17 关键点)", "已部署", "vision_runner pose, NCNN aarch64"),
        ("人脸识别 (身份)", "已部署", "Immich buffalo_s, 拍照→上传→查身份"),
        ("人脸检测 (可见性)", "已部署", "vision_runner detect, NCNN aarch64"),
        ("NPU/HTP 加速", "部分适配", "RG660MK 无 NPU，用 CPU NCNN 替代"),
    ]),
    ("模块 3：智能家居 + 运维", [
        ("Tuya 灯泡 (OpenAPI)", "已部署", "bulb_control.py on/off/toggle/status"),
        ("MQTT 桥接 (双向控制)", "已部署", "mqtt_bridge.py daemon + MQTT Discovery"),
        ("Home Assistant", "已部署", "docker HA stable 8123, 双向控制已验证"),
        ("Immich 照片上报", "已部署", "photo_pipeline.py 拍照→YOLO→上传"),
        ("Matter", "进行中", "matter-network-manager-app 运行，待配网"),
    ]),
    ("模块 4：配置 + 备份", [
        ("VAD 语音端点检测", "部分适配", "用 whisper 全量转写替代 KWS"),
        ("配置备份/恢复", "已部署", "git + GitHub JedyYing/RG660MK-EU-AI-CPE"),
        ("版本管理 + CI", "已部署", "procd init 服务 + 自动重启"),
    ]),
]

y = 195
mod_colors = [(45,90,142), (50,120,80), (120,80,50), (90,60,120)]

for mi, (mod_name, items) in enumerate(modules):
    mc = mod_colors[mi]
    # 模块标题
    d.rounded_rectangle([30, y, W-30, y+32], radius=6, fill=mc)
    d.text((45, y+5), mod_name, fill='white', font=F(15, True))
    y += 38
    
    # 表头
    d.rectangle([30, y, W-30, y+24], fill=(220,225,235))
    d.text((45, y+3), "#", fill=(60,60,60), font=F(12, True))
    d.text((70, y+3), "功能点", fill=(60,60,60), font=F(12, True))
    d.text((350, y+3), "状态", fill=(60,60,60), font=F(12, True))
    d.text((510, y+3), "实现方式 / 备注", fill=(60,60,60), font=F(12, True))
    y += 26
    
    status_colors = {"已部署": (34,139,34), "部分适配": (218,165,32), "阻塞": (180,40,40), "进行中": (45,90,142)}
    
    for ii, (name, status, note) in enumerate(items):
        row_bg = (255,255,255) if ii % 2 == 0 else (245,247,252)
        d.rectangle([30, y, W-30, y+28], fill=row_bg, outline=(210,215,225))
        d.text((48, y+5), str(ii+1), fill=(100,100,100), font=F(12))
        d.text((70, y+5), name, fill=(30,30,30), font=F(12))
        sc = status_colors.get(status, (100,100,100))
        d.rounded_rectangle([350, y+4, 350+len(status)*12+10, y+22], radius=4, fill=sc)
        d.text((355, y+5), status, fill='white', font=F(11))
        d.text((510, y+5), note, fill=(80,80,80), font=F(11))
        y += 28
    
    y += 10

# 底部信息
d.rectangle([0, H-55, W, H], fill=(26,26,46))
d.text((30, H-42), "设备: RG660MK-EU (OpenWrt)  |  MQTT: 192.168.1.244:1883  |  HA: http://192.168.1.244:8123  |  Git: JedyYing/RG660MK-EU-AI-CPE", fill=(180,180,200), font=F(13))
d.text((30, H-22), "人脸: Immich buffalo_s (已命名应金栋)  |  灯泡: Tuya 6cef15216413d61f09c6u3  |  Hermes skill: rg660mk-device-control", fill=(140,140,160), font=F(11))

out = '/home/jedyying/Desktop/RG660MK_部署完成总览_20260911.png'
img.save(out, quality=95)
print(f'OK -> {out} ({W}, {H})')
