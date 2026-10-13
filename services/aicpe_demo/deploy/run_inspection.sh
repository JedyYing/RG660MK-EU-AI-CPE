#!/bin/sh
# ─────────────────────────────────────────────────────────────────────
# RG660MK 七大功能巡检（静音质检）· 服务化版（2026-10-13）
#
#  背景：看板进程原先"跑完一轮 + 在线 6 小时后自动退出"（一次性实例），
#  导致链接隔夜/过窗口后打不开。现改为：
#    - 看板 = procd 常驻服务 /etc/init.d/aicpe-board（--serve-only，自愈+开机自启）
#    - 本脚本不再起停看板，只做两件事：①确保服务在跑 ②前台跑一轮静音检查
#    - 静音检查用 --no-serve（只跑检查、不占 8099 端口），进度直接显示在本窗口，
#      报告照常写入 reports/，看板全程在线（随时可开）。
#  桌面「RG660MK巡检」通过 ssh 调用本脚本；每日 08:30 定时任务同样走这里。
# ─────────────────────────────────────────────────────────────────────
D=/data/ai_cpe/hermes/home/aicpe_demo

echo "[1/3] 确保看板服务在跑…"
if ! /etc/init.d/aicpe-board status >/dev/null 2>&1; then
  /etc/init.d/aicpe-board start >/dev/null 2>&1
  sleep 2
fi
i=0
while [ $i -lt 30 ]; do
  if curl -s -m 2 -o /dev/null http://127.0.0.1:8099/; then break; fi
  i=$((i+1)); sleep 1
done
if ! curl -s -m 2 -o /dev/null http://127.0.0.1:8099/; then
  echo "⚠️ 看板服务未就绪，请检查: /etc/init.d/aicpe-board start；日志: $D/demo_board.log"
fi
echo "[2/3] 看板: http://192.168.1.1:8099/  （常驻，随时可开）"
echo "[3/3] 开始静音质检（不播报、不实际开关灯）…（约 3-4 分钟）"
echo

cd "$D" || exit 1
/data/hermes/venv/bin/python aicpe_demo.py --no-audio --no-serve
RC=$?

echo
echo "===== 本轮巡检结束（rc=$RC）====="
latest=$(ls -t "$D"/reports/AI_CPE_功能演示报告_*.md 2>/dev/null | head -1)
[ -n "$latest" ] && echo "报告: $latest"
exit $RC
