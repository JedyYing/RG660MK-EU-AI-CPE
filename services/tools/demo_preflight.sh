#!/bin/bash
# demo_preflight.sh — 演示前 60 秒体检（笔记本 + RG660MK + 公网隧道 + 看板）
#
# 用法:
#   demo_preflight.sh          # 只检查，输出 PASS/FAIL 清单和可用公网地址
#   demo_preflight.sh --fix    # 检查 + 安全自愈（隧道/看板/统计服务；不动网络配置）
#
# 设计原则: 不改网络配置、不重启模组；只重启我们自己的服务。
# 退出码: 0 全通过；1 有 FAIL（附修复建议）

set -u
BOARD=192.168.1.1
SSH="ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=no root@$BOARD"
FIX=0
[ "${1:-}" = "--fix" ] && FIX=1
FAILS=0
say()  { printf '%s\n' "$*"; }
ok()   { printf '  [PASS] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAILS=$((FAILS+1)); }
warn() { printf '  [WARN] %s\n' "$*"; }

say "=============================================="
say " AI CPE 演示前体检  $(date '+%F %H:%M:%S')"
say "=============================================="

# ---------- 1. 本机出网路径 ----------
say "1) 本机上网"
ROUTES=$(ip route show default 2>/dev/null)
say "     默认路由: $(echo "$ROUTES" | tr '\n' ';')"
if echo "$ROUTES" | grep -q 'dev utun'; then
  warn "utun(VPN) 抢了默认路由 —— 这是昨天本机断网的典型原因"
  say "     修复: sudo ip route del default dev utun   （或关掉 VPN 客户端）"
fi
NET_OK=0
for IF in $(echo "$ROUTES" | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | sort -u); do
  OUT=$(curl -4 -s -m 6 --interface "$IF" -o /dev/null -w '%{http_code}' https://www.baidu.com 2>/dev/null)
  if [ "$OUT" = "200" ]; then ok "经 $IF 出网正常 (baidu 200)"; NET_OK=1
  else warn "经 $IF 出网失败 (http=$OUT)"; fi
done
[ "$NET_OK" = 1 ] || bad "所有默认路由都出不了网 → 查 WiFi/网线/VPN"
DNS=$(getent hosts ai.phicotek.com >/dev/null 2>&1 && echo ok || echo fail)
[ "$DNS" = ok ] && ok "DNS 解析正常 (ai.phicotek.com)" || bad "DNS 解析失败 → 换 DNS 或断网重连"

# ---------- 2. 模组连通 ----------
say "2) 模组连通"
ping -c 2 -W 1 "$BOARD" >/dev/null 2>&1 && ok "ping $BOARD 通" || bad "ping 不通 $BOARD → 查 USB 网卡/线（enx* 是否还在）"
if $SSH 'echo ok' >/dev/null 2>&1; then ok "SSH 登录正常"; else bad "SSH 不通（看板可能仍在跑，但无法自检）"; fi

# ---------- 3. 模组健康 ----------
say "3) 模组健康"
$SSH '
UP=$(cut -d. -f1 /proc/uptime)
T=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
MEM=$(free -m | awk "/Mem:/{print \$3\"/\"\$2\"MB\"}")
DISK=$(df -h /data | awk "NR==2{print \$4\" 可用\"}")
WAN=$(ip -4 -o addr show ccmni2 2>/dev/null | awk "{print \$4}")
SIG=$(uqmi -d /dev/cdc-wdm0 --get-signal-info 2>/dev/null | tr -d "\n" | cut -c1-120)
echo "UPTIME=$UP"; echo "TEMP=$T"; echo "MEM=\"$MEM\""; echo "DISK=\"$DISK\""; echo "WAN=\"$WAN\""; echo "SIG=\"$SIG\""
' > /tmp/_pf_board 2>/dev/null
. /tmp/_pf_board 2>/dev/null || true
UP=${UPTIME:-0}; TEMP=${TEMP:-0}
[ "$UP" -gt 1800 ] && ok "持续运行 $((UP/60)) 分钟（无重启）" || warn "运行仅 $((UP/60)) 分钟 → 刚重启过，注意观察"
TC=$(( ${TEMP:-0} / 1000 ))
[ "$TC" -lt 75 ] && ok "SoC 温度 ${TC}°C" || warn "SoC 温度 ${TC}°C 偏高 → 加强散热/通风"
[ -n "${WAN:-}" ] && ok "5G WAN 在线 $WAN" || bad "5G WAN 无地址 → ifup wan"
say "     内存 $MEM ｜ /data $DISK"
[ -n "${SIG:-}" ] && say "     信号 $SIG"

# ---------- 4. 看板与相机 ----------
say "4) 看板与相机"
C8099=$($SSH 'curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8099/state' 2>/dev/null)
C8090=$($SSH 'curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/snapshot' 2>/dev/null)
[ "$C8099" = 200 ] && ok "看板 8099 正常" || { bad "看板 8099 不通"; [ "$FIX" = 1 ] && { say "     → 重启 aicpe-board"; $SSH '/etc/init.d/aicpe-board restart' >/dev/null 2>&1; }; }
[ "$C8090" = 200 ] && ok "相机 8090 正常" || { bad "相机 8090 不通"; [ "$FIX" = 1 ] && { say "     → 重启 cam-stack"; $SSH '/etc/init.d/cam-stack restart' >/dev/null 2>&1; }; }

# ---------- 5. 公网地址 ----------
say "5) 公网演示地址"
URL=$($SSH 'cat /www/board_url.txt 2>/dev/null' 2>/dev/null | tr -d '\r\n')
if [ -n "$URL" ]; then
  H=$(curl -4 -s -m 15 -o /dev/null -w '%{http_code}' "$URL/" 2>/dev/null)
  if [ "$H" = 200 ]; then ok "公网 200  $URL"
  else
    bad "公网不可用 (http=$H)  $URL"
    if [ "$FIX" = 1 ]; then
      say "     → 触发隧道守护"; $SSH 'sh /data/ai_cpe/tools/board_tunnel_keeper.sh' >/dev/null 2>&1
      sleep 4; URL=$($SSH 'cat /www/board_url.txt' 2>/dev/null | tr -d '\r\n')
      H2=$(curl -4 -s -m 15 -o /dev/null -w '%{http_code}' "$URL/" 2>/dev/null)
      [ "$H2" = 200 ] && ok "自愈成功，新地址 $URL" || bad "自愈后仍不通"
    fi
  fi
else bad "读不到 /www/board_url.txt（隧道从未建立？）"; fi

# ---------- 6. 演示状态 ----------
say "6) 看板状态"
$SSH 'curl -s -m 8 http://127.0.0.1:8099/state' 2>/dev/null | python3 -c '
import sys, json
try: d = json.load(sys.stdin)
except Exception: print("STATE_PARSE_FAIL"); raise SystemExit
print("PHASE=%s" % d.get("phase"))
print("SUMMARY=%s" % (d.get("summary") or ""))
bad = [i["name"] for i in d.get("items", []) if i.get("status") == "fail"]
print("FAILEDITEMS=%s" % (",".join(bad) if bad else "none"))
for i in d.get("items", []):
    print("  %d. %-18s %-6s %.1fs" % (i["idx"], i["name"], i["status"], i.get("dur") or 0))
' 2>/dev/null || bad "读不到 /state"
if [ "$FIX" = 1 ]; then
  $SSH '/data/hermes/venv/bin/python /data/ai_cpe/token_cost.py' >/dev/null 2>&1 && ok "Token 统计已刷新"
fi

say "----------------------------------------------"
if [ "$FAILS" = 0 ]; then
  say "结论: 全通过，可以直接演示"
  say "公网地址: ${URL:-'(读取失败)'}"
else
  say "结论: $FAILS 项异常，按上面的提示处理；或跑 $0 --fix"
fi
say "----------------------------------------------"
[ "$FAILS" = 0 ]
