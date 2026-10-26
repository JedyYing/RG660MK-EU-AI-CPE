#!/bin/bash
# What SoC is this? Dump the kernel's own view.
echo "=== /sys/devices/soc0 ==="
for f in machine family soc_id platform revision vendor; do
  printf '%s: ' "$f"
  cat "/sys/devices/soc0/$f" 2>/dev/null || echo "(none)"
done
echo "=== device-tree model ==="
cat /proc/device-tree/model 2>/dev/null; echo
echo "=== device-tree compatible ==="
strings /proc/device-tree/compatible 2>/dev/null | head -6
echo "=== lscpu ==="
lscpu 2>/dev/null | grep -iE 'model name|architecture|implementer|part|variant|cpu\(s\)|bogomips' | head -8
echo "=== cpuinfo (first block) ==="
head -8 /proc/cpuinfo
echo "=== qnn dsp arch ==="
cat /data/local/tmp/qnn/deepseek-7b/htp-model-config-7b.json 2>/dev/null | head -4
ls /data/local/tmp/qnn/dsp/lib/ | grep -i skel
echo "=== dmesg soc ==="
dmesg 2>/dev/null | grep -iE 'qcom,soc|soc_id|sm8|sm7|qcs|qcm|sc8' | head -5
