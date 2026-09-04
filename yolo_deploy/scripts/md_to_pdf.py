#!/usr/bin/env python3
# md_to_pdf.py — 把中文 Markdown 报告渲染为 PDF(reportlab + Noto Sans CJK)
# 支持: #/##/### 标题、段落、**加粗**、`行内代码`、有序/无序列表、表格、``` 代码块、--- 分隔线。
import sys, re, html
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Preformatted, HRFlowable, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily

# reportlab 只支持 TrueType(glyf)轮廓,Noto CJK 为 CFF/OTF 不可用;
# 改用 Alibaba PuHuiTi(TrueType)。仅有 Regular 字重,粗体退化为常规,层级靠字号+颜色区分。
CJK_R = "/usr/share/fonts/Alibaba-PuHuiTi/Alibaba-PuHuiTi-Regular.ttf"
FALLBACK = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
MONO  = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

import os
_cjk = CJK_R if os.path.exists(CJK_R) else FALLBACK
pdfmetrics.registerFont(TTFont("CJK",  _cjk))
pdfmetrics.registerFont(TTFont("CJKB", _cjk))
pdfmetrics.registerFont(TTFont("Mono", MONO))
registerFontFamily("CJK", normal="CJK", bold="CJKB", italic="CJK", boldItalic="CJKB")

def inline(text):
    # 转义 XML,再还原 **bold** 与 `code`
    t = html.escape(text)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'`(.+?)`', r'<font face="Mono" size="8.5">\1</font>', t)
    return t

def build(md_path, pdf_path):
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="CJK", fontSize=10.2,
                          leading=15.5, alignment=TA_LEFT, spaceAfter=5)
    h1 = ParagraphStyle("h1", fontName="CJKB", fontSize=18, leading=23, spaceBefore=6, spaceAfter=10,
                        textColor=colors.HexColor("#0b3d66"))
    h2 = ParagraphStyle("h2", fontName="CJKB", fontSize=14, leading=19, spaceBefore=12, spaceAfter=6,
                        textColor=colors.HexColor("#12507b"))
    h3 = ParagraphStyle("h3", fontName="CJKB", fontSize=11.5, leading=16, spaceBefore=8, spaceAfter=4,
                        textColor=colors.HexColor("#1a5f8a"))
    li = ParagraphStyle("li", parent=body, leftIndent=12, spaceAfter=3)
    codest = ParagraphStyle("code", fontName="Mono", fontSize=8.2, leading=11,
                            backColor=colors.HexColor("#f4f5f7"), textColor=colors.HexColor("#1b1f23"),
                            borderPadding=5, leftIndent=2, spaceBefore=4, spaceAfter=8)

    story = []
    lines = open(md_path, encoding="utf-8").read().split("\n")
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        # 代码块
        if ln.strip().startswith("```"):
            i += 1; buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            i += 1
            # 软换行:超过 W 字符的行折行,避免溢出右边距
            WRAP = 92
            wrapped = []
            for row in (buf if buf else [" "]):
                if len(row) <= WRAP:
                    wrapped.append(row)
                else:
                    while len(row) > WRAP:
                        wrapped.append(row[:WRAP] + "↩")  # 续行标记
                        row = row[WRAP:]
                    wrapped.append(row)
            story.append(Preformatted("\n".join(wrapped), codest))
            continue
        # 表格
        if ln.strip().startswith("|") and i+1 < n and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i+1]):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                if re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i]):
                    i += 1; continue
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells); i += 1
            if rows:
                ncol = max(len(r) for r in rows)
                data = [[Paragraph(inline(c), body) for c in (r + [""]*(ncol-len(r)))] for r in rows]
                tw = A4[0] - 36*mm
                tbl = Table(data, colWidths=[tw/ncol]*ncol, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#12507b")),
                    ("TEXTCOLOR",(0,0),(-1,0),colors.white),
                    ("FONTNAME",(0,0),(-1,0),"CJKB"),
                    ("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#c8ced3")),
                    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f4f6f8")]),
                    ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                    ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
                    ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
                ]))
                story.append(tbl); story.append(Spacer(1,6))
            continue
        s = ln.strip()
        if not s:
            i += 1; continue
        if re.match(r'^---+\s*$', s):
            story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c8ced3"),
                                    spaceBefore=6, spaceAfter=6)); i += 1; continue
        if s.startswith("### "):
            story.append(Paragraph(inline(s[4:]), h3)); i += 1; continue
        if s.startswith("## "):
            story.append(Paragraph(inline(s[3:]), h2)); i += 1; continue
        if s.startswith("# "):
            story.append(Paragraph(inline(s[2:]), h1)); i += 1; continue
        m = re.match(r'^(\s*)([-*])\s+(.*)', ln)
        if m:
            story.append(Paragraph("• " + inline(m.group(3)), li)); i += 1; continue
        m = re.match(r'^(\s*)(\d+)\.\s+(.*)', ln)
        if m:
            story.append(Paragraph(m.group(2) + ". " + inline(m.group(3)), li)); i += 1; continue
        story.append(Paragraph(inline(s), body)); i += 1

    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm,
                            title="RG660MK-EU YOLO 姿态/坐姿检测部署报告")
    doc.build(story)
    print("PDF 生成:", pdf_path)

if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
