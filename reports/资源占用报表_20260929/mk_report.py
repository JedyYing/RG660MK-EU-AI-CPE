#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 资源占用报表生成器 v2：读 /tmp/rg660mk_res.json → 输出深色主题 HTML 报表"""
import json, time

SRC = "/tmp/rg660mk_res.json"
DST = "/tmp/rg660mk_report.html"

d = json.load(open(SRC, encoding="utf-8"))
mem = d["mem_kb"]
total_mb = mem["MemTotal"] / 1024.0
avail_mb = mem["MemAvailable"] / 1024.0
used_mb = total_mb - avail_mb
uptime_min = d["uptime_s"] / 60.0

CATS = [
    ("hermes",  "Hermes 智能体",        "AI 大脑 · 飞书助手 / 工具编排 / 会话",        "#63a4ff", lambda c: "venv/bin/hermes" in c),
    ("para",    "Paraformer 语音识别",   "常驻 ASR 服务（sherpa-onnx :6006）",            "#4ade80", lambda c: "sherpa-onnx" in c),
    ("yolo",    "YOLO 视觉推理",         "目标检测 / 姿态估计（ai_service 常驻）",        "#fbbf24", lambda c: "ai_service.py" in c),
    ("va",      "语音助手",              "唤醒 / VAD / 对话编排 / 播报",                  "#c084fc", lambda c: "voice_assistant.py" in c),
    ("camav",   "视频流服务 camav",      "摄像头流 / 抓帧（:8092）",                      "#5eead4", lambda c: "camav" in c),
    ("camview", "摄像头预览 camview",    "画面预览推流（:8090）",                         "#93c5fd", lambda c: "camview" in c),
    ("smarthome","智能家居控制",         "本地 MQTT broker + agent（Matter 真灯）",       "#f9a8d4", lambda c: "smarthome" in c),
    ("matter",  "Matter 设备端（本机）", "CPE 自身作为 Matter 设备接入",                  "#bef264", lambda c: "matter-network-manager" in c),
    ("files",   "文件服务",              "固件 / 文件分发（:8098）",                      "#cbd5e1", lambda c: "http.server" in c),
]

groups = {k: {"name": n, "desc": ds, "color": col, "rss": 0, "cpu": 0.0, "threads": 0, "age": 0, "n": 0}
          for k, n, ds, col, _ in CATS}
other = {"name": "系统 / 其他", "desc": "内核 · 网络 · 蓝牙 · 日志 · 基带等", "color": "#64748b",
         "rss": 0, "cpu": 0.0, "threads": 0, "age": 0, "n": 0}

for p in d["procs"]:
    c = p["cmd"]
    hit = None
    for k, n, ds, col, fn in CATS:
        if fn(c):
            hit = k
            break
    g = groups[hit] if hit else other
    g["rss"] += p["rss_kb"]; g["cpu"] += p["cpu_pct"]
    g["threads"] += p["threads"]; g["n"] += 1
    if p["age_s"] > g["age"]:
        g["age"] = p["age_s"]

rows = [g for g in list(groups.values()) + [other] if g["n"] > 0 and g["rss"] > 0]
apps_mb = sum(g["rss"] for g in rows) / 1024.0
kernel_mb = max(0.0, used_mb - apps_mb)

