#!/bin/bash
# 唤醒成功率测量：用法 bash ~/wake_rate.sh [窗口秒]；建议先说 10 次"你好，小皮"再跑
W=${1:-120}
ssh -o ConnectTimeout=8 root@192.168.1.1 "/data/hermes/venv/bin/python /data/ai_cpe/tools/wake_rate.py $W"
