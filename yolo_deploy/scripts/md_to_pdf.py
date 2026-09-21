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

# CJK 汉字/全角标点。DejaVu Mono 没有这些字形,含中文的等宽片段必须换字体,
# 否则行内代码与代码块会整片渲染成豆腐块(□)。
_CJK_CHAR = re.compile(r'[⺀-鿿＀-￯　-〿]')


def _code_span(body):
    # Mono 无中文字形 -> 含中文的片段退化为 CJK 字体(非等宽,但可读)。
    if _CJK_CHAR.search(body):
        return '<font face="CJK" size="9.3">%s</font>' % body
    return '<font face="Mono" size="8.5">%s</font>' % body


def inline(text):
    # 转义 XML,再还原 **bold** 与 `code`
    t = html.escape(text)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'`(.+?)`', lambda m: _code_span(m.group(1)), t)
    return t


def _disp_width(text):
    """显示宽度:中文按 2 计,便于折行时对齐右边距。"""
    return sum(2 if _CJK_CHAR.match(c) else 1 for c in text)


def _cut(text, limit):
    """切成不超过 limit 显示宽度的前缀,返回字符数。"""
    used = 0
    for idx, ch in enumerate(text):
        used += 2 if _CJK_CHAR.match(ch) else 1
        if used > limit:
            return max(1, idx)
    return len(text)

def build(md_path, pdf_path, title="RG660MK-EU YOLO 姿态/坐姿检测部署报告", footer_text=None):
    md_path, pdf_path = str(md_path), str(pdf_path)  # reportlab 不接受 pathlib.Path
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
    # 含中文的代码块用这个;见 _CJK_CHAR 注释。
    codest_cjk = ParagraphStyle("codecjk", parent=codest, fontName="CJK", fontSize=8.6, leading=12)

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
            # 含中文的块改用 CJK 字体,否则整块是豆腐块
            style = codest_cjk if _CJK_CHAR.search("".join(buf)) else codest
            # 软换行:按显示宽度折行(中文按 2 计),避免溢出右边距
            WRAP = 82 if style is codest_cjk else 92
            wrapped = []
            for row in (buf if buf else [" "]):
                while _disp_width(row) > WRAP:
                    cut = _cut(row, WRAP)
                    wrapped.append(row[:cut] + "↩")  # 续行标记
                    row = row[cut:]
                wrapped.append(row)
            story.append(Preformatted("\n".join(wrapped), style))
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
                            leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm,
                            bottomMargin=20*mm if footer_text else 16*mm,
                            title=title)
    if footer_text:
        def footer(canvas, doc_):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#cad5df"))
            canvas.line(18*mm, 13*mm, A4[0]-18*mm, 13*mm)
            canvas.setFont("CJK", 8)
            canvas.setFillColor(colors.HexColor("#526474"))
            canvas.drawString(18*mm, 8.5*mm, footer_text)
            canvas.drawRightString(A4[0]-18*mm, 8.5*mm, str(doc_.page))
            canvas.restoreState()
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    else:
        doc.build(story)
    print("PDF 生成:", pdf_path)

if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
