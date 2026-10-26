#!/bin/bash
# Push the Hexagon NPU with QNN's own CNN benchmark and measure achieved throughput.
# yolov8n 640x640 W8A8 ≈ 8.7 GFLOP per inference -> inferences/s * 8.7 = GFLOP/s.
set -u
export ADSP_LIBRARY_PATH=/data/local/tmp/qnn/dsp/lib
export LD_LIBRARY_PATH=/opt/qnn/sdk/lib:${LD_LIBRARY_PATH:-}
BE=/data/local/tmp/qnn/dsp/lib/libQnnHtp.so
CTX=/opt/qnn/model/qnn/yolov8n/yolov8n_w8a8_9075.bin
INLIST=/opt/qnn/model/qnn/yolov8n/input_list/input_list_npu.txt
OUT=/tmp/yolo_out
mkdir -p $OUT

run_case () { # $1=label $2=num_inferences $3=batch_multiplier
  local label=$1 n=$2 bm=$3
  local extra=""
  [ "$bm" != "1" ] && extra="--batch_multiplier=$bm"
  rm -rf $OUT/* 2>/dev/null
  local t0=$(date +%s.%N)
  /opt/qnn/sdk/bin/qnn-net-run --backend $BE --retrieve_context $CTX \
      --input_list $INLIST --num_inferences $n --output_dir $OUT \
      --perf_profile burst $extra > /tmp/qnn_run.log 2>&1
  local rc=$?
  local t1=$(date +%s.%N)
  local wall=$(python3 -c "print(f'{$t1-$t0:.2f}')")
  local ips=$(python3 -c "print(f'{$n/($t1-$t0):.2f}')")
  local gflops=$(python3 -c "print(f'{$n*8.7/($t1-$t0)/1000:.2f}')")
  echo "$label: n=$n batch=$bm wall=${wall}s rc=$rc -> ${ips} inf/s, ${gflops} TFLOP/s"
  grep -iE "error|failed|unsupported" /tmp/qnn_run.log | head -2
}

echo "=== single inference (load amortised over 1 run) ==="
run_case "warmup" 2 1
echo "=== steady-state, 1 process ==="
run_case "1proc" 50 1
echo "=== batch_multiplier sweep, 1 process ==="
run_case "1proc-b2" 50 2
run_case "1proc-b4" 50 4
echo "=== 4 concurrent processes, 25 inferences each ==="
t0=$(date +%s.%N)
for i in 1 2 3 4; do
  /opt/qnn/sdk/bin/qnn-net-run --backend $BE --retrieve_context $CTX \
      --input_list $INLIST --num_inferences 25 --output_dir $OUT \
      --perf_profile burst > /tmp/qnn_p$i.log 2>&1 &
done
wait
t1=$(date +%s.%N)
python3 -c "print(f'4proc: 100 inferences in {$t1-$t0:.2f}s -> {100/($t1-$t0):.2f} inf/s, {100*8.7/($t1-$t0)/1000:.2f} TFLOP/s')"
