#!/bin/bash
echo "=== QNN SDK bins ==="
ls /opt/qnn/sdk/bin | sort | head -40
echo "=== throughput/net-run present? ==="
ls /opt/qnn/sdk/bin | grep -iE 'throughput|net-run' || echo "(none in sdk/bin)"
ls /data/sdk/bin | sort
echo "=== yolov8n assets ==="
ls -la /opt/qnn/model/qnn/yolov8n/ 2>/dev/null
echo "--- input_list ---"
cat /opt/qnn/model/qnn/yolov8n/input_list 2>/dev/null
echo "=== qnn libs for x86 host? ==="
ls /opt/qnn/sdk/lib 2>/dev/null | head -5
