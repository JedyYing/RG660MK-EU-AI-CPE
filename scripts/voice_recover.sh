#!/bin/bash
# 语音卡死一键恢复（小皮不响应时跑这个）
# 现象：说了唤醒词没反应，voice.jsonl 停在某条 "小皮休息" 之后
# 原因：休眠路径停止读麦克风，arecord 写满管道后互等死锁
set -u
echo "===== 重启前状态 ====="
ssh -o ConnectTimeout=8 root@192.168.1.1 'tail -1 /data/ai_cpe/logs/voice.jsonl | cut -c1-100; ps w | grep -c "[a]record"' 2>/dev/null

echo "===== 重启语音栈 ====="
ssh -o ConnectTimeout=10 root@192.168.1.1 '
for p in $(ps w | grep -E "[v]oice_assistant|[a]record" | awk "{print \$1}"); do kill -9 $p 2>/dev/null; done
for p in $(ps w | grep "sherpa-onnx-offline-websocket-server" | awk "{print \$1}"); do kill -9 $p 2>/dev/null; done
sleep 3
/etc/init.d/aicpe-boot start >/dev/null 2>&1
sleep 12
echo "voice_pid=$(ps w | grep "[v]oice_assistant" | awk "{print \$1}" | head -1)"
echo "arecord_pid=$(ps w | grep "[a]record" | awk "{print \$1}" | head -1)"
tail -2 /data/ai_cpe/logs/voice.jsonl | cut -c1-110
' 2>/dev/null

echo "===== 验证 ====="
echo "现在对麦克风说一句「你好小皮」"
sleep 8
ssh -o ConnectTimeout=8 root@192.168.1.1 'tail -3 /data/ai_cpe/logs/voice.jsonl | cut -c1-120' 2>/dev/null
echo "若出现 kind=turn/listen_start 即已恢复；仍是旧时间戳则再跑一次本脚本"
