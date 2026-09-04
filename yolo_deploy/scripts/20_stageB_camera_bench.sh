#!/usr/bin/env bash
# 20_stageB_camera_bench.sh — 阶段B:摄像头性能测试(320/416 × 1..4 线程,各100帧)
# 设备端跑 pose_camera_arm64,采集 P50/P95/FPS/内存/丢帧到 benchmark.csv。
# 用法: SSH_KEY=... HOST=root@192.168.1.1 ./20_stageB_camera_bench.sh
set -eu
HOST="${HOST:-root@192.168.1.1}"
SSH_KEY="${SSH_KEY:-}"
FRAMES="${FRAMES:-100}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
[ -n "$SSH_KEY" ] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY -o IdentitiesOnly=yes"
WR="$(cd "$(dirname "$0")/.." && pwd)"
RT=/tmp/pose_stageB
sshx() { ssh $SSH_OPTS "$HOST" "$@"; }
scpx() { scp $SSH_OPTS "$@"; }

echo "[B] 推送二进制/模型..."
sshx "mkdir -p $RT/models"
scpx "$WR/build/aarch64/pose_camera_arm64" "$HOST:$RT/"
scpx -r "$WR/package/models/pose_320" "$WR/package/models/pose_416" "$HOST:$RT/models/"
sshx "chmod +x $RT/pose_camera_arm64; rm -f $RT/benchmark.csv"

for SIZE in 320 416; do
  for TH in 1 2 3 4; do
    echo "[B] size=$SIZE threads=$TH frames=$FRAMES ..."
    sshx "$RT/pose_camera_arm64 $RT/models/pose_$SIZE/model.ncnn.param $RT/models/pose_$SIZE/model.ncnn.bin \
      --size $SIZE --threads $TH --frames $FRAMES --stats $RT/benchmark.csv --quiet 2>&1 | tail -4"
  done
done

echo "[B] 回取 benchmark.csv"
scpx "$HOST:$RT/benchmark.csv" "$WR/results/benchmark.csv"
echo "== benchmark.csv =="; cat "$WR/results/benchmark.csv"
