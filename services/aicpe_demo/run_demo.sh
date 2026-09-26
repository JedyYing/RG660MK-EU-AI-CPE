#!/bin/bash
# 在你的电脑上运行：一键启动 RG660MK 的 AI CPE 五大功能演示，并打开实时看板
# 用法: bash run_demo.sh [--no-audio]
set -e
DEV=root@192.168.1.1
PORT=8099
OPTS="$@"
echo "→ 在设备上启动演示套件…"
ssh $DEV "cd /data/ai_cpe/hermes/home/aicpe_demo && pkill -f aicpe_demo.py 2>/dev/null; nohup /data/hermes/venv/bin/python aicpe_demo.py --port $PORT --keep-alive 3600 $OPTS >> demo_desktop.log 2>&1 &"
sleep 3
URL="http://192.168.1.1:$PORT/"
echo "→ 看板地址: $URL   （浏览器打开即可看到实时进度与现场画面）"
command -v xdg-open >/dev/null && xdg-open "$URL" || true
echo "→ 结束后报告在设备: /data/ai_cpe/hermes/home/aicpe_demo/reports/"
