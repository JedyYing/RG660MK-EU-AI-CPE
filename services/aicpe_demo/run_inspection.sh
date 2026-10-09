#!/bin/sh
# ─────────────────────────────────────────────────────────────────────
# RG660MK 五大功能巡检（静音质检）· 设备侧启动器
#   桌面「RG660MK巡检」通过 ssh 调用本脚本；--no-audio 模式不播报、
#   不实际开关灯；一轮跑完后看板继续在线 1 小时（页面点「开始演示」可再跑一轮）。
#   看板: http://192.168.1.1:8099/ 或 http://[fdc3:8285:b409::1]:8099/
# ─────────────────────────────────────────────────────────────────────
D=/data/ai_cpe/hermes/home/aicpe_demo
cd "$D" 2>/dev/null || exit 1
for p in $(pgrep -f aicpe_demo.py); do kill "$p" 2>/dev/null; done
sleep 1
for p in $(pgrep -f aicpe_demo.py); do kill -9 "$p" 2>/dev/null; done
sleep 1
nohup /data/hermes/venv/bin/python aicpe_demo.py --no-audio --port 8099 --keep-alive 3600 >> demo_desktop.log 2>&1 &
sleep 1
echo "静音质检已启动（看板: http://192.168.1.1:8099/）"
