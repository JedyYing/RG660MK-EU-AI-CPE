#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 RG660MK 人脸识别 + Home Assistant 部署文档 PDF"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, HRFlowable)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
F = 'STSong-Light'
OUT = '/home/jedyying/Desktop/RG660MK_人脸识别_HomeAssistant_部署文档_20260910.pdf'

title = ParagraphStyle('title', fontName=F, fontSize=18, leading=24,
                       alignment=TA_CENTER, textColor=colors.HexColor('#1a1a2e'))
subtitle = ParagraphStyle('subtitle', fontName=F, fontSize=10.5, leading=15,
                          alignment=TA_CENTER, textColor=colors.HexColor('#555555'))
h1 = ParagraphStyle('h1', fontName=F, fontSize=13.5, leading=19,
                    textColor=colors.HexColor('#1a1a2e'), spaceBefore=13, spaceAfter=5)
h2 = ParagraphStyle('h2', fontName=F, fontSize=11, leading=15,
                    textColor=colors.HexColor('#2d5a8e'), spaceBefore=9, spaceAfter=3)
body = ParagraphStyle('body', fontName=F, fontSize=9.5, leading=15,
                      alignment=TA_LEFT, textColor=colors.HexColor('#222222'))
code = ParagraphStyle('code', fontName=F, fontSize=8, leading=12,
                      backColor=colors.HexColor('#f4f4f8'), textColor=colors.HexColor('#1a1a2e'),
                      leftIndent=8, rightIndent=8, spaceBefore=3, spaceAfter=3, borderPadding=6)
bullet = ParagraphStyle('bullet', fontName=F, fontSize=9.5, leading=15,
                        leftIndent=12, bulletIndent=4, textColor=colors.HexColor('#222222'))

def P(t, s=body): return Paragraph(t, s)
def C(t): return Paragraph(t, code)
def B(t): return Paragraph(t, bullet, bulletText='•')

def make_table(data, widths, header_bg='#2d5a8e'):
    tbl = Table(data, colWidths=widths)
    tbl.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), F),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor(header_bg)),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#eef2f7')]),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor('#c5ccd6')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 4), ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))
    return tbl

story = []
story.append(P('RG660MK 人脸识别 + Home Assistant 接入', title))
story.append(P('部署文档', title))
story.append(Spacer(1, 3))
story.append(P('生成时间 2026-09-10 · 目标设备 RG660MK-EU（OpenWrt CPE）', subtitle))
story.append(Spacer(1, 5))
story.append(HRFlowable(width='100%', thickness=1.2, color=colors.HexColor('#1a1a2e')))

# 一、结论摘要
story.append(P('一、结论摘要', h1))
story.append(make_table([
    ['功能', '代码状态', '阻塞点', '能否直接部署'],
    ['人脸识别（身份识别）', '代码已全部就绪', '缺 2 个 NCNN 模型 + vision_runner 未实现 face 操作', '否（需改 C++ + 建模）'],
    ['Home Assistant 接入', '仅设计', '无 HA 实例 + 无 MQTT broker', '否（需外部环境）'],
], [36*mm, 40*mm, 66*mm, 26*mm]))
story.append(Spacer(1, 4))
story.append(P('两个功能都属于「需要新建模型 / 外部基础设施」的工程，不是把现成代码部署过去就能跑通的。', body))

# 二、人脸识别
story.append(P('二、人脸识别（身份识别）', h1))

story.append(P('2.1 已完成的部分（代码层 100% 就绪）', h2))
story.append(make_table([
    ['组件', '位置', '状态'],
    ['人脸识别服务', 'ai_service.py', 'Gallery 匹配（余弦相似度，阈值 0.52）、_identify_faces()、match/add/remove 全实现'],
    ['图库管理', 'face_gallery.py', 'enroll（录入人脸）/ remove / list 命令'],
    ['CLI 客户端', 'hermes_ai_tool.py', 'face 动作（POST /vision/face）'],
    ['配置', 'ai-service.json', 'face_recognition 段已配好，但 face_detect: null、face_embed: null'],
], [28*mm, 40*mm, 100*mm]))

story.append(P('2.2 阻塞点（两个，都需新建）', h2))
story.append(B('vision_runner 未实现 face 操作 —— ai_runtime/src/vision_runner.cpp 第 596 行：'))
story.append(C('if (operation != "detect" && operation != "pose")'))
story.append(C('    throw JsonError("operation must be detect or pose");'))
story.append(P('目前只支持 YOLO 的 detect（COCO 80 类）和 pose（17 关键点）。人脸识别需新增 face_detect（RetinaFace / YOLOv5-face，输出人脸框）和 face_embed（MobileFaceNet / ArcFace，输出 128~512 维 embedding），并实现对应后处理，再用工具链交叉编译 aarch64。', body))
story.append(B('两个 NCNN 模型缺失 —— face_detect 和 face_embed 均未配置模型文件（.param + .bin）。'))

