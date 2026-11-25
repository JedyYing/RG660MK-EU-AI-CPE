#!/bin/sh
# bulb_watch.sh —— Matter 灯在线状态巡检（每 2 分钟）
# 记录上下线变化与原因线索，避免演示时才发现灯掉线
LOG=/data/ai_cpe/logs/bulb_watch.log
MAC=d8:fc:92:b8:b1:41
STATE=/tmp/bulb_state
mkdir -p /data/ai_cpe/logs 2>/dev/null

NOW=$(date '+%Y-%m-%d %H:%M:%S')
N=$(hostapd_cli -i ra0 all_sta 2>/dev/null | grep -ci "$MAC")
if [ "$N" -ge 1 ]; then
  CUR=online
else
  CUR=offline
fi
PREV=$(cat "$STATE" 2>/dev/null || echo unknown)

if [ "$CUR" != "$PREV" ]; then
  echo "$NOW $PREV -> $CUR" >> "$LOG"
  if [ "$CUR" = "offline" ]; then
    # 附上最近一次断开原因（hostapd 日志尾部）
    grep -i "$MAC" /var/log/hostapd.log 2>/dev/null | tail -3 | sed "s/^/    /" >> "$LOG"
  fi
  echo "$CUR" > "$STATE"
fi
# 每次都在线则只保留心跳（体积很小）
[ "$CUR" = "online" ] && echo "$NOW online" >> "$LOG"
# 日志滚动：保留最后 500 行
tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
