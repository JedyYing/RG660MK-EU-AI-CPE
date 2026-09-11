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
story.append(P('生成时间 2026-09-11 · 目标设备 RG660MK-EU（OpenWrt CPE）', subtitle))
story.append(Spacer(1, 5))
story.append(HRFlowable(width='100%', thickness=1.2, color=colors.HexColor('#1a1a2e')))

story.append(P('一、结论摘要', h1))
story.append(make_table([
    ['功能', '部署状态', '实现方式', '备注'],
    ['人脸识别（身份识别）', '已部署', 'Immich 云人脸识别（InsightFace buffalo_s）', '复用本机 Immich，无需本地模型'],
    ['Home Assistant 接入', '未接入', 'MQTT Discovery（设计就绪）', '缺 HA 实例 + MQTT broker'],
], [36*mm, 20*mm, 62*mm, 50*mm]))
story.append(Spacer(1, 4))
story.append(P('人脸识别已落地：走本机 Immich 的人脸识别（原本就启用 buffalo_s 模型），RG660MK 拍照→上传→查身份，识别到的人名通过语音播报。Home Assistant 仍需外部环境。', body))

story.append(P('二、人脸识别（身份识别）—— 已部署（Immich 方案）', h1))
story.append(P('2.1 为什么用 Immich 而不是本地模型', h2))
story.append(B('RG660MK 本地做人脸 embedding 需新建 NCNN 模型 + 改 vision_runner（C++ 交叉编译），工程量大。'))
story.append(B('SG560D 原项目的人脸识别本就是走 Immich 云平台（进展总览第 12 项）。'))
story.append(B('本机 Immich 2.7.5 已启用 facialRecognition（模型 buffalo_s，minScore 0.7），已识别 2 个人。'))
story.append(B('RG660MK 已有 Immich 上传链路（photo_pipeline.py），复用即可。'))

story.append(P('2.2 实现', h2))
story.append(P('新增 face_recognize.py（部署在 /data/ai_cpe/face_recognize.py）：', body))
story.append(C('C270 拍照 → 上传 Immich(/api/assets) → 等 ML 异步识别(~6~8s) → 查 asset.people → 返回人名'))
story.append(B('已识别的人命名：PUT /api/people/{id} {"name":"应金栋"}（已命名 1 人）。'))
story.append(B('无人/未识别 → 返回「没有识别到人脸」。'))

story.append(P('2.3 已接入的调用入口', h2))
story.append(make_table([
    ['入口', '方式'],
    ['语音助手', '说「人脸识别 / 识别一下 / 这是谁」→ face_recognize.py'],
    ['Hermes', 'rg660mk-device-control skill 新增「人脸识别（身份）」'],
], [30*mm, 138*mm]))

story.append(P('2.4 实测', h2))
story.append(C('python3 face_recognize.py  →  识别到 1 个人：应金栋'))

story.append(P('三、Home Assistant 接入 —— 未接入（方案就绪）', h1))
story.append(P('3.1 现状与阻塞', h2))
story.append(B('本机（192.168.1.244）无 Home Assistant 实例（端口 8123 关闭）。'))
story.append(B('本机无 MQTT broker：仅装 mosquitto-clients，未装 broker 守护进程（安装需 sudo）。'))

story.append(P('3.2 推荐方案：MQTT Discovery', h2))
story.append(C('RG660MK ──(MQTT pub/sub)──> MQTT broker (mosquitto) <── Home Assistant'))
story.append(B('灯泡 → homeassistant/light/rg660mk_bulb/config + state/command 主题。'))

story.append(P('3.3 下一步路线', h2))
for i, t in enumerate([
    '部署 MQTT broker（本机 apt 装 mosquitto 或 docker eclipse-mosquitto）',
    'RG660MK 写 MQTT 发布脚本（paho-mqtt 或纯 stdlib 最小 MQTT 客户端）',
    '部署 Home Assistant 实例，配置 MQTT integration，自动发现 RG660MK 设备',
    '双向：HA 下发开关 → MQTT → RG660MK → bulb_control.py',
], 1):
    story.append(B('%d. %s' % (i, t)))

story.append(P('四、Hermes 交接状态', h1))
story.append(make_table([
    ['能力', 'Hermes 是否可调', '说明'],
    ['人脸识别身份', '可调', 'face_recognize.py 已接入 skill，语音+Hermes 均可触发'],
    ['Home Assistant', '未接', '需先有 broker + HA 实例'],
], [40*mm, 48*mm, 80*mm]))

story.append(P('五、结论', h1))
story.append(B('人脸识别（身份识别）已通过 Immich 方案落地，语音 + Hermes 均可调用，无需本地模型。'))
story.append(B('Home Assistant 接入仍卡在外部环境（HA 实例 + MQTT broker），方案已明确（MQTT Discovery）。'))
story.append(B('至此，原 SG560D 项目 19 个功能点中，除 Home Assistant 外已全部落地（含人脸识别）。'))

story.append(Spacer(1, 8))
story.append(HRFlowable(width='100%', thickness=0.8, color=colors.HexColor('#c5ccd6')))
story.append(P('—— 完 ——', subtitle))

doc = SimpleDocTemplate(OUT, pagesize=A4,
                        leftMargin=18*mm, rightMargin=18*mm,
                        topMargin=16*mm, bottomMargin=16*mm,
                        title='RG660MK 人脸识别 + Home Assistant 部署文档')
doc.build(story)
print('OK ->', OUT)
