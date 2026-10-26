#!/bin/bash
# Concurrency sweep on the Hexagon NPU with qnn-net-run (yolov8n 640 W8A8, 8.7 GFLOP/inf).
set -u
export ADSP_LIBRARY_PATH=/data/local/tmp/qnn/dsp/lib
export LD_LIBRARY_PATH=/opt/qnn/sdk/lib:${LD_LIBRARY_PATH:-}
BE=/opt/qnn/sdk/lib/libQnnHtp.so
CTX=/opt/qnn/model/qnn/yolov8n/yolov8n_w8a8_9075.bin
INLIST=yolov8n/input_list/input_list_npu.txt
OUT=/tmp/yolo_out
cd /opt/qnn/model/qnn || exit 1
mkdir -p "$OUT"

for P in 1 2 4 8 12 16; do
  PER=20
  T0=$(date +%s.%N)
  for i in $(seq 1 "$P"); do
    /opt/qnn/sdk/bin/qnn-net-run --backend "$BE" --retrieve_context "$CTX" \
        --input_list "$INLIST" --num_inferences "$PER" --output_dir "$OUT" \
        --perf_profile burst > "/tmp/qnn_c${P}_$i.log" 2>&1 &
  done
  wait
  T1=$(date +%s.%N)
  TOTAL=$((P * PER))
  python3 -c "
p=$P; total=$TOTAL; wall=$T1-$T0
if wall < 0.5:
    print('procs=%-3d FAILED (wall %.2fs too short)' % (p, wall))
else:
    print('procs=%-3d total=%-4d wall=%6.2fs -> %7.2f inf/s, %6.2f TFLOP/s' % (p, total, wall, total/wall, total*8.7/wall/1000))
"
  NERR=$(grep -l "ERROR" /tmp/qnn_c${P}_*.log 2>/dev/null | wc -l)
  [ "$NERR" -gt 0 ] && echo "   warnings: $NERR process logs contain ERROR"
  uptime | sed 's/^/   /'
done