def mb(kb): return kb / 1024.0
def fmt_min(s): return "%d 分" % (s // 60) if s < 3600 else "%d 小时 %d 分" % (s // 3600, (s % 3600) // 60)

def seg(label, v_mb, color, small=False):
    pct = v_mb / total_mb * 100
    cls = "seg small" if small else "seg"
    return '<div class="%s" style="width:%.2f%%;min-width:4px;background:%s" title="%s %.0f MB (%.1f%%)"></div>' % (cls, pct, color, label, v_mb, pct)

bar = "".join(seg(g["name"], mb(g["rss"]), g["color"], mb(g["rss"]) < 30) for g in rows)
bar += seg("内核/缓存/其他", kernel_mb, "#3b4a63")
bar += seg("空闲", avail_mb, "#182640")

legend = "".join(
    '<span class="lg"><i style="background:%s"></i>%s <b>%.0f</b>MB</span>' % (g["color"], g["name"], mb(g["rss"]))
    for g in rows) + '<span class="lg"><i style="background:#3b4a63"></i>内核/缓存 <b>%.0f</b>MB</span><span class="lg"><i style="background:#182640;border:1px solid #2b3f60"></i>空闲 <b>%.0f</b>MB</span>' % (kernel_mb, avail_mb)

def eng_card(emoji, key, tag):
    g = groups[key]
    return ('<div class="eng" style="border-top:3px solid %s"><div class="en">%s %s</div>'
            '<div class="em"><b>%.1f</b> MB</div>'
            '<div class="es">占总内存 %.1f%% ｜ CPU %.1f%% ｜ %d 线程</div>'
            '<div class="et">%s</div></div>') % (
        g["color"], emoji, g["name"], mb(g["rss"]), g["rss"] / 1024.0 / total_mb * 100, g["cpu"], g["threads"], tag)

engines = "".join([
    eng_card("🧠", "hermes", "飞书智能助手 · 工具编排 · 问答"),
    eng_card("🎙️", "para", "全程常驻 · 0.08–0.7 秒 / 句（实测）"),
    eng_card("👁️", "yolo", "检测 + 姿态 · 随演示调用推理"),
])

trows = ""
for g in sorted(rows, key=lambda x: -x["rss"]):
    cpu_cls = "hi" if g["cpu"] >= 10 else ("mid" if g["cpu"] >= 3 else "")
    trows += ('<tr><td><span class="dot" style="background:%s"></span><b>%s</b>%s</td><td class="desc">%s</td>'
              '<td class="num">%.1f</td><td class="num">%.1f%%</td><td class="num"><span class="cv %s">%.1f%%</span></td>'
              '<td class="num">%d</td><td class="num">%s</td></tr>') % (
        g["color"], g["name"], ("" if g["n"] <= 1 else '<span class="cnt">×%d</span>' % g["n"]),
        g["desc"], mb(g["rss"]), g["rss"] / 1024.0 / total_mb * 100, cpu_cls, g["cpu"], g["threads"], fmt_min(g["age"]))

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1322;color:#e8f0fb;font:15px/1.65 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif}
.page{width:1240px;margin:0 auto;padding:42px 44px 30px;background:
 radial-gradient(1200px 420px at 20% -10%,#16294a 0%,transparent 60%),#0b1322}
header{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid #1d2f4d;padding-bottom:20px}
.t1{font-size:34px;font-weight:800;letter-spacing:.5px}
.t1 i{font-style:normal;color:#63a4ff}
.t2{font-size:13.5px;color:#9db1cd;margin-top:8px}
.brand{font-size:12px;color:#7c93b5;text-align:right;line-height:1.7}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:13px;margin:26px 0 10px}
.kpi{background:#111c30;border:1px solid #1d2f4d;border-radius:13px;padding:16px 18px}
.kpi .k{font-size:12.5px;color:#9db1cd}
.kpi .v{font-size:26px;font-weight:800;margin-top:5px}
.kpi .s{font-size:11.5px;color:#7c93b5;margin-top:4px}
h2{font-size:16px;color:#a9c2e8;font-weight:700;margin:32px 0 14px;letter-spacing:.5px}
h2 span{color:#7c93b5;font-weight:400;font-size:12.5px;margin-left:10px}
.engs{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}
.eng{background:#111c30;border:1px solid #1d2f4d;border-radius:13px;padding:18px 20px 15px}
.en{font-size:14.5px;font-weight:700;color:#cfe0f7}
.em{font-size:31px;font-weight:800;margin:8px 0 3px}
.em b{color:#fff}
.es{font-size:12.5px;color:#9db1cd}
.et{font-size:12px;color:#7c93b5;margin-top:9px;border-top:1px dashed #1d2f4d;padding-top:9px}
.bar{display:flex;height:36px;border-radius:9px;overflow:hidden;border:1px solid #1d2f4d}
.seg{height:100%;border-right:1px solid #0b1322}
.lgs{margin-top:11px;display:flex;flex-wrap:wrap;gap:8px 16px}
.lg{font-size:12.5px;color:#a9c2e8;display:inline-flex;align-items:center;gap:7px}
.lg i{width:11px;height:11px;border-radius:3px;display:inline-block}
.lg b{color:#fff}
table{width:100%;border-collapse:collapse;background:#111c30;border:1px solid #1d2f4d;border-radius:13px;overflow:hidden}
th,td{padding:13px 14px;text-align:left;border-bottom:1px solid #16233c;font-size:13.5px}
th{background:#152340;color:#9db1cd;font-size:12.5px;font-weight:600}
tr:last-child td{border-bottom:0}
.dot{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:9px}
.cnt{font-size:11.5px;color:#7c93b5;margin-left:7px}
.desc{color:#8ba1c2;font-size:12.5px}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.cv{color:#cfe0f7}
.cv.mid{color:#fbbf24}
.cv.hi{color:#f87171;font-weight:700}
footer{margin-top:26px;border-top:1px solid #1d2f4d;padding-top:16px;color:#7c93b5;font-size:12.5px;line-height:2.05}
footer b{color:#9db1cd}
.note{background:#101a2e;border:1px solid #1d2f4d;border-radius:11px;padding:13px 17px;margin-top:18px;font-size:12.8px;color:#9db1cd;line-height:1.85}
"""

HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>RG660MK-EU 资源占用报表</title>
<style>%s</style></head><body><div class="page">
<header>
  <div><div class="t1">RG660MK-EU <i>·</i> 资源占用报表</div>
  <div class="t2">设备侧实时采样快照 ｜ %s ｜ 开机 %s ｜ 常驻服务全量计入</div></div>
  <div class="brand">数据源：设备 /proc 实时采样<br>生成：Hermes Agent · %s</div>
</header>
<div class="kpis">
  <div class="kpi"><div class="k">内存总量</div><div class="v">%.0f MB</div><div class="s">LPDDR · %d 核 CPU</div></div>
  <div class="kpi"><div class="k">内存已用 / 剩余</div><div class="v">%.0f / %.0f MB</div><div class="s">剩余可用 %.0f%%</div></div>
  <div class="kpi"><div class="k">CPU 占用（整机）</div><div class="v">%.1f%%</div><div class="s">近 10 秒采样 · 负载 %s</div></div>
  <div class="kpi"><div class="k">SoC 温度</div><div class="v">%.1f °C</div><div class="s">阈值 95°C 降频 / 115°C 保护</div></div>
  <div class="kpi"><div class="k">进程 / 运行时长</div><div class="v">%d 个</div><div class="s">开机 %s</div></div>
</div>
<h2>三大 AI 引擎占用 <span>用户点名统计</span></h2>
<div class="engs">%s</div>
<h2>内存分布（RSS 口径，总量 %.0f MB）</h2>
<div class="bar">%s</div>
<div class="lgs">%s</div>
<h2>服务明细 <span>CPU 为单核口径（top 惯例）· 采样窗口 %.1f 秒</span></h2>
<table><tr><th>应用 / 服务</th><th>角色</th><th style="text-align:right">内存 MB</th><th style="text-align:right">占总内存</th><th style="text-align:right">CPU</th><th style="text-align:right">线程</th><th style="text-align:right">已运行</th></tr>
%s</table>
<div class="note">🛡️ <b>快照注解</b>：本次采样于清晨设备重启 %s 后（服务已全部就绪）。Hermes 智能体 RSS 会随会话活动增长（昨日 8 小时运行观测 230 → 290 MB，属 Python 进程正常驻留形态）；演示套件当刻未运行，未计入。</div>
<footer>
  <b>口径说明</b>：内存 = 进程 RSS（物理驻留，含共享库；本内核未启用 smaps，无 PSS 公平口径）。CPU = ΔCPU时间 / 采样窗口，单核口径（各进程值 ÷ %d 核 ≈ 对整机的贡献）；整机占用取 /proc/stat 全核。负载（load）含不可中断 IO 等待，与 CPU%% 口径不同，故 1 分钟负载略高于 CPU 占用属正常。内核/缓存 = 已用内存 − 应用 RSS 合计。<br>
  <b>设备</b>：RG660MK-EU ｜ MediaTek T930 + Wi-Fi MT7992 ｜ Linux %s ｜ aarch64 &nbsp;&nbsp;·&nbsp;&nbsp;
  <b>采样</b>：%s &nbsp;&nbsp;·&nbsp;&nbsp; <b>生成</b>：Hermes Agent（自动报表）
</footer>
</div></body></html>""" % (
    CSS, d["ts"], fmt_min(d["uptime_s"]), time.strftime("%Y-%m-%d %H:%M"),
    total_mb, d["cores"],
    used_mb, avail_mb, avail_mb / total_mb * 100,
    d["busy_pct"], "/".join(d["load"]),
    d["temp_c"],
    len(d["procs"]), fmt_min(d["uptime_s"]),
    engines,
    total_mb, bar, legend,
    d["sample_dt_s"], trows,
    fmt_min(d["uptime_s"]),
    d["cores"], d["kernel"], d["ts"],
)

open(DST, "w", encoding="utf-8").write(HTML)
print("written:", DST, len(HTML), "bytes")

# 估算内容高度（供截图窗口高度参考）：粗略按块数
est = 42 + 20 + 90 + 26 + 100 + 32 + 140 + 32 + 36 + 11 + 50 + 32 + (13 * 10 + 60) + 18 + 80 + 26 + 90 + 30
print("estimated content height ~%d px (tune --window-size accordingly)" % est)
