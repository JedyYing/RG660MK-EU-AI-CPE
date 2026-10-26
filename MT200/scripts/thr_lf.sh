#!/bin/bash
# Push the Hexagon NPU with QNN's CNN benchmark and measure the achieved throughput.
# yolov8n 640x640 W8A8 ≈ 8.7 GFLOP per inference, so inf/s * 8.7 = GFLOP/s.
set -u
export ADSP_LIBRARY_PATH=/data/local/tmp/qnn/dsp/lib
export LD_LIBRARY_PATH=/opt/qnn/sdk/lib:${LD_LIBRARY_PATH:-}
BE=/opt/qnn/sdk/lib/libQnnHtp.so
CTX=/opt/qnn/model/qnn/yolov8n/yolov8n_w8a8_9075.bin
INLIST=/opt/qnn/model/qnn/yolov8n/input_list/input_list_npu.txt
OUT=/tmp/yolo_out
cd /opt/qnn/model/qnn || exit 1   # input_list entries are relative to this root
mkdir -p "$OUT"

run_case () { # $1=label $2=num_inferences $3=batch_multiplier
  local label=$1 n=$2 bm=$3 extra=""
  [ "$bm" != "1" ] && extra="--batch_multiplier=$bm"
  rm -rf "$OUT"/* 2>/dev/null
  local t0 t1 rc
  t0=$(date +%s.%N)
  /opt/qnn/sdk/bin/qnn-net-run --backend "$BE" --retrieve_context "$CTX" \
      --input_list "$INLIST" --num_inferences "$n" --output_dir "$OUT" \
      --perf_profile burst $extra > /tmp/qnn_run.log 2>&1
  rc=$?
  t1=$(date +%s.%N)
  if [ $rc -ne 0 ]; then
    echo "$label: FAILED rc=$rc"
    grep -iE "error|failed|unsupported" /tmp/qnn_run.log | head -2
    return
  fi
  python3 - "$label" "$n" "$bm" "$t0" "$t1" <<'PY'
import sys
label, n, bm, t0, t1 = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
wall = t1 - t0
if wall <= 0:
    print("%-12s n=%-4d batch=%s wall=%ss (invalid)" % (label, n, bm, wall)); raise SystemExit
print("%-12s n=%-4d batch=%s wall=%6.2fs -> %8.2f inf/s, %7.2f TFLOP/s"
      % (label, n, bm, wall, n / wall, n * 8.7 / wall / 1000))
PY
}

echo "=== validate + warmup ==="
run_case "warmup" 4 1
echo "=== single process, batch sweep ==="
run_case "1proc-b1" 60 1
run_case "1proc-b2" 60 2
run_case "1proc-b4" 60 4
echo "=== 4 concurrent processes x 25 inferences ==="
t0=$(date +%s.%N)
for i in 1 2 3 4; do
  /opt/qnn/sdk/bin/qnn-net-run --backend "$BE" --retrieve_context "$CTX" \
      --input_list "$INLIST" --num_inferences 25 --output_dir "$OUT" \
      --perf_profile burst > "/tmp/qnn_p$i.log" 2>&1 &
done
wait
t1=$(date +%s.%N)
python3 -c "print('4proc        n=100            wall=%6.2fs -> %8.2f inf/s, %7.2f TFLOP/s' % ($t1-$t0, 100/($t1-$t0), 100*8.7/($t1-$t0)/1000))"
