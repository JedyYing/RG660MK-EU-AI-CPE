#!/bin/bash
# 上电抢现场：等 SSH 恢复后立刻抓崩溃快照 + 临时关闭系统 crontab 周期任务
# 用法：nohup bash ~/scripts/postboot_capture.sh > ~/rg660_diag/postboot.log 2>&1 &
set -u
OUT="$HOME/rg660_diag/postboot_$(date +%Y%m%d_%H%M).txt"
B=192.168.1.1
echo "[$(date +%T)] 等待板子上电..."
for i in $(seq 1 90); do
  if timeout 8 ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$B 'true' 2>/dev/null; then
    echo "[$(date +%T)] SSH 恢复，开始抓现场"
    {
      echo "=== 抓取时间 $(date '+%F %T') ==="
      timeout 30 ssh -o ConnectTimeout=6 root@$B '
        echo "-- uptime/load --"; uptime; cat /proc/loadavg
        echo "-- 进程数 --"; ps w | wc -l
        echo "-- pid/threads 上限 --"; cat /proc/sys/kernel/pid_max /proc/sys/kernel/threads-max 2>/dev/null
        echo "-- cgroup pids --"; cat /sys/fs/cgroup/pids.current /sys/fs/cgroup/pids.max 2>/dev/null
        echo "-- CPU top8 --"; top -bn1 2>/dev/null | head -14 | tail -9
        echo "-- 内存 --"; free -m | head -2
        echo "-- 磁盘 --"; df -h /data | tail -1
        echo "-- dmesg 尾 60 --"; dmesg | tail -60
        echo "-- OOM --"; dmesg | grep -iE "oom|killed process" | tail -10
        echo "-- 健康快照文件 --"; ls -lt /data/ai_cpe/logs/health/fail_*.txt 2>/dev/null | head -3
        echo "-- 最新快照前 80 行 --"; F=$(ls -t /data/ai_cpe/logs/health/fail_*.txt 2>/dev/null | head -1); [ -n "$F" ] && head -80 "$F"
        echo "-- 系统 crontab --"; cat /etc/crontabs/root
        echo "-- 语音 --"; ps w | grep -cE "[v]oice_assistant"; tail -3 /data/ai_cpe/logs/voice.jsonl 2>/dev/null
      ' 2>&1
      # 备份并关闭除健康监控外的系统周期任务
      timeout 30 ssh -o ConnectTimeout=6 root@$B '
        cp -f /etc/crontabs/root /etc/crontabs/root.bak_demo_$(date +%H%M)
        awk "{ if (\$0 ~ /health_watch/) print; else if (\$0 ~ /^[^#]/ && NF>0) print \"#DISABLED# \" \$0; else print }" /etc/crontabs/root > /tmp/ct.new
        mv /tmp/ct.new /etc/crontabs/root
        /etc/init.d/cron restart >/dev/null 2>&1
        echo "-- 关闭后的 crontab（非注释行）--"; grep -vE "^#|^$" /etc/crontabs/root
      ' 2>&1
    } > "$OUT" 2>&1
    echo "[$(date +%T)] 现场已保存: $OUT"
    exit 0
  fi
  sleep 10
done
echo "[$(date +%T)] 超时：15 分钟内未等到 SSH"
