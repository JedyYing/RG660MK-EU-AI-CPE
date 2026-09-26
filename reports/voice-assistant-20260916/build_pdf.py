"""Generate the archived Chinese report PDF with ReportLab and an installed CJK font."""
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

folder = Path(__file__).resolve().parent
font = '/usr/share/fonts/Alibaba-PuHuiTi/Alibaba-PuHuiTi-Regular.ttf'
pdfmetrics.registerFont(TTFont('CJK', font))
styles = {
 'title': ParagraphStyle('title', fontName='CJK', fontSize=21, leading=31, textColor=colors.HexColor('#15385a'), spaceAfter=18),
 'heading': ParagraphStyle('heading', fontName='CJK', fontSize=13, leading=21, textColor=colors.HexColor('#15385a'), spaceBefore=15, spaceAfter=8, keepWithNext=True),
 'body': ParagraphStyle('body', fontName='CJK', fontSize=10, leading=17, spaceAfter=9, wordWrap='CJK'),
}
def footer(canvas, doc):
 canvas.saveState()
 canvas.setStrokeColor(colors.HexColor('#cad5df'))
 canvas.line(42, 39, A4[0]-42, 39)
 canvas.setFont('CJK', 8)
 canvas.setFillColor(colors.HexColor('#526474'))
 canvas.drawString(42, 26, 'RG660MK 智能音箱 · 2026-09-16 排查与修复归档')
 canvas.drawRightString(A4[0]-42, 26, str(doc.page))
 canvas.restoreState()

story = []
for block in (folder / '排查与修复报告.md').read_text().split('\n\n'):
 block = block.strip()
 if not block:
  continue
 style = 'body'
 if block.startswith('# '):
  block, style = block[2:], 'title'
 elif block.startswith('## '):
  if block.startswith(('## 4.', '## 6.', '## 8.')):
   story.append(PageBreak())
  block, style = block[3:], 'heading'
 story.append(Paragraph(escape(block).replace('\n', '<br/>'), styles[style]))
out = folder / 'RG660MK智能音箱排查与修复成果报告_20260916.pdf'
doc = SimpleDocTemplate(str(out), pagesize=A4, rightMargin=43, leftMargin=43, topMargin=42, bottomMargin=52,
 title='RG660MK 智能音箱排查与修复成果报告 20260916', author='RG660MK AI CPE 项目')
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(out)
