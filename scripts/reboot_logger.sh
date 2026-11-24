#!/bin/sh
# reboot_logger.sh —— 记录谁调用了 reboot/poweroff/halt（2026-11-24）
# 安装：ln -sf /data/ai_cpe/tools/reboot_logger.sh /sbin/reboot （poweroff/halt 同理）
LOG=/data/ai_cpe/logs/reboot_caller.log
[ -d /data/ai_cpe/logs ] || LOG=/tmp/reboot_caller.log
CMD=$(basename "$0")
mkdir -p /data/ai_cpe/logs 2>/dev/null
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') $CMD called ==="
  echo "argv: $0 $*"
  echo "ppid: $PPID  parent: $(tr '\0' ' ' < /proc/$PPID/cmdline 2>/dev/null)"
  echo "self: $(tr '\0' ' ' < /proc/$$/cmdline 2>/dev/null)"
  echo "uptime_s: $(cut -d' ' -f1 /proc/uptime 2>/dev/null)"
  echo "user: $(id 2>/dev/null)"
} >> "$LOG" 2>&1
sync
if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] would exec /bin/busybox $CMD $*"
  exit 0
fi
exec /bin/busybox "$CMD" "$@"
