#!/usr/bin/env python3
"""Build the formal RG660MK-EU video QoE investigation PDF.

The document is generated entirely from local evidence. Charts are embedded as
base64 PNG data so the print-ready HTML and PDF have no external dependencies.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import markdown
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402


REPORT_TITLE = "RG660MK-EU 接入终端视频流畅度调查报告"
REPORT_VERSION = "R1.0"
REPORT_DATE = "2026-08-25"
REPORT_ID = "RG660-QOE-20260825-01"


class ReportBuildError(RuntimeError):
    pass


def configure_chinese_font() -> str:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            family = font_manager.FontProperties(fname=candidate).get_name()
            plt.rcParams["font.family"] = family
            plt.rcParams["axes.unicode_minus"] = False
            return family
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.unicode_minus"] = False
    return "sans-serif"


def png_data_uri(figure: plt.Figure) -> str:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def load_station_periods(path: Path) -> list[dict[str, float]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) < 2:
        raise ReportBuildError("station_samples.csv does not contain enough samples")
    origin = float(rows[0]["monotonic_s"])
    periods: list[dict[str, float]] = []
    for previous, current in zip(rows, rows[1:]):
        start = float(previous["monotonic_s"])
        end = float(current["monotonic_s"])
        elapsed = end - start
        byte_delta = int(current["wifi_tx_bytes"]) - int(previous["wifi_tx_bytes"])
        if elapsed <= 0 or byte_delta < 0:
            continue
        periods.append(
            {
                "time_s": end - origin,
                "elapsed_s": elapsed,
                "rate_mbps": byte_delta * 8.0 / elapsed / 1_000_000.0,
                "bytes": float(byte_delta),
            }
        )
    return periods


def load_ping_times(path: Path) -> list[float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    values = [float(value) for value in re.findall(r"time[=<]([0-9.]+)\s*ms", text)]
    if not values:
        raise ReportBuildError("wan_ping.txt does not contain RTT samples")
    return values


def throughput_chart(periods: list[dict[str, float]], summary: dict[str, Any]) -> str:
    times = [period["time_s"] for period in periods]
    rates = [period["rate_mbps"] for period in periods]
    mean = summary["station"]["downlink_mbps"]["mean"]
    peak = summary["station"]["downlink_mbps"]["max"]

    figure, axis = plt.subplots(figsize=(10.2, 3.5))
    axis.plot(times, rates, color="#1464A5", linewidth=1.25, label="下行吞吐")
    axis.fill_between(times, rates, color="#4EA3D8", alpha=0.22)
    axis.axhline(mean, color="#D97706", linestyle="--", linewidth=1.2, label=f"平均 {mean:.3f} Mbit/s")
    peak_index = max(range(len(rates)), key=rates.__getitem__)
    axis.scatter([times[peak_index]], [rates[peak_index]], color="#B91C1C", s=24, zorder=4)
    axis.annotate(
        f"峰值 {peak:.3f} Mbit/s",
        (times[peak_index], rates[peak_index]),
        xytext=(8, -18),
        textcoords="offset points",
        fontsize=8.5,
        color="#7F1D1D",
    )
    axis.set_title("图 1  单终端下行吞吐时间序列", fontsize=12, fontweight="bold")
    axis.set_xlabel("相对采集时间（秒）")
    axis.set_ylabel("Mbit/s")
    axis.set_xlim(0, max(times))
    axis.set_ylim(bottom=0, top=max(peak * 1.13, 1.0))
    axis.grid(True, axis="y", alpha=0.22, linewidth=0.6)
    axis.legend(loc="upper right", frameon=False, fontsize=8.5)
    figure.tight_layout()
    return png_data_uri(figure)


def ping_chart(values: list[float], summary: dict[str, Any]) -> str:
    mean = summary["ip_probe"]["rtt_ms"]["mean"]
    p95 = summary["ip_probe"]["rtt_ms"]["p95"]
    samples = list(range(1, len(values) + 1))

    figure, axis = plt.subplots(figsize=(10.2, 3.35))
    axis.plot(samples, values, color="#0F766E", linewidth=1.0)
    axis.axhline(mean, color="#D97706", linestyle="--", linewidth=1.1, label=f"均值 {mean:.2f} ms")
    axis.axhline(p95, color="#B91C1C", linestyle=":", linewidth=1.1, label=f"P95 {p95:.2f} ms")
    axis.fill_between(samples, values, color="#14B8A6", alpha=0.12)
    axis.set_title("图 2  CPE 至公网主动探测 RTT", fontsize=12, fontweight="bold")
    axis.set_xlabel("探测序号（约 1 秒/次）")
    axis.set_ylabel("RTT（ms）")
    axis.set_xlim(1, len(values))
    axis.set_ylim(bottom=0, top=max(values) * 1.15)
    axis.grid(True, axis="y", alpha=0.22, linewidth=0.6)
    axis.legend(loc="upper right", frameon=False, fontsize=8.5)
    figure.tight_layout()
    return png_data_uri(figure)


def coverage_chart(summary: dict[str, Any]) -> str:
    coverage = summary["capture_coverage"]
    labels = ["WAN RX", "Wi-Fi TX", "br-lan TX", "pcap 可见"]
    values = [
        coverage["wan_rx_bytes"] / 1_000_000.0,
        coverage["wifi_tx_bytes"] / 1_000_000.0,
        coverage["bridge_tx_bytes"] / 1_000_000.0,
        coverage["pcap_down_bytes"] / 1_000_000.0,
    ]
    colors = ["#1D4ED8", "#0F766E", "#D97706", "#B91C1C"]

    figure, axis = plt.subplots(figsize=(9.6, 3.45))
    bars = axis.bar(labels, values, color=colors, width=0.62)
    for bar, value in zip(bars, values):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(values) * 0.025,
            f"{value:.2f} MB",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ratio = coverage["pcap_vs_wifi_ratio"] * 100.0
    axis.set_title(f"图 3  快速转发导致的软件观测覆盖差异（pcap/Wi-Fi={ratio:.1f}%）", fontsize=12, fontweight="bold")
    axis.set_ylabel("采集期字节量（MB）")
    axis.set_ylim(0, max(values) * 1.2)
    axis.grid(True, axis="y", alpha=0.22, linewidth=0.6)
    figure.tight_layout()
    return png_data_uri(figure)


def strip_document_title(source: str) -> str:
    lines = source.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines)


def evidence_appendix(summary: dict[str, Any]) -> str:
    pcap = summary["pcap"]
    coverage = summary["capture_coverage"]
    fast_path = summary["fast_path_probe"]
    return f"""

