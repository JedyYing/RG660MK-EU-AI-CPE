#!/bin/bash
# sg885g_forward.sh —— SG885G USB 设备在线时自动建立 adb 端口转发
# 28080 -> SG885G:8080 (LLM), 28081 -> SG885G:8081 (VLM)
set -u
while true; do
  DEV=$(adb devices 2>/dev/null | awk 'NR>1 && $2=="device" {print $1; exit}')
  if [ -n "$DEV" ]; then
    CUR=$(adb -s "$DEV" forward --list 2>/dev/null)
    echo "$CUR" | grep -q "tcp:28080" || adb -s "$DEV" forward tcp:28080 tcp:8080 >/dev/null 2>&1
    echo "$CUR" | grep -q "tcp:28081" || adb -s "$DEV" forward tcp:28081 tcp:8081 >/dev/null 2>&1
  fi
  sleep 10
done
