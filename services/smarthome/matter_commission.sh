#!/bin/sh
# matter_commission.sh —— 在 RG660MK 上对「已在局域网内、且已打开配网窗口」的 Matter 设备做 on-network 配网
# 用法:  sh matter_commission.sh <node_id> <setup_pin>      # 例: sh matter_commission.sh 2 12345678901
#        sh matter_commission.sh --check                    # 只检查工具链与网络前置
# 注意: setup PIN 属敏感信息——本脚本不回显、不落盘，仅作为命令参数传给 chip-tool。
set -eu
CHIP="${CHIP_TOOL:-/data/ai_cpe/matter/bin/chip-tool}"
KVS="${CHIP_KVS:-/data/ai_cpe/matter/kvs/chip_kvs}"
CFG=/data/ai_cpe/services/smarthome/config.json

if [ "${1:-}" = "--check" ]; then
    echo "[1] chip-tool: $([ -x "$CHIP" ] && echo 存在 || echo 缺失)"
    echo "[2] br-lan IPv6: $(ip -6 addr show br-lan | grep -c inet6) 条"
    echo "[3] mDNS 5353: $(awk '$2 ~ /:14E9$/ {c++} END {print c+0}' /proc/net/udp6) 条监听"
    echo "[4] 已有配网存储: $([ -s /data/ai_cpe/matter/kvs/chip_tool_config.ini ] && echo "存在($(wc -c < /data/ai_cpe/matter/kvs/chip_tool_config.ini) B)" || echo 无)"
    echo "[5] PAA 信任库: $(ls /data/ai_cpe/matter/paa/*.der 2>/dev/null | wc -l) 个证书"
    exit 0
fi

NODE="${1:?用法: matter_commission.sh <node_id> <setup_pin> | --check}"
PIN="${2:?缺少 setup_pin}"

echo "== on-network 配网 node_id=$NODE （PIN 不回显） =="
mkdir -p "$(dirname "$KVS")"
mkdir -p "$(dirname "$KVS")"
"$CHIP" pairing code "$NODE" "$PIN" --paa-trust-store-path /data/ai_cpe/matter/paa   # PAA 信任库目录（Tuya 等厂商根证书；缺失会 Device Attestation 失败）
KDIR="$(dirname "$KVS")"; OK=0
for n in chip_tool_config.ini chip_tool_config.alpha.ini chip_config.ini chip_factory.ini chip_counters.ini; do
    if [ -f "/tmp/$n" ]; then cp -f "/tmp/$n" "$KDIR/$n"; OK=1; fi
done
if [ "$OK" = 1 ]; then echo "已持久化 Matter 存储 -> $KDIR（重启不丢）"; else echo "配网失败：请确认 ① 灯泡已通电且在 2.4G SSID 上 ② 已打开配网窗口(commissioning window) ③ PIN 正确 ④ PAA 证书在 /data/ai_cpe/matter/paa"; exit 1; fi

echo "== 配网成功，写入 config.json（driver=matter, node_id=$NODE） =="
/usr/bin/python3 - <<PY
import json
p = "$CFG"
cfg = json.load(open(p, encoding="utf-8"))
cfg["driver"] = "matter"
cfg.setdefault("matter", {})["node_id"] = int("$NODE")
cfg["matter"].setdefault("endpoint", 1)
json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("已更新:", p)
PY
/etc/init.d/smarthome restart >/dev/null 2>&1 || true
echo "== 验证 =="
/usr/bin/python3 /data/ai_cpe/smarthome.py status
