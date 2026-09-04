#!/usr/bin/env bash
# 00_device_probe.sh — RG660MK 只读环境探测(SSH 恢复后运行)
# 用法: SSH_KEY=/path/key HOST=root@192.168.1.1 ./00_device_probe.sh
set -u
HOST="${HOST:-root@192.168.1.1}"
SSH_KEY="${SSH_KEY:-}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
[ -n "$SSH_KEY" ] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY -o IdentitiesOnly=yes"
WR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$WR/logs/device_probe.log"

sshx() { ssh $SSH_OPTS "$HOST" "$@"; }

{
echo "===== RG660MK 只读探测 $(date -Iseconds) ====="
echo "### uname"; sshx 'uname -a'
echo "### openwrt_release"; sshx 'cat /etc/openwrt_release 2>/dev/null'
echo "### cpuinfo"; sshx 'cat /proc/cpuinfo'
echo "### LONG_BIT/nproc"; sshx 'getconf LONG_BIT; nproc'
echo "### mem"; sshx 'free -h 2>/dev/null || cat /proc/meminfo'
echo "### df"; sshx 'df -hT'
echo "### mount"; sshx 'mount'
echo "### libc/loader"; sshx 'ls -l /lib/ld-* /lib/libc* 2>/dev/null; ls -l /lib/ld-musl* 2>/dev/null'
echo "### video nodes"; sshx 'ls -l /dev/video* 2>/dev/null || echo NO_VIDEO_NODE'
echo "### usb (lsusb)"; sshx 'lsusb 2>/dev/null || cat /sys/kernel/debug/usb/devices 2>/dev/null'
echo "### v4l2-ctl?"; sshx 'command -v v4l2-ctl && v4l2-ctl --list-devices 2>/dev/null || echo NO_V4L2CTL'
echo "### thermal"; sshx 'for z in /sys/class/thermal/thermal_zone*; do echo -n "$z: "; cat $z/type 2>/dev/null; cat $z/temp 2>/dev/null; cat $z/trip_point_0_temp 2>/dev/null; done'
echo "### dmesg tail (usb/uvc)"; sshx 'dmesg 2>/dev/null | grep -iE "usb|uvc|video" | tail -40'
echo "===== 探测结束 ====="
} 2>&1 | tee -a "$OUT"
echo "写入: $OUT"
