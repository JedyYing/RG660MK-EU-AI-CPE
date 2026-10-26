#!/bin/bash
# Read-only hardware/software inventory for MT200 (QCS9075). No service changes.
set -u
echo "===== SOC ====="
cat /proc/device-tree/model 2>/dev/null; echo
for f in soc_id machine platform; do printf "%s=" "$f"; cat "/sys/devices/soc0/$f" 2>/dev/null; done
lscpu | grep -E "^(Architecture|CPU\(s\)|Model name|Thread|Core|Socket|BogoMIPS|CPU max|CPU min)" | head -8

echo "===== MEMORY ====="
free -m
grep -E "^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree|CmaTotal|CmaFree|Dirty|Writeback)" /proc/meminfo
echo "--- psi ---"; cat /proc/pressure/memory 2>/dev/null
echo "--- swap devices ---"; swapon --show 2>/dev/null || echo "(none)"

echo "===== CMA / RESERVED ====="
echo "cmdline: $(cat /proc/cmdline)"
dmesg 2>/dev/null | grep -iE "cma|reserved memory|carveout" | head -8

echo "===== DMABUF ====="
ls /dev/dma_heap/ 2>/dev/null || echo "(no /dev/dma_heap)"
if [ -r /sys/kernel/debug/dma_buf/bufinfo ]; then
  echo "bufinfo lines: $(wc -l < /sys/kernel/debug/dma_buf/bufinfo)"
  awk '/^size/{s+=$2; n++} END{printf "buffers=%d total=%.1f MB\n", n, s/1048576}' /sys/kernel/debug/dma_buf/bufinfo
else
  echo "(bufinfo not readable)"
fi

echo "===== DISK ====="
df -h / /data /data/local/tmp 2>/dev/null | sort -u
lsblk 2>/dev/null | head -10

echo "===== OS / KERNEL / BOOT ====="
uname -a
grep -E "^(PRETTY_NAME|VERSION_ID)" /etc/os-release
echo "bootloader: $(cat /proc/cmdline | grep -o 'androidboot[^ ]*' | head -3 | tr '\n' ' ')"
[ -r /proc/device-tree/chosen/bootargs ] && echo "chosen/bootargs present"

echo "===== QNN / AI-RT ====="
ls -1 /opt/qnn/sdk 2>/dev/null | head -8
find /opt/qnn -maxdepth 3 -iname "VERSION*" -o -maxdepth 3 -iname "*.ver" 2>/dev/null | head -5
for v in /opt/qnn/sdk/VERSION /opt/qnn/sdk/version.txt /opt/qnn/VERSION; do [ -f "$v" ] && echo "$v: $(head -1 "$v")"; done
echo "--- bin ---"; ls -1 /opt/qnn/sdk/bin 2>/dev/null | head -20
echo "--- htp libs ---"; ls -1 /opt/qnn/sdk/lib/libQnnHtp*.so 2>/dev/null
echo "--- hexagon arch dirs ---"; ls -1d /opt/qnn/sdk/lib/hexagon-* 2>/dev/null
echo "--- skel ---"; ls -lh /opt/qnn/sdk/lib/hexagon-v73/unsigned/*.so 2>/dev/null | awk '{print $5, $9}' | head -6
echo "--- skel version strings ---"
strings /opt/qnn/sdk/lib/hexagon-v73/unsigned/libQnnHtpV73Skel.so 2>/dev/null | grep -m4 -E "^v?[0-9]+\.[0-9]+\.[0-9]+" 

echo "===== GENIE ====="
which genie-t2t-run genie-app 2>/dev/null
genie-t2t-run --version 2>&1 | head -4
ls -1 /opt/qnn/sdk/lib/libGenie.so* 2>/dev/null

echo "===== CURRENT BUNDLE ====="
ls -lh /data/local/tmp/qnn/deepseek-7b/ 2>/dev/null | head -12
python3 - <<'PY' 2>/dev/null
import json, glob
for p in sorted(glob.glob('/data/local/tmp/qnn/deepseek-7b/*.json')):
    print('---', p.split('/')[-1], '---')
    try:
        d = json.load(open(p))
    except Exception as e:
        print('parse error', e); continue
    s = json.dumps(d, indent=1)
    print(s[:1400])
PY

echo "===== SERVICES (running, untouched) ====="
systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk '{print $1}' | head -14
ps -eo pid,pcpu,pmem,rss,comm --sort=-rss 2>/dev/null | head -8

echo "===== OTHER BACKENDS / CONVERTERS ====="
which qnn-onnx-converter qairt-converter snpe-onnx-to-dlc snpe-dlc-quantize tflite_benchmark 2>/dev/null || true
ls /opt/qnn/sdk/bin 2>/dev/null | grep -iE "convert|quantiz|compile|generator" | head -6
python3 -c "import importlib;print({m: bool(importlib.util.find_spec(m)) for m in ['onnx','onnxruntime','torch','tflite_runtime','numpy']})" 2>/dev/null
ls /usr/lib/aarch64-linux-gnu/ 2>/dev/null | grep -E "onnxruntime|libtflite|libtorch" | head -4
echo "===== DONE ====="
