#!/bin/bash
# Does the SNPE runtime beat QNN on the same model? (yolov8n W8A8, DSP/HTP)
set -u
export ADSP_LIBRARY_PATH=/data/local/tmp/qnn/dsp/lib
export LD_LIBRARY_PATH=/opt/qnn/sdk/lib:/opt/qnn/sdk/lib/dsp:${LD_LIBRARY_PATH:-}
DLC=/opt/qnn/model/snpe/yolov8n/yolov8n_w8a8_9075.dlc
LIST=/opt/qnn/model/qnn/yolov8n/input_list/input_list_npu.txt
cd /opt/qnn/model/qnn || exit 1

echo "=== snpe-net-run help (runtime/iterations) ==="
snpe-net-run --help 2>&1 | grep -iE "container|input_list|runtime|use_dsp|perf_profile|iterations" | head -10

echo "=== single run (dsp) ==="
T0=$(date +%s.%N)
snpe-net-run --container "$DLC" --input_list "$LIST" --runtime dsp --perf_profile burst > /tmp/snpe1.log 2>&1
RC=$?
T1=$(date +%s.%N)
echo "rc=$RC wall=$(python3 -c "print(f'{$T1-$T0:.2f}')")s"
grep -iE "error|fail" /tmp/snpe1.log | head -3
