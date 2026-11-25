#!/bin/sh
# bulb_watch.sh —— Matter 灯在线状态巡检（每 2 分钟）
# 状态：online（Wi-Fi+IP 都通）/ wifi_only（关联但 IP 不可达=协议栈卡死）/ offline（无关联）
LOG=/data/ai_cpe/logs/bulb_watch.log
MAC=d8:fc:92:b8:b1:41
IP=192.168.1.134
STATE=/tmp/bulb_state
mkdir -p /data/ai_cpe/logs 2>/dev/null

NOW=$(date '+%Y-%m-%d %H:%M:%S')
N=$(hostapd_cli -i ra0 all_sta 2>/dev/null | grep -ci "$MAC")
if [ "$N" -ge 1 ]; then
  if ping -c 1 -W 1 "$IP" >/dev/null 2>&1; then
    CUR=online
  else
    CUR=wifi_only
  fi
else
  CUR=offline
fi

PREV=$(cat "$STATE" 2>/dev/null || echo unknown)
if [ "$CUR" != "$PREV" ]; then
  echo "$NOW $PREV -> $CUR" >> "$LOG"
  if [ "$CUR" != "online" ]; then
    grep -i "$MAC" /var/log/hostapd.log 2>/dev/null | tail -3 | sed "s/^/    /" >> "$LOG"
  fi
  echo "$CUR" > "$STATE"
fi

# 持续 wifi_only 超过 5 分钟 → 明确提示需要断电（协议栈卡死，软件无法恢复）
if [ "$CUR" = "wifi_only" ]; then
  CNT=$(cat /tmp/bulb_wifi_only_cnt 2>/dev/null || echo 0)
  CNT=$((CNT + 1))
  echo "$CNT" > /tmp/bulb_wifi_only_cnt
  [ "$CNT" -eq 3 ] && echo "$NOW WARN 灯已连续 5 分钟 wifi_only（关联但 IP 不通）→ 需断电 10 秒恢复" >> "$LOG"
else
  echo 0 > /tmp/bulb_wifi_only_cnt
  [ "$CUR" = "online" ] && echo "$NOW online" >> "$LOG"
fi

tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
