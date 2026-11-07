#!/bin/sh
# attr_watchdog.sh — 属性服务看门狗
# 每分钟由 cron 调用：health 不通就清残留并重启。
# 设计原则：只在异常时动手，正常时零输出（cron 不产生噪音）。
LOG=/tmp/attr_watchdog.log

api_ok=0; live_ok=0
curl -s -m 5 http://127.0.0.1:8093/health > /dev/null 2>&1 && api_ok=1
curl -s -m 5 -o /dev/null http://127.0.0.1:8094/ 2>/dev/null && live_ok=1
[ "$api_ok" = "1" ] && [ "$live_ok" = "1" ] && exit 0

echo "[$(date '+%F %T')] health 不通，准备重启" >> "$LOG"
# 清理可能的残留进程与失效 pid 文件
for p in $(ps -eo pid,args 2>/dev/null | grep "[a]ttr_service_pc.py" | awk '{print $1}'); do
    kill -9 "$p" 2>/dev/null
done
rm -f /tmp/attr_service.pid
sleep 1
"$HOME/attrctl" start >> "$LOG" 2>&1
sleep 2
if curl -s -m 5 http://127.0.0.1:8093/health > /dev/null 2>&1; then
    echo "[$(date '+%F %T')] 重启成功" >> "$LOG"
else
    echo "[$(date '+%F %T')] 重启失败，需人工介入" >> "$LOG"
fi
