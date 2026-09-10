#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 RG660MK 功能部署状态总览图（PNG），仿照原 SG560D 进展总览布局。"""
from PIL import Image, ImageDraw, ImageFont

# ---- 字体 ----
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
def F(size, bold=False):
    if bold:
        return ImageFont.truetype(FONT_BOLD, size, index=2)
    return ImageFont.truetype(FONT_PATH, size, index=2)

# ---- 数据：(模块名, [(功能点, 状态, 能力), ...]) ----
# 状态: 0=已部署(绿) 1=部分/适配(黄) 2=未部署(红)
DATA = [
    ("1. 智能音箱", [
        ("录音采集", 0, "arecord 48kHz（CM564 mic）"),
        ("语音转文字", 0, "whisper base 本地 ASR"),
        ("VAD/KWS 唤醒", 1, "whisper 全量转写做唤醒（无专用 KWS）"),
        ("多轮对话/意图路由", 0, "LLM 函数调用 + Hermes"),
        ("语音代理链路", 0, "Hermes chat -q"),
        ("TTS 播报", 0, "edge-tts + mpg123 + aplay"),
    ]),
    ("2. 视觉 AI 与推理", [
        ("本机拍照", 0, "C270 snapshot（640×480）"),
        ("YOLO 姿态检测", 0, "vision_runner pose（17 关键点）"),
        ("NPU/HTP 推理", 1, "NCNN CPU（RG660MK 无 NPU）"),
        ("告警推送", 1, "posture_check 告警（推送链路待完善）"),
        ("Immich 上报", 0, "photo_pipeline.py 一条龙上传"),
        ("人脸识别（身份）", 2, "仅人脸可见性，缺 embedding 模型"),
    ]),
    ("3. 智能家居与平台接入", [
        ("Home Assistant 接入", 2, "未接入（需 HA 实例）"),
        ("涂鸦/MQTT 设备接入", 0, "Tuya 云 OpenAPI（bulb_control.py）"),
        ("Matter 协议接入", 0, "Matter chip-tool 已部署"),
    ]),
    ("4. 运维配置与访问", [
        ("配置备份", 0, "git 备份到 GitHub"),
        ("手机访问界面", 0, "LuCI Web UI"),
        ("ADB 与设备调试", 0, "adbd_usb"),
        ("版本备份", 0, "git 版本管理"),
    ]),
]

STATUS_TXT = {0: "已部署", 1: "部分/适配", 2: "未部署"}
STATUS_COLOR = {0: (46, 160, 67), 1: (200, 140, 0), 2: (200, 40, 40)}
STATUS_BG = {0: (223, 242, 228), 1: (252, 240, 210), 2: (250, 224, 224)}

# ---- 布局参数 ----
W = 1672
MARGIN_X = 60
TITLE_H = 120
MODULE_H = 52
ROW_H = 56
COL_X = [MARGIN_X, 60, 320, 560]  # 功能点 / 状态 / 能力 三列起点
COL_W = [260, 240, W - MARGIN_X - 560 - 60]

def draw_table():
    img = Image.new("RGB", (W, 0), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # 估算高度
    rows = sum(len(rows) for _, rows in DATA)
    H = TITLE_H + len(DATA) * MODULE_H + rows * ROW_H + 120
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # 标题
    d.rectangle([0, 0, W, TITLE_H], fill=(24, 50, 88))
    d.text((MARGIN_X, 30), "RG660MK AI CPE 功能部署状态总览", font=F(42), fill=(255, 255, 255))
    d.text((MARGIN_X, 80), "对照《SG560D AI CPE 项目功能进展总览-5》· 更新时间 2026-09-10", font=F(22), fill=(200, 215, 235))

    y = TITLE_H
    # 表头
    d.rectangle([0, y, W, y + 44], fill=(235, 240, 248))
    headers = [("功能模块", COL_X[0]), ("部署状态", COL_X[1]), ("已实现能力 / 说明", COL_X[2])]
    for txt, cx in headers:
        d.text((cx + 14, y + 10), txt, font=F(24), fill=(40, 50, 70))
    y += 44

    for mod_name, rows in DATA:
        # 模块标题行
        d.rectangle([0, y, W, y + MODULE_H], fill=(45, 75, 120))
        d.text((COL_X[0] + 14, y + 12), mod_name, font=F(28), fill=(255, 255, 255))
        y += MODULE_H
        for name, status, cap in rows:
            # 行底色
            row_bg = (255, 255, 255) if (y // ROW_H) % 2 == 0 else (248, 250, 253)
            d.rectangle([0, y, W, y + ROW_H], fill=row_bg)
            # 功能名
            d.text((COL_X[0] + 14, y + 16), name, font=F(26), fill=(30, 35, 45))
            # 状态徽章
            bx, by = COL_X[1], y + 12
            bw, bh = 200, 32
            d.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=STATUS_BG[status])
            d.text((bx + 12, by + 6), "● " + STATUS_TXT[status], font=F(20), fill=STATUS_COLOR[status])
            # 能力说明
            d.text((COL_X[2] + 14, y + 16), cap, font=F(24), fill=(60, 65, 80))
            # 分隔线
            d.line([0, y + ROW_H - 1, W, y + ROW_H - 1], fill=(228, 232, 240))
            y += ROW_H

    # 底部汇总
    d.rectangle([0, H - 90, W, H], fill=(24, 50, 88))
    d.text((MARGIN_X, H - 70), "已部署 14 项    部分/适配 3 项    未部署 2 项（VAD/KWS 唤醒、人脸识别身份）", font=F(26), fill=(255, 255, 255))
    d.text((MARGIN_X, H - 40), "未完成项均为「需新建模型/外部环境」，非一键部署：KWS 唤醒词模型、face embedding 模型、Home Assistant 实例", font=F(20), fill=(190, 205, 230))

    return img

img = draw_table()
img.save("/tmp/RG660MK功能部署状态总览.png")
print("已生成 /tmp/RG660MK功能部署状态总览.png", img.size)
