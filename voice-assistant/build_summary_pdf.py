#!/usr/bin/env python3
"""把阶段工作总结渲染成 PDF(复用 yolo_deploy 的中文 Markdown 渲染器)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "yolo_deploy" / "scripts"))
import md_to_pdf

HERE = Path(__file__).resolve().parent
build = md_to_pdf.build
build(HERE / "阶段工作总结_20260921.md",
      Path.home() / "Desktop" / "RG660MK智能音箱语音助手_阶段工作总结_20260921.pdf",
      title="RG660MK 智能音箱语音助手 阶段工作总结 20260921",
      footer_text="RG660MK 智能音箱语音助手 · 阶段工作总结 · 2026-09-21")
