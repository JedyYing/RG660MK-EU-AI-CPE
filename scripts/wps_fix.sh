#!/bin/bash
# wps_fix.sh —— WPS“对话框打开时命令无法执行/无法打开文件”一键修复
# 用法：
#   wps_fix.sh            清理残留实例
#   wps_fix.sh 文件.pptx   清理后重新打开文件
#   wps_fix.sh --check    只列出残留进程，不动手
set -u
NAMES="wpsoffice wps wpp et wpspdf wpscloudsvr wpsupdate"

if [ "${1:-}" = "--check" ]; then
  for n in $NAMES; do pgrep -a -x "$n" 2>/dev/null; done
  exit 0
fi

FILE="${1:-}"
for n in $NAMES; do pkill -x "$n" 2>/dev/null; done
sleep 2
for n in $NAMES; do pkill -9 -x "$n" 2>/dev/null; done
sleep 1

LEFT=0
for n in $NAMES; do pgrep -x "$n" >/dev/null 2>&1 && LEFT=1; done
if [ "$LEFT" = "1" ]; then
  echo "仍有残留进程："
  for n in $NAMES; do pgrep -a -x "$n" 2>/dev/null; done
  exit 1
fi

if [ -n "$FILE" ] && [ -f "$FILE" ]; then
  nohup wps "$FILE" >/dev/null 2>&1 &
  echo "已清理残留实例，并重新打开：$FILE"
else
  echo "已清理 WPS 残留实例。现在可以正常打开文件了。"
fi
