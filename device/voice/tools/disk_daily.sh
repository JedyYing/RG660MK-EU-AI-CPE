#!/bin/sh
# disk_daily.sh —— 每日磁盘/DB/日志增长记录（供计算增长速率与可用天数）
L=/data/ai_cpe/logs/health/disk_daily.csv
mkdir -p /data/ai_cpe/logs/health
[ -f "$L" ] || echo "date,data_avail_kb,state_db_bytes,hermes_logs_kb,aicpe_logs_kb" > "$L"
AVAIL=$(df -k /data 2>/dev/null | awk 'NR==2{print $4}')
DB=$(wc -c < /data/hermes/.hermes/state.db 2>/dev/null || echo 0)
HL=$(du -sk /data/hermes/.hermes/logs 2>/dev/null | awk '{print $1}')
AL=$(du -sk /data/ai_cpe/logs 2>/dev/null | awk '{print $1}')
echo "$(date +%F),$AVAIL,$DB,$HL,$AL" >> "$L"
