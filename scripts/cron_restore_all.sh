#!/bin/bash
# 演示后一键恢复：系统 crontab + 板子 Hermes 任务
set -u
echo "=== 1) 恢复系统 crontab ==="
ssh -o ConnectTimeout=8 root@192.168.1.1 'sh /data/ai_cpe/tools/cron_restore_system.sh'
echo "=== 2) 恢复 Hermes 任务 ==="
bash "$HOME/scripts/cron_restore.sh"
