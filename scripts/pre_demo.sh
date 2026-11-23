#!/bin/bash
# 演示前 15 分钟检查（在笔记本上跑）：bash ~/scripts/pre_demo.sh
# 目的：把"能自愈的抖动"提前消耗掉，确保 8099 十大功能全 pass（无大模型模式 10/10）
set -u
B=192.168.1.1
ok(){ echo "  ✓ $1"; }
bad(){ echo "  ✗ $1"; FAIL=1; }
FAIL=0
# 设备运维令牌（与设备 /data/ai_cpe/secrets/ops_token 一致，权限 0600）：/state、/mode、/run 需要它
TOKF="${RG660MK_DEVICE_TOKEN_FILE:-$HOME/.config/rg660mk/device_ops_token}"
TOK=$(cat "$TOKF" 2>/dev/null || true)
[ -n "$TOK" ] || { echo "缺少设备令牌文件：$TOKF（权限 0600，内容同设备 /data/ai_cpe/secrets/ops_token）"; exit 2; }
export RG660MK_DEVICE_TOKEN="$TOK"
AUTH="Authorization: Bearer $TOK"

echo "===== 1. 板子与 8099 ====="
ping -c 3 -W1 $B >/dev/null 2>&1 && ok "板子 ping 通" || bad "板子 ping 不通"
C=$(curl -s -m 6 -o /dev/null -w "%{http_code}" http://$B:8099/)
[ "$C" = "200" ] && ok "8099=200" || bad "8099=$C"

echo "===== 2. 5G / WAN ====="
ssh -o ConnectTimeout=6 root@$B 'ip -brief addr show ccmni2 2>/dev/null | awk "{print \$3}"; ip route show default 2>/dev/null | grep -c ccmni2' 2>/dev/null | sed 's/^/  /'
if curl -s -m 10 -o /dev/null https://open.feishu.cn/; then ok "飞书通路可达"; else bad "飞书不可达（5G/WAN 异常）"; fi

echo "===== 3. 模式 ====="
M=$(curl -s -m 5 -H "$AUTH" http://$B:8099/state | python3 -c "import json,sys;print((json.load(sys.stdin).get('llm') or {}).get('mode'))" 2>/dev/null)
echo "  当前模式: $M"
if [ "$M" != "cpu-local" ]; then
  echo "  → 切换到 无大模型(cpu-local) 以保证 10/10（在线模式第10项无 MT200 会 skip）"
  curl -s -m 10 -X POST -H "$AUTH" -H 'Content-Type: application/json' -d '{"mode":"cpu-local"}' http://$B:8099/mode >/dev/null
  sleep 2
  echo "  切换后: $(curl -s -m 5 -H "$AUTH" http://$B:8099/state | python3 -c "import json,sys;print((json.load(sys.stdin).get('llm') or {}).get('mode'))" 2>/dev/null)"
fi

echo "===== 4. 灯泡（冷启动后需 1–2 分钟重新关联 AP，最多等 180 秒）====="
BULB_OK=0
for i in $(seq 1 18); do
  if ping -c 2 -W1 192.168.1.134 >/dev/null 2>&1; then BULB_OK=1; break; fi
  echo "  等待灯重连… ${i}0s"
  sleep 10
done
if [ "$BULB_OK" = "1" ]; then
  ok "灯泡 ping 通（等待 $(( (i-1)*10 )) 秒后）"
else
  bad "灯泡 180 秒仍不通 → 请把这盏灯断电 10 秒再上电，等 90 秒后重跑本脚本"
fi
ssh -o ConnectTimeout=6 root@$B 'cd /data/ai_cpe && python3 smarthome.py status 2>&1 | tail -c 300' 2>/dev/null | sed 's/^/  /'

echo "===== 4.5 麦克风 RMS（区分停流/静音）====="
RMS=$(ssh -o ConnectTimeout=6 root@$B 'tail -5 /tmp/voice_rms.csv 2>/dev/null' 2>/dev/null)
if [ -n "$RMS" ]; then
  R_LAST=$(echo "$RMS" | tail -1)
  R_TS=$(echo "$R_LAST" | cut -d, -f1); R_P50=$(echo "$R_LAST" | cut -d, -f2); R_PEAK=$(echo "$R_LAST" | cut -d, -f3)
  SPK=$(echo "$RMS" | awk -F, '{s+=$4} END{print s+0}')
  AGE=$(( $(date +%s) - ${R_TS:-0} ))
  echo "  近 5 个窗口：p50=$R_P50 peak=$R_PEAK 有效语音帧=$SPK（最后更新 ${AGE}s 前，VAD 阈值 450）"
  if [ "$AGE" -gt 180 ]; then bad "RMS 遥测超过 180 秒未更新 → 语音主循环可能卡死"
  elif [ "${SPK:-0}" -gt 0 ]; then ok "检测到语音活动（麦克风在送真实声音）"
  else bad "近 2.5 分钟没有一帧达到 VAD 阈值：若你刚才没说话属正常；若说了话仍为 0 → 麦克风静音，请重插 CM564 或改用 C270"
  fi
else
  bad "无 RMS 遥测（语音可能没起或旧代码）"
fi

echo "===== 5. 亮度（最低档，避免扰民）====="
ssh -o ConnectTimeout=6 root@$B 'cd /data/ai_cpe/matter && ./bin/chip-tool levelcontrol read current-level 22 1 --timeout 20 2>/dev/null | grep CurrentLevel | tail -1' 2>/dev/null | sed 's/^/  /'

if [ "${1:-}" = "--check-only" ]; then
  echo "===== 6. 跑一轮验收（已跳过 --check-only：不动灯、不占演示通道）====="
else
echo "===== 6. 跑一轮验收 ====="
python3 - <<'PY'
import json,os,time,urllib.request
B="http://192.168.1.1:8099"
TOK=os.environ.get("RG660MK_DEVICE_TOKEN","")
def http(p,d=None,t=15):
    h={"Authorization":"Bearer "+TOK}
    if d: h["Content-Type"]="application/json"
    req=urllib.request.Request(B+p, data=json.dumps(d).encode() if d else None, headers=h)
    with urllib.request.urlopen(req, timeout=t) as r: return json.loads(r.read() or b"{}")
try:
    http("/run",{"full":False})
    t0=time.time()
    while time.time()-t0<180:
        time.sleep(3)
        s=http("/state")
        if s.get("phase")!="running": break
    items=s.get("items") or []
    bad=[(i.get("idx"),i.get("name"),i.get("status")) for i in items if i.get("status")!="pass"]
    print("  本轮通过 %d/%d" % (len(items)-len(bad), len(items)))
    for b in bad: print("   非pass:", b)
except Exception as e:
    print("  验收请求失败:", str(e)[:120])
PY

fi

echo "===== 结论 ====="
[ "$FAIL" = "0" ] && echo "  基础项全部就绪；若第 6 步 10/10 即可开始演示" || echo "  有 ✗ 项，按提示处理后重跑"