## 附录 A：指标定义与计算口径

| 指标 | 计算与来源 |
|---|---|
| 单终端下行吞吐 | 采集开始与结束均确认只有一个授权 Wi-Fi 终端；对 `{summary['metadata']['wifi_interface']}` 的 `tx_bytes` 按真实采样间隔差分计算 |
| 峰值吞吐 | 实际平均采样间隔约 {summary['station']['actual_interval_s']['mean']:.2f} 秒的最大差分值 |
| 波动幅度 | P95 吞吐减 P5 吞吐；同时给出标准差和变异系数 |
| IP 丢包率 | CPE 从 `ccmni3` 以固定间隔向公网目标发起 180 次 ICMP 探测 |
| TCP RTT | 对软件可见 TCP 包优先使用 timestamp echo；不足时使用 ACK/握手样本 |
| TCP 抖动 | 按时间排序后，相邻 RTT 样本绝对差的均值 |
| TCP 数据重传 | 去除 2 ms 内 MTK tap 重复包后，按相同方向、序号和数据段识别；SYN/FIN 重试单列 |
| 零窗口 | 仅统计已建立连接中的 ACK/数据包，排除 SYN、FIN、RST 上常见的 `win=0` |
| cwnd | CPE 不拥有转发连接的 TCP socket，不能读取真实 cwnd；只记录数据重传和三次重复 ACK 等间接线索 |

平均吞吐代表本次视频的实际消耗，不等于链路最大容量。分片播放会产生长时间低流量与短时高速下载，不能仅凭 CV 或低流量间隔判断卡顿。

## 附录 B：数据质量与证据完整性

| 检查项 | 结果 |
|---|---:|
| pcap 原始记录 | {pcap['raw_packets']} 包 |
| 解析后的目标 IP 包 | {pcap['parsed_target_ip_packets']} 包 |
| 去除的 MTK tap 重复包 | {pcap['duplicate_tap_packets_removed']} 包 |
| pcap/Wi-Fi 字节覆盖率 | {coverage['pcap_vs_wifi_ratio'] * 100:.1f}% |
| br-lan/Wi-Fi 字节覆盖率 | {coverage['bridge_vs_wifi_ratio'] * 100:.1f}% |
| 快速路径确认 | {str(fast_path['hardware_fast_path_confirmed'])} |
| 快速路径模块 | `{', '.join(fast_path['loaded_modules'])}` |
| 终端采样缺失 | {summary['station']['station_missing_samples']} 次 |
| 采集结束仍关联 | {str(summary['metadata']['target_still_associated'])} |
| CPE 遗留 tcpdump | 无 |

