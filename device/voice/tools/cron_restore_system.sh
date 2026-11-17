#!/bin/sh
# 演示后恢复系统 crontab（保留健康监控），并提示 Hermes 任务恢复
B=$(ls -t /etc/crontabs/root.bak_demo_* 2>/dev/null | head -1)
if [ -z "$B" ]; then echo "找不到备份 root.bak_demo_*"; exit 1; fi
cp -f "$B" /etc/crontabs/root
grep -q "health_watch" /etc/crontabs/root || echo "*/2 * * * * sh /data/ai_cpe/tools/health_watch.sh >/dev/null 2>&1" >> /etc/crontabs/root
grep -q "voice_guard" /etc/crontabs/root || echo "*/2 * * * * sh /data/ai_cpe/tools/voice_guard.sh >/dev/null 2>&1" >> /etc/crontabs/root
/etc/init.d/cron restart >/dev/null 2>&1
echo "已恢复自 $B，当前生效行："
grep -vE "^#|^$" /etc/crontabs/root
