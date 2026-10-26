#!/bin/bash
export ADSP_LIBRARY_PATH=/data/local/tmp/qnn/dsp/lib
export LD_LIBRARY_PATH=/opt/qnn/sdk/lib:$LD_LIBRARY_PATH
echo "=== qnn-net-run help (key opts) ==="
/opt/qnn/sdk/bin/qnn-net-run --help 2>&1 | grep -iE '^\s*-|usage|backend|input|output|profile|inference|batch' | head -40
echo "=== input_list contents ==="
ls -la /opt/qnn/model/qnn/yolov8n/input_list/ | head -10
echo "=== snpe-net-run help (iterations) ==="
snpe-net-run --help 2>&1 | grep -iE 'iterations|runtime|input|output|dlc|profile' | head -12
