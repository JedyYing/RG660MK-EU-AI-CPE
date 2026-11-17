#!/bin/bash
# 笔记本侧同步探测（有线）：30s 一轮，采端口可达性；每 5 分钟拉取板端健康日志
# 单实例；证据落在 ~/rg660_diag/health/，最多保留 24 份快照
set -u
OUT="$HOME/rg660_diag/health"; mkdir -p "$OUT"
CSV="$OUT/lan_probe.csv"
LOCK=/tmp/lan_probe.lock
exec 9>"$LOCK"; flock -n 9 || { echo "lan_probe already running"; exit 1; }
[ -f "$CSV" ] || echo "epoch,local,rtt_ms,p22,p80,p1883,p6006,p8099,http8099" > "$CSV"
B=192.168.1.1
i=0
while true; do
  E=$(date +%s); L=$(date '+%F %T')
  R=$(ping -c 2 -W1 $B 2>/dev/null | awk -F'/' '/rtt|round-trip/{print $5}')
  [ -z "$R" ] && R=""
  probe(){ timeout 3 bash -c "echo > /dev/tcp/$B/$1" 2>/dev/null && echo 1 || echo 0; }
  P22=$(probe 22); P80=$(probe 80); P1883=$(probe 1883); P6006=$(probe 6006); P8099=$(probe 8099)
  H=$(curl -s -m 4 -o /dev/null -w "%{http_code}" http://$B:8099/ 2>/dev/null)
  echo "$E,$L,$R,$P22,$P80,$P1883,$P6006,$P8099,$H" >> "$CSV"
  i=$((i+1))
  if [ $((i % 10)) -eq 0 ]; then
    TS=$(date +%Y%m%d_%H%M)
    scp -q -o ConnectTimeout=6 root@$B:/data/ai_cpe/logs/health/health.log "$OUT/health_$TS.log" 2>/dev/null
    scp -q -o ConnectTimeout=6 root@$B:/data/ai_cpe/logs/health/fail_*.txt "$OUT/" 2>/dev/null
    ls -1t "$OUT"/health_*.log 2>/dev/null | tail -n +25 | xargs -r rm -f
    ls -1t "$OUT"/fail_*.txt 2>/dev/null | tail -n +200 | xargs -r rm -f
  fi
  sleep 30
done