story.append(P('2.3 下一步路线（建议顺序）', h2))
for i, t in enumerate([
    '选型人脸检测模型（优先 RetinaFace-mobilenet，NCNN 生态成熟）',
    '选型人脸 embedding 模型（优先 MobileFaceNet，128 维，轻量）',
    '转 NCNN 格式（onnx2ncnn），放入 /data/ai_cpe/demo/ai_models/',
    '改 vision_runner.cpp：新增 face 操作 + 两个模型前向 + 后处理',
    '交叉编译（见 quectel-module-deployment skill 的交叉编译坑）',
    '回填 ai-service.json 的 face_detect / face_embed 模型路径',
    '用 face_gallery.py enroll 录入人脸；通过 hermes_ai_tool.py face 验证身份识别',
], 1):
    story.append(B('%d. %s' % (i, t)))

# 三、Home Assistant
story.append(P('三、Home Assistant 接入', h1))

story.append(P('3.1 现状', h2))
story.append(B('本机（192.168.1.244）无 Home Assistant 实例（端口 8123 关闭）。'))
story.append(B('本机无 MQTT broker：仅装 mosquitto-clients（客户端），未装 broker 守护进程 mosquitto（安装需 sudo）。'))
story.append(B('RG660MK 已有 Tuya 云（灯泡）、Matter 等设备接入，但未接入 HA。'))

story.append(P('3.2 推荐方案：MQTT Discovery', h2))
story.append(P('HA 生态标准接入方式是 MQTT，RG660MK 作为 MQTT 客户端把设备通过 MQTT Discovery 暴露给 HA：', body))
story.append(C('RG660MK ──(MQTT pub/sub)──> MQTT broker (mosquitto) <── Home Assistant'))
story.append(B('灯泡 → homeassistant/light/rg660mk_bulb/config（Discovery）+ state/command 主题'))
story.append(B('摄像头/传感器 → 类似方式'))

story.append(P('3.3 阻塞点', h2))
story.append(B('需一台运行中的 MQTT broker（本机 sudo apt install mosquitto，或 docker eclipse-mosquitto）。'))
story.append(B('需一个 Home Assistant 实例（本机 docker 或独立设备）+ MQTT integration。'))

story.append(P('3.4 下一步路线', h2))
for i, t in enumerate([
    '部署 MQTT broker（本机 apt 装 mosquitto 或 docker 起 eclipse-mosquitto）',
    'RG660MK 上写 MQTT 发布脚本（paho-mqtt 或纯 stdlib 最小 MQTT 客户端），发布灯泡/设备状态',
    '部署 Home Assistant 实例，配置 MQTT integration，自动发现 RG660MK 设备',
    '双向：HA 下发开关 → MQTT → RG660MK → bulb_control.py',
], 1):
    story.append(B('%d. %s' % (i, t)))

# 四、Hermes 交接状态
story.append(P('四、Hermes 交接状态', h1))
story.append(make_table([
    ['能力', 'Hermes 是否可调', '说明'],
    ['人脸识别身份', '接口已接、模型缺失', 'hermes_ai_tool.py face 已接入 rg660mk-device-control skill，但缺模型返回错误'],
    ['Home Assistant', '未接', '需先有 broker + HA 实例'],
], [40*mm, 48*mm, 80*mm]))

# 五、结论
story.append(P('五、结论', h1))
story.append(B('人脸识别的代码链路已 100% 就绪，唯一缺口是「2 个 NCNN 模型 + vision_runner 的 face 操作 C++ 实现」。'))
story.append(B('Home Assistant 接入方案明确（MQTT Discovery），缺口是「broker + HA 实例」两个外部环境。'))
story.append(B('两者都不属于一键部署，需要新建模型/环境。本项目四大块里，除这两项外的 17 个功能点已全部落地。'))

story.append(Spacer(1, 8))
story.append(HRFlowable(width='100%', thickness=0.8, color=colors.HexColor('#c5ccd6')))
story.append(P('—— 完 ——', subtitle))

doc = SimpleDocTemplate(OUT, pagesize=A4,
                        leftMargin=18*mm, rightMargin=18*mm,
                        topMargin=16*mm, bottomMargin=16*mm,
                        title='RG660MK 人脸识别 + Home Assistant 部署文档')
doc.build(story)
print('OK ->', OUT)
