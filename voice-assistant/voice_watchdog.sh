#!/bin/sh
# 语音助手看门狗：不在就拉起（每分钟由 cron 调用）
# 2026-11-06：现场出现进程被会话带崩后“喊三次没反应”，加此守护
if pgrep -f 'voice_assistant.py' >/dev/null 2>&1; then
    exit 0
fi
cd /data/ai_cpe || exit 1
start-stop-daemon -S -b -m -p /var/run/voice_assistant.pid \
    -x /data/hermes/venv/bin/python -- /data/ai_cpe/voice_assistant.py \
    >> /data/ai_cpe/logs/voice.log 2>&1