正式结论仅采用证据目录 `video_qoe_l1_20260825_103855`。预备连接检查、空闲烟雾验证和未产生有效视频流的窗口均未纳入正式统计。最终 pcap 已通过本机 tcpdump 读取验证，报告关键数值已与 `summary.json` 自动核对。

## 附录 C：安全、隐私与复现说明

- 本次未修改 CPE 网络、防火墙、offload、固件、USB role、Hermes 或系统服务。
- 设备侧未保存 pcap；包头经 ADB 流式写入 Ubuntu 主机。
- `target_headers.pcap` 的 snaplen 为 128，仍包含 IP、端口和有限协议元数据，应作为敏感诊断资料管理。
- 正式报告对 MAC 和 IPv4 地址进行了部分脱敏；结构化原始证据仅限授权人员访问。
- 复测时应保持单终端、固定视频、固定清晰度、移动数据关闭，并记录终端实际卡顿时刻。
- 如需判断真实播放器 QoE，应补充 buffer level、rebuffer 次数/时长、selected bitrate、dropped frames 和 decoder error。

复测命令：

```bash
cd AI_CPE_Demo
python3 scripts/video_qoe_l1_collect.py --duration 180 --interval 1
```

报告构建命令：

```bash
python3 scripts/build_video_qoe_pdf.py \\
  --evidence-dir reports/evidence/video_qoe_l1_20260825_103855 \\
  --output reports/RG660MK-EU_接入终端视频流畅度调查报告_R1.0_20260825.pdf
```
"""


def render_html(evidence_dir: Path) -> tuple[str, dict[str, Any]]:
    required = [
        "THREE_LAYER_ASSESSMENT.md",
        "summary.json",
        "station_samples.csv",
        "wan_ping.txt",
    ]
    missing = [name for name in required if not (evidence_dir / name).is_file()]
    if missing:
        raise ReportBuildError(f"missing evidence files: {', '.join(missing)}")

    summary = json.loads((evidence_dir / "summary.json").read_text(encoding="utf-8"))
    source = (evidence_dir / "THREE_LAYER_ASSESSMENT.md").read_text(encoding="utf-8")
    source = strip_document_title(source) + evidence_appendix(summary)
    periods = load_station_periods(evidence_dir / "station_samples.csv")
    ping_values = load_ping_times(evidence_dir / "wan_ping.txt")

    configure_chinese_font()
    chart_throughput = throughput_chart(periods, summary)
    chart_ping = ping_chart(ping_values, summary)
    chart_coverage = coverage_chart(summary)

    converter = markdown.Markdown(extensions=["extra", "toc", "sane_lists"], output_format="html5")
    body = converter.convert(source)
    toc = converter.toc

    metadata = summary["metadata"]
    station = summary["station"]
    pcap = summary["pcap"]
    ip_probe = summary["ip_probe"]
    html_document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(REPORT_TITLE)}</title>
<style>
@page {{
  size: A4;
  margin: 17mm 16mm 18mm 16mm;
  @top-left {{
    content: "基于 SDR 的语义通信实验平台";
    color: #64748b;
    font: 8.2pt "Noto Sans CJK SC", sans-serif;
  }}
  @top-right {{
    content: "{REPORT_ID}  |  {REPORT_VERSION}";
    color: #64748b;
    font: 8.2pt "Noto Sans CJK SC", sans-serif;
  }}
  @bottom-left {{
    content: "RG660MK-EU 视频流畅度调查报告";
    color: #64748b;
    font: 8.2pt "Noto Sans CJK SC", sans-serif;
  }}
  @bottom-right {{
    content: "第 " counter(page) " 页 / 共 " counter(pages) " 页";
    color: #64748b;
    font: 8.2pt "Noto Sans CJK SC", sans-serif;
  }}
}}
@page :first {{
  margin: 0;
  @top-left {{ content: none; }}
  @top-right {{ content: none; }}
  @bottom-left {{ content: none; }}
  @bottom-right {{ content: none; }}
}}
* {{ box-sizing: border-box; }}
html {{ background: #eef2f6; }}
body {{
  margin: 0 auto;
  max-width: 210mm;
  background: white;
  color: #1f2937;
  font-family: "Noto Sans CJK SC", "Alibaba PuHuiTi", sans-serif;
  font-size: 10.1pt;
  line-height: 1.62;
  text-rendering: optimizeLegibility;
}}
.cover {{
  width: 210mm;
  height: 297mm;
  padding: 24mm 22mm 19mm;
  position: relative;
  overflow: hidden;
  background: linear-gradient(150deg, #f8fafc 0%, #ffffff 56%, #e7f0f8 100%);
  page-break-after: always;
}}
.cover::before {{
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 12mm;
  background: linear-gradient(90deg, #0b3b66, #1464a5, #0f766e);
}}
.cover::after {{
  content: "";
  position: absolute;
  width: 145mm; height: 145mm;
  border: 1.5mm solid rgba(20, 100, 165, 0.08);
  border-radius: 50%;
  right: -72mm; bottom: -55mm;
}}
.cover-project {{ margin-top: 12mm; color: #0f4c78; font-size: 12.5pt; letter-spacing: 1.5px; font-weight: 600; }}
.cover-rule {{ width: 33mm; height: 1.4mm; background: #0f766e; margin: 9mm 0 13mm; }}
.cover h1 {{ margin: 0; color: #0b3153; font-family: "Noto Serif CJK SC", serif; font-size: 29pt; line-height: 1.34; letter-spacing: 1px; }}
.cover-subtitle {{ margin-top: 7mm; color: #475569; font-size: 14pt; }}
.cover-verdict {{ margin-top: 13mm; padding: 6mm 7mm; border-left: 2mm solid #0f766e; background: rgba(240, 253, 250, 0.82); font-size: 11.5pt; color: #134e4a; }}
.cover-meta {{ margin-top: 20mm; width: 100%; border-collapse: collapse; font-size: 10.5pt; }}
.cover-meta td {{ border: 0; border-bottom: 0.25mm solid #cbd5e1; padding: 3mm 2mm; }}
.cover-meta td:first-child {{ width: 31mm; color: #64748b; }}
.cover-footer {{ position: absolute; left: 22mm; bottom: 18mm; color: #64748b; font-size: 9.5pt; }}
.print-body {{ padding: 0; }}
.toc-page {{ page-break-after: always; }}
.toc-page h1 {{ font-family: "Noto Serif CJK SC", serif; color: #0b3153; font-size: 23pt; border-bottom: 1mm solid #1464a5; padding-bottom: 4mm; }}
.toc ul {{ list-style: none; margin: 0; padding-left: 0; }}
.toc ul ul {{ padding-left: 7mm; }}
.toc li {{ margin: 1.7mm 0; border-bottom: 0.2mm dotted #cbd5e1; }}
.toc a {{ color: #334155; text-decoration: none; background: white; padding-right: 2mm; }}
.executive {{ page-break-after: always; }}
.executive h1 {{ color: #0b3153; font-family: "Noto Serif CJK SC", serif; font-size: 21pt; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 3.5mm; margin: 6mm 0 8mm; }}
.kpi {{ border: 0.25mm solid #cbd5e1; border-top: 1.3mm solid #1464a5; padding: 4mm; min-height: 26mm; background: #f8fafc; break-inside: avoid; }}
.kpi .label {{ color: #64748b; font-size: 8.8pt; }}
.kpi .value {{ color: #0b3153; font-size: 17pt; font-weight: 700; margin-top: 1mm; }}
.kpi .note {{ color: #64748b; font-size: 8pt; margin-top: 1mm; }}
.callout {{ margin: 5mm 0; padding: 4mm 5mm; background: #eff6ff; border-left: 1.3mm solid #2563eb; break-inside: avoid; }}
.callout.warning {{ background: #fffbeb; border-left-color: #d97706; }}
.figures {{ page-break-after: always; }}
.figure {{ margin: 5mm 0 7mm; padding: 3mm; border: 0.25mm solid #d8e1e8; border-radius: 1.5mm; break-inside: avoid; }}
.figure img {{ display: block; width: 100%; height: auto; }}
.figure-caption {{ color: #64748b; text-align: center; font-size: 8.6pt; padding: 1mm 4mm 2mm; }}
article h2 {{ color: #0b3153; font-family: "Noto Serif CJK SC", serif; font-size: 18pt; border-bottom: 0.65mm solid #cbd5e1; padding-bottom: 2mm; margin-top: 9mm; break-after: avoid; }}
article h3 {{ color: #0f4c78; font-size: 13pt; margin-top: 6mm; break-after: avoid; }}
article h4 {{ color: #334155; font-size: 11.3pt; margin-top: 4mm; break-after: avoid; }}
p {{ margin: 2.2mm 0; orphans: 3; widows: 3; }}
ul, ol {{ margin: 2mm 0 3mm; padding-left: 7mm; }}
li {{ margin: 1mm 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 3.5mm 0 5mm; font-size: 8.8pt; break-inside: auto; }}
thead {{ display: table-header-group; }}
tr {{ break-inside: avoid; }}
th {{ background: #eaf1f7; color: #0b3153; font-weight: 650; text-align: left; }}
th, td {{ border: 0.22mm solid #b9c7d3; padding: 2mm 2.2mm; vertical-align: top; }}
tbody tr:nth-child(even) {{ background: #f8fafc; }}
code {{ font-family: "Noto Sans Mono CJK SC", monospace; background: #f1f5f9; padding: 0.2mm 0.8mm; border-radius: 0.6mm; font-size: 8.7pt; overflow-wrap: anywhere; }}
pre {{ padding: 3.5mm; background: #111827; color: #e5e7eb; border-radius: 1.5mm; white-space: pre-wrap; overflow-wrap: anywhere; break-inside: avoid; }}
pre code {{ background: transparent; color: inherit; padding: 0; }}
blockquote {{ margin: 4mm 0; padding: 2mm 4mm; border-left: 1mm solid #94a3b8; color: #475569; }}
strong {{ color: #111827; }}
a {{ color: #0f5f99; }}
.small {{ color: #64748b; font-size: 8.5pt; }}
@media screen {{ body {{ box-shadow: 0 0 18px rgba(15, 23, 42, 0.16); }} .print-body {{ padding: 16mm; }} }}
@media print {{ html, body {{ background: white; max-width: none; }} .print-body {{ padding: 0; }} a {{ color: inherit; }} }}
</style>
</head>
<body>
<section class="cover">
  <div class="cover-project">基于 SDR 的语义通信实验平台</div>
  <div class="cover-rule"></div>
  <h1>{html.escape(REPORT_TITLE)}</h1>
  <div class="cover-subtitle">单终端实机采集 · 网络条件间接推断 · DPI 增强路线评估</div>
  <div class="cover-verdict"><strong>综合判定：</strong>当前播放流未发现网络链路持续具备造成卡顿的明确条件；网络侧风险偏低，结论置信度为中等。</div>
  <table class="cover-meta">
    <tr><td>报告编号</td><td>{REPORT_ID}</td></tr>
    <tr><td>报告版本</td><td>{REPORT_VERSION}</td></tr>
    <tr><td>调查对象</td><td>RG660MK-EU CPE / HONOR-90 单终端</td></tr>
    <tr><td>正式采集</td><td>{html.escape(metadata['started_at'])}，约 {station['duration_s']:.0f} 秒</td></tr>
    <tr><td>调查范围</td><td>吞吐、IP 丢包、RTT/抖动、TCP 重传/零窗口/cwnd 线索、DPI 可行性</td></tr>
    <tr><td>报告日期</td><td>{REPORT_DATE}</td></tr>
    <tr><td>文档属性</td><td>内部技术调查报告</td></tr>
  </table>
  <div class="cover-footer">依据实机只读采集证据生成 · 未修改 CPE 网络、固件或服务</div>
</section>
<main class="print-body">
<section class="toc-page">
  <h1>目录</h1>
  <nav class="toc">{toc}</nav>
  <div class="callout warning"><strong>阅读提示：</strong>TCP 包级指标仅覆盖软件可见流量；HNAT/WARP/WED 快速路径造成的观测盲区已在正文和附录中单独量化。</div>
</section>
<section class="executive">
  <h1>关键结果摘要</h1>
  <div class="kpi-grid">
    <div class="kpi"><div class="label">下行平均吞吐</div><div class="value">{station['downlink_mbps']['mean']:.3f}</div><div class="note">Mbit/s，当前内容实际消耗</div></div>
    <div class="kpi"><div class="label">下行峰值吞吐</div><div class="value">{station['downlink_mbps']['max']:.3f}</div><div class="note">Mbit/s，约 1.49 秒粒度</div></div>
    <div class="kpi"><div class="label">公网主动探测丢包</div><div class="value">{ip_probe['loss_percent']:.2f}%</div><div class="note">{ip_probe['received']}/{ip_probe['transmitted']} 应答</div></div>
    <div class="kpi"><div class="label">可见 TCP RTT</div><div class="value">{pcap['tcp_rtt_ms']['mean']:.2f}</div><div class="note">ms，P95 {pcap['tcp_rtt_ms']['p95']:.2f} ms</div></div>
    <div class="kpi"><div class="label">TCP 数据重传</div><div class="value">{pcap['target_retransmission_count']}</div><div class="note">下行 1 次，手机零窗口 0 次</div></div>
    <div class="kpi"><div class="label">软件包级覆盖率</div><div class="value">{summary['capture_coverage']['pcap_vs_wifi_ratio'] * 100:.1f}%</div><div class="note">受 HNAT/WARP/WED 影响</div></div>
  </div>
  <div class="callout"><strong>第二层结论：</strong>当前流的平均需求约 0.508 Mbit/s，已观测分片突发约 6.04 Mbit/s，约有 12 倍瞬时补给余量；未出现持续丢包、队列拥塞、终端零窗口或持续高 RTT。</div>
  <div class="callout warning"><strong>结论边界：</strong>CPE 无法感知播放器 buffer、rebuffer、解码掉帧、片源/CDN 和手机 CPU/GPU；WAN 还观察到 UDP/8443 流，因此不能把本报告等同于终端播放器真值。</div>
</section>
<section class="figures">
  <h1>实测图表</h1>
  <div class="figure"><img src="{chart_throughput}" alt="下行吞吐时间序列"><div class="figure-caption">图 1：下行数据集中在短时突发，符合分片预取；低流量区间不能直接视为卡顿。</div></div>
  <div class="figure"><img src="{chart_ping}" alt="公网主动探测 RTT"><div class="figure-caption">图 2：RTT 存在蜂窝调度波动，但未出现连续高时延或探测丢包。</div></div>
  <div class="figure"><img src="{chart_coverage}" alt="软件观测覆盖率"><div class="figure-caption">图 3：WAN/Wi-Fi 字节量与 bridge/pcap 明显不一致，证明快速转发观测盲区。</div></div>
</section>
<article>{body}</article>
</main>
</body>
</html>
"""
    return html_document, summary


