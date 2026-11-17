#!/bin/bash
# 演示结束后恢复板子上被暂停的 18 个 Hermes cron 任务
# 用法：bash ~/cron_restore.sh
set -u
IDS=$(tr '\n' ' ' < "$HOME/rg660_diag/cron_paused_ids.txt")
echo "将恢复: $(echo $IDS | wc -w) 个任务"
ssh -o ConnectTimeout=8 root@192.168.1.1 "export HOME=/data/hermes
for id in $IDS; do /data/hermes/venv/bin/hermes cron resume \$id >/dev/null 2>&1; done
echo '--- 恢复后 [active] 数量 ---'
/data/hermes/venv/bin/hermes cron list --all 2>/dev/null | grep -c '\[active\]'
echo '--- 仍保持关闭（必须）---'
/data/hermes/venv/bin/hermes cron list --all 2>/dev/null | grep -E '1e696b1ed539|重启溯源' | head -2"
echo "完成。注意：1e696b1ed539（重启溯源/strace）保持 disabled，不要恢复。"
