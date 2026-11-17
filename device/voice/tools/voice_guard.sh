#!/bin/sh
# voice_guard.sh —— 语音看门狗：以"音频读取心跳"判断推进，不用"有无识别结果"
# 触发条件：voice_assistant 进程缺失，或 /tmp/voice_hb 超过 180 秒未更新
# 限流：每 3600 秒最多重启 3 次；每次重启前先 kill -9 残留 arecord
D=/data/ai_cpe/logs/health
mkdir -p $D
exec 8>/tmp/voice_guard.lock
flock -n 8 || exit 0

NOW=$(date +%s)
HB=$(cat /tmp/voice_hb 2>/dev/null || echo 0)
AGE=$((NOW - HB))
V=$(ps w 2>/dev/null | grep -c "[v]oice_assistant")

if [ "$V" -ge 1 ] && [ "$AGE" -lt 180 ]; then exit 0; fi


# 热插拔会把 Mic 增益重置为 100%（255），统一回 75%（191）
G=$(amixer -c 2 sget Mic 2>/dev/null | awk "/Mono:/{print $4}")
[ -n "$G" ] && [ "$G" != "191" ] && { amixer -c 2 sset Mic 75% >/dev/null 2>&1; echo "$(date '+%F %T') 增益 $G -> 191" >> $D/voice_guard.log; }

ST=$D/voice_guard.state
CNT=0; WIN=0
[ -f "$ST" ] && read CNT WIN < "$ST" 2>/dev/null
[ -z "$CNT" ] && CNT=0
[ -z "$WIN" ] && WIN=0
if [ $((NOW - WIN)) -gt 3600 ]; then CNT=0; WIN=$NOW; fi
if [ "$CNT" -ge 3 ]; then
  echo "$(date '+%F %T') 限流中：本小时已重启 $CNT 次（voice=$V hb_age=${AGE}s），本轮跳过" >> $D/voice_guard.log
  exit 0
fi
CNT=$((CNT + 1)); echo "$CNT $WIN" > "$ST"
echo "$(date '+%F %T') 触发重启 #$CNT：voice=$V hb_age=${AGE}s" >> $D/voice_guard.log

for p in $(ps w 2>/dev/null | grep -E "[v]oice_assistant|[a]record" | awk '{print $1}'); do
  kill -9 "$p" 2>/dev/null
done
sleep 2
/etc/init.d/aicpe-boot start >/dev/null 2>&1
sleep 10
echo "$(date '+%F %T') 重启后：voice=$(ps w 2>/dev/null | grep -c '[v]oice_assistant') arecord=$(ps w 2>/dev/null | grep -c '[a]record') hb_age=$(( $(date +%s) - $(cat /tmp/voice_hb 2>/dev/null || echo 0) ))s" >> $D/voice_guard.log
