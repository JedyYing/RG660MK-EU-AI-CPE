#!/bin/sh
# RG660MK camview(8090 摄像头预览)看门狗
# 背景:C270 为 USB 独占设备,姿态检测每小时用 rg660mk_c270_snapshot 直开 USB 抓帧,
#       与常驻 camview 抢占后,camview 会攥着已失效的 USB 句柄(/proc/PID/fd 里 "(deleted)"),
#       进程不崩溃故 procd respawn 不触发,但 /stream 预览卡死——即"8090 打不开"。
# 本脚本由 cron 每 5 分钟调用:检测句柄失效或取帧失败则重启 camview 自愈。
# 部署背景见工作区 RG660MK摄像头实时预览_部署说明.md。完全可逆:删本脚本 + 删 cron 条目即还原。
PROG=/data/camview/camview
PORT=8090
LOG=/data/camview/watchdog.log

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) $1" >> "$LOG"; }

PID=$(pgrep -f "$PROG" | head -1)
NEED=0
REASON=""

if [ -z "$PID" ]; then
    NEED=1; REASON="进程不存在"
elif ls -l /proc/$PID/fd 2>/dev/null | grep -qi 'usb.*(deleted)'; then
    NEED=1; REASON="USB句柄失效(deleted)"
else
    BYTES=$(curl -s -m 8 "http://127.0.0.1:${PORT}/snapshot" 2>/dev/null | wc -c | tr -d ' ')
    [ "${BYTES:-0}" -lt 1000 ] && { NEED=1; REASON="snapshot取帧失败(${BYTES}字节)"; }
fi

if [ "$NEED" = "1" ]; then
    log "异常:${REASON} → 重启 camview"
    /etc/init.d/camview enabled 2>/dev/null || /etc/init.d/camview enable
    /etc/init.d/camview restart >/dev/null 2>&1
    sleep 5
    NPID=$(pgrep -f "$PROG" | head -1)
    if [ -n "$NPID" ] && ! ls -l /proc/$NPID/fd 2>/dev/null | grep -qi 'usb.*(deleted)'; then
        log "已重启恢复,新PID=${NPID}"
    else
        log "重启后仍异常,需人工排查(USB供电/摄像头连接)"
    fi
fi

# 日志滚动:超过 200 行只保留最近 100 行(日志不存在则跳过)
if [ -f "$LOG" ]; then
    LINES=$(wc -l < "$LOG" 2>/dev/null | tr -d ' ')
    if [ "${LINES:-0}" -gt 200 ]; then
        tail -100 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
    fi
fi
