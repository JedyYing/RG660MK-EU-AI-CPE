#!/bin/sh
# log_rotate.sh —— 大日志截断（只清访问/心跳类，不动业务数据）
# 每 30 分钟跑一次；单文件超过 8MB 就截断保留最后 2000 行
LIMIT_KB=8192
for f in /data/ai_cpe/logs/camav_access.log \
         /data/ai_cpe/logs/broker.log \
         /data/ai_cpe/logs/mqtt_bridge.log \
         /data/ai_cpe/logs/smarthome_agent.log \
         /data/ai_cpe/logs/voice.jsonl \
         /data/ai_cpe/logs/wifi_assoc.csv \
         /data/ai_cpe/logs/thermal.csv \
         /data/ai_cpe/logs/sherpa_asr.log \
         /var/log/hostapd.log; do
  [ -f "$f" ] || continue
  SZ=$(du -k "$f" 2>/dev/null | awk '{print $1}')
  [ -z "$SZ" ] && continue
  if [ "$SZ" -gt "$LIMIT_KB" ]; then
    tail -n 2000 "$f" > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f"
    echo "$(date '+%F %T') truncated $f ${SZ}KB" >> /data/ai_cpe/logs/log_rotate.log
  fi
done
