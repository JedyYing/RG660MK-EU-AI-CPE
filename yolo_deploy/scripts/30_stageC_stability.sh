#!/usr/bin/env bash
# 30_stageC_stability.sh — 阶段C:连续稳定性测试(默认30分钟)
# 选性能/占用平衡配置(默认 size=320 threads=2),连续运行并采样 CPU/RSS/温度/丢帧。
# 同时周期性核查 5G/Wi-Fi/路由未受影响。用法: SSH_KEY=... HOST=... DURATION=1800 ./30_stageC_stability.sh
set -eu
HOST="${HOST:-root@192.168.1.1}"
SSH_KEY="${SSH_KEY:-}"
SIZE="${SIZE:-320}"; TH="${TH:-2}"; DURATION="${DURATION:-1800}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
[ -n "$SSH_KEY" ] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY -o IdentitiesOnly=yes"
WR="$(cd "$(dirname "$0")/.." && pwd)"
RT=/tmp/pose_stageC
LOG="$WR/logs/stability_test.log"
sshx() { ssh $SSH_OPTS "$HOST" "$@"; }
scpx() { scp $SSH_OPTS "$@"; }

echo "[C] 推送..."
sshx "mkdir -p $RT/models"
scpx "$WR/build/aarch64/pose_camera_arm64" "$HOST:$RT/"
scpx -r "$WR/package/models/pose_$SIZE" "$HOST:$RT/models/"
sshx "chmod +x $RT/pose_camera_arm64"

echo "[C] 后台启动 ${DURATION}s 连续运行..." | tee "$LOG"
sshx "cd $RT; nohup ./pose_camera_arm64 models/pose_$SIZE/model.ncnn.param models/pose_$SIZE/model.ncnn.bin \
  --size $SIZE --threads $TH --duration-sec $DURATION --stats $RT/stability.csv --jsonl $RT/stream.jsonl --quiet \
  >$RT/run.log 2>&1 & echo PID=\$!"

# 采样循环:每 30s 记录 CPU/RSS/温度 + CPE 业务健康
END=$(( $(date +%s) + DURATION ))
while [ "$(date +%s)" -lt "$END" ]; do
  TS=$(date -Iseconds)
  SAMP=$(sshx "ps w 2>/dev/null | grep '[p]ose_camera' | awk '{print \$1}'; \
    grep VmHWM /proc/\$(pgrep -f pose_camera|head -1)/status 2>/dev/null; \
    for z in /sys/class/thermal/thermal_zone*/temp; do cat \$z 2>/dev/null; done | sort -rn | head -1; \
    ping -c1 -W2 223.5.5.5 >/dev/null 2>&1 && echo NET_OK || echo NET_FAIL")
  echo "[$TS] $SAMP" | tr '\n' ' ' | tee -a "$LOG"; echo | tee -a "$LOG"
  sleep 30
done

echo "[C] 回取..."
scpx "$HOST:$RT/stability.csv" "$WR/results/stability.csv" 2>/dev/null || true
scpx "$HOST:$RT/run.log" "$WR/logs/stability_run.log" 2>/dev/null || true
echo "[C] 完成。检查 $LOG / results/stability.csv"
