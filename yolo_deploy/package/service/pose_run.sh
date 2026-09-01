#!/bin/sh
# pose_run.sh — 读取 pose.conf 并以低优先级启动 pose_camera(供 procd 调用或手动运行)
# 位置:与 install.sh 部署的 BASE 目录同级。用法: pose_run.sh [配置文件]
set -e
BASE="$(cd "$(dirname "$0")/.." && pwd)"
CONF="${1:-$BASE/config/pose.conf}"
[ -f "$CONF" ] || { echo "缺配置: $CONF" >&2; exit 1; }
. "$CONF"

BIN="$BASE/bin/pose_camera_arm64"
MODELD="$BASE/${MODEL_DIR#./}"
[ -x "$BIN" ] || { echo "缺可执行: $BIN" >&2; exit 1; }
[ -f "$MODELD/model.ncnn.param" ] || { echo "缺模型: $MODELD" >&2; exit 1; }

mkdir -p "$BASE/logs"
OUT="$BASE/${OUT_JSONL#./}"

# 启动前检查摄像头是否在(lsusb 简单匹配 VID:PID 后四位)
VIDS=$(printf '%s' "$CAM_VID" | sed 's/^0x//')
PIDS=$(printf '%s' "$CAM_PID" | sed 's/^0x//')
if command -v lsusb >/dev/null 2>&1; then
  lsusb 2>/dev/null | grep -qi "$VIDS:$PIDS" || echo "[warn] 未在 lsusb 见到 $VIDS:$PIDS,仍尝试启动" >&2
fi

echo "[pose_run] size=$INPUT_SIZE threads=$THREADS model=$MODELD out=$OUT"
exec nice -n "${NICE:-10}" "$BIN" \
  "$MODELD/model.ncnn.param" "$MODELD/model.ncnn.bin" \
  --size "$INPUT_SIZE" --threads "$THREADS" \
  --vid "$CAM_VID" --pid "$CAM_PID" --fps "$CAM_FPS" \
  --conf "$CONF_THRESH" --kpt "$KPT_THRESH" \
  --smooth "$SMOOTH_WINDOW" \
  --duration-sec 0 --frames 1000000000 \
  --jsonl "$OUT" --quiet
