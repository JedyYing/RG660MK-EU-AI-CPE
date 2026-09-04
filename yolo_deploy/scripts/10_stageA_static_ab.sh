#!/usr/bin/env bash
# 10_stageA_static_ab.sh — 阶段A:设备端静态图片推理 vs PC 基线 A/B 比对
# 先推送二进制/模型/场景图到设备 /tmp,设备端跑 pose_image_arm64,回取 JSONL,与 PC 基线逐图比对。
# 用法: SSH_KEY=... HOST=root@192.168.1.1 ./10_stageA_static_ab.sh
set -eu
HOST="${HOST:-root@192.168.1.1}"
SSH_KEY="${SSH_KEY:-}"
SIZE="${SIZE:-320}"
THREADS="${THREADS:-2}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
[ -n "$SSH_KEY" ] && SSH_OPTS="$SSH_OPTS -i $SSH_KEY -o IdentitiesOnly=yes"
WR="$(cd "$(dirname "$0")/.." && pwd)"
RT=/tmp/pose_stageA
sshx() { ssh $SSH_OPTS "$HOST" "$@"; }
scpx() { scp $SSH_OPTS "$@"; }

echo "[A] 推送到设备 $RT ..."
sshx "mkdir -p $RT/scenes $RT/models"
scpx "$WR/build/aarch64/pose_image_arm64" "$HOST:$RT/"
scpx -r "$WR/package/models/pose_$SIZE" "$HOST:$RT/models/"
scpx "$WR/results/baseline_pc/scenes/"*.jpg "$HOST:$RT/scenes/"

echo "[A] 设备端推理(size=$SIZE threads=$THREADS)..."
sshx "chmod +x $RT/pose_image_arm64; rm -f $RT/device.jsonl; \
  for img in $RT/scenes/*.jpg; do \
    $RT/pose_image_arm64 $RT/models/pose_$SIZE/model.ncnn.param $RT/models/pose_$SIZE/model.ncnn.bin \
      \$img --size $SIZE --threads $THREADS --json $RT/device.jsonl >/dev/null 2>&1; \
  done; echo DONE"

echo "[A] 回取结果..."
scpx "$HOST:$RT/device.jsonl" "$WR/results/device/device_stageA_$SIZE.jsonl"

echo "[A] 比对..."
python3 "$WR/scripts/compare_baseline.py" \
  "$WR/results/baseline_pc/pc_baseline_$SIZE.jsonl" \
  "$WR/results/device/device_stageA_$SIZE.jsonl" \
  | tee "$WR/results/device/stageA_compare_$SIZE.txt"