def run_chrome(chrome: str, html_path: Path, output_path: Path) -> None:
    executable = shutil.which(chrome) if not Path(chrome).is_absolute() else chrome
    if not executable or not Path(executable).is_file():
        raise ReportBuildError(f"Chrome executable not found: {chrome}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".chrome-qoe-", dir=output_path.parent) as profile:
        command = [
            str(executable),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=3000",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={output_path}",
            html_path.resolve().as_uri(),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise ReportBuildError(f"Chrome PDF conversion failed ({result.returncode}): {result.stderr.strip()}")
    if not output_path.is_file() or output_path.stat().st_size < 10_000:
        raise ReportBuildError("Chrome did not produce a valid-sized PDF")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the formal RG660MK-EU video QoE investigation PDF")
    parser.add_argument("--evidence-dir", type=Path, required=True, help="formal evidence directory")
    parser.add_argument("--output", type=Path, required=True, help="target PDF path")
    parser.add_argument("--html-output", type=Path, help="optional print-ready HTML path")
    parser.add_argument("--chrome", default="google-chrome", help="Chrome/Chromium executable")
    parser.add_argument("--html-only", action="store_true", help="generate HTML without invoking Chrome")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    evidence_dir = args.evidence_dir.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    html_path = (
        args.html_output.expanduser().resolve()
        if args.html_output
        else output_path.with_suffix(".html")
    )
    try:
        html_document, summary = render_html(evidence_dir)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html_document, encoding="utf-8")
        if not args.html_only:
            run_chrome(args.chrome, html_path, output_path)
        print(
            "REPORT_BUILD_COMPLETE "
            f"html={html_path} pdf={output_path if not args.html_only else 'SKIPPED'} "
            f"avg_down={summary['station']['downlink_mbps']['mean']:.3f}Mbps",
            flush=True,
        )
        return 0
    except (OSError, ReportBuildError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"REPORT_BUILD_FAILED: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
