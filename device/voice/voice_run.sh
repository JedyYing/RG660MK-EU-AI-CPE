#!/bin/sh
export VOICE_VAD_ON=1600
export VOICE_VAD_START_FRAMES=5
export VOICE_VAD_OFF=1100
# 语音助手启动包装：支持通过 voice_rec_dev 切换采集设备（默认 UGREEN）
DEV=$(cat /data/ai_cpe/voice_rec_dev 2>/dev/null)
[ -n "$DEV" ] && export REC_DEV="$DEV"
exec /data/hermes/venv/bin/python /data/ai_cpe/voice_assistant.py >> /data/ai_cpe/logs/voice.log 2>&1
export TTS_OUTPUT=local
export VOICE_TTS_ENGINE=piper
