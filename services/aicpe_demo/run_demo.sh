#!/bin/bash
# ─────────────────────────────────────────────────────────────────────
# AI CPE 五大功能演示 · 一键启动器（桌面双击 / 终端可用）
#   双击 .desktop → 本脚本：连设备 → 重启演示套件 → 打开实时看板
#   用法：bash run_demo.sh [--desktop] [--no-open] [--no-audio] [其余参数透传设备]
#
#   直连方式：优先走设备 IPv6 地址（ULA fdc3:8285:b409::1），避开
#   192.168.1.0/24 与公司网/飞连静态路由的冲突；ULA 不通自动回退
#   链路本地地址（此时看板自动改走本机 SSH 隧道）。详见仓库 README。
# ─────────────────────────────────────────────────────────────────────
set -u
TITLE='AI CPE 五大功能演示'
printf '\033]0;%s\007' "$TITLE"

DESKTOP=0; NOOPEN=0; OPTS=""
for arg in "$@"; do
  case "$arg" in
    --desktop) DESKTOP=1 ;;
    --no-open) NOOPEN=1 ;;
    *) OPTS="$OPTS $arg" ;;
  esac
done
OPTS="${OPTS# }"

pause_end() { if [ "$DESKTOP" -eq 1 ]; then echo; read -r -p "按回车键关闭本窗口… " _ 2>/dev/null || true; fi; return 0; }
die() { echo; echo "  ✗ $1"; [ -n "${2:-}" ] && echo "    ↳ $2"; pause_end; exit 1; }

KEY="$HOME/.ssh/id_ed25519_termux"
SSHOPTS=(-i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o ConnectTimeout=8 -o LogLevel=ERROR)
ULA='fdc3:8285:b409::1'
LL='fe80::e868:e8ff:fe53:6a31%enx9c69d3c6b35d'
V4='192.168.1.1'

# ① 找设备（校验型号含 evb6988，防止路由劫持时连错机器）
DEVHOST=""; USETUN=0
echo "→ 正在寻找设备（USB 直连）…"
for cand in "$ULA" "$LL" "$V4"; do
  out=$(ssh "${SSHOPTS[@]}" "root@$cand" 'echo P_OK; cat /tmp/sysinfo/model 2>/dev/null' 2>/dev/null)
  if echo "$out" | grep -q P_OK && echo "$out" | grep -q evb6988; then
    DEVHOST="$cand"; echo "  ✓ 已连接设备：$DEVHOST"; break
  fi
  echo "  · $cand 不可达，尝试下一个…"
done
[ -n "$DEVHOST" ] || die "未找到设备" "检查：①USB 网卡插好 ②设备已上电 ③网卡名仍为 enx9c69d3c6b35d（ip link 可查）"

# ② 计算看板地址（链路本地地址浏览器不可用 → 走本机 SSH 隧道）
case "$DEVHOST" in
  *%*) USETUN=1; URL="http://127.0.0.1:18099/" ;;
  *:*) URL="http://[${DEVHOST}]:8099/" ;;
  *)   URL="http://${DEVHOST}:8099/" ;;
esac

# ③ 设备侧：清旧实例 → 启动新一轮演示（设备无 pkill，用 pgrep+kill）
echo "→ 正在设备上启动演示套件…"
ssh "${SSHOPTS[@]}" "root@$DEVHOST" sh -s -- "$OPTS" <<'REMOTE' >/dev/null 2>&1 || true
cd /data/ai_cpe/hermes/home/aicpe_demo 2>/dev/null || exit 1
for p in $(pgrep -f aicpe_demo.py); do kill "$p" 2>/dev/null; done
sleep 1
for p in $(pgrep -f aicpe_demo.py); do kill -9 "$p" 2>/dev/null; done
sleep 1
nohup /data/hermes/venv/bin/python aicpe_demo.py --port 8099 --keep-alive 3600 $1 >> demo_desktop.log 2>&1 &
REMOTE
sleep 1

# ④ 需要隧道则建立（后台，退出时清理）
TPID=""
if [ "$USETUN" -eq 1 ]; then
  echo "→ 建立看板隧道（127.0.0.1:18099 → 设备:8099）…"
  ssh "${SSHOPTS[@]}" -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -N \
      -L 127.0.0.1:18099:127.0.0.1:8099 "root@$DEVHOST" &
  TPID=$!
  trap '[ -n "$TPID" ] && kill "$TPID" 2>/dev/null' EXIT
fi

# ⑤ 等看板就绪（最多 40 秒）
OK=0
for i in $(seq 1 40); do
  if curl -fs -m 2 --noproxy '*' -o /dev/null "$URL" 2>/dev/null; then OK=1; break; fi
  sleep 1
done
[ "$OK" -eq 1 ] || die "看板未能就绪（40 秒超时）" "设备侧日志：/data/ai_cpe/hermes/home/aicpe_demo/demo_desktop.log"
echo "  ✓ 看板已就绪"

# ⑥ 打开浏览器
if [ "$NOOPEN" -eq 1 ]; then
  echo "→ （测试模式：跳过打开浏览器）"
else
  echo "→ 打开浏览器…"
  command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" >/dev/null 2>&1 || echo "   请手动在浏览器打开：$URL"
fi

# ⑦ 说明与保持窗口
cat <<EOF

── 说明 ──────────────────────────────
• 浏览器看板：$URL
• 演示进度 / 现场画面 / 日志，都在看板页面实时显示
• 全程约 5～10 分钟；跑完后看板继续在线 1 小时
• 报告（设备）：/data/ai_cpe/hermes/home/aicpe_demo/reports/
• 本窗口保持打开即可$([ "$USETUN" -eq 1 ] && echo '（看板经本窗口隧道转发，关闭=断开）' || echo '，也可以随时关闭')
──────────────────────────────────────
EOF
pause_end
