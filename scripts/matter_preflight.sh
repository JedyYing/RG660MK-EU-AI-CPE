#!/bin/sh
# Read-only Matter Controller preflight for RG660MK-EU over ADB.
set -u

ADB=${ADB:-adb}
SERIAL=${SERIAL:-}
OUT_DIR=${OUT_DIR:-"$(pwd)/reports/evidence/matter_preflight_$(date +%Y%m%d_%H%M%S)"}
mkdir -p "$OUT_DIR"
RAW="$OUT_DIR/raw.log"
VERDICT="$OUT_DIR/verdict.txt"

adb_cmd() {
    if [ -n "$SERIAL" ]; then "$ADB" -s "$SERIAL" "$@"; else "$ADB" "$@"; fi
}

if ! command -v "$ADB" >/dev/null 2>&1; then
    echo "RESULT=BLOCKED reason=adb_not_found" | tee "$VERDICT"
    exit 2
fi
if [ "$(adb_cmd get-state 2>/dev/null | tr -d '\r')" != "device" ]; then
    echo "RESULT=BLOCKED reason=adb_unavailable" | tee "$VERDICT"
    exit 2
fi

REMOTE_PROBE='echo "===== PLATFORM ====="
uname -m
cat /etc/openwrt_release 2>/dev/null || true
/lib/ld-musl-aarch64.so.1 2>&1 | head -1 || true

echo "===== MATTER NETWORK ====="
ip -br link show br-lan 2>/dev/null || true
ip -br -6 addr show dev br-lan 2>/dev/null || true
ip -6 route show dev br-lan 2>/dev/null || true
ip maddr show dev br-lan 2>/dev/null || true

echo "===== REQUIRED RUNTIME ====="
command -v dbus-daemon 2>/dev/null || true
/etc/init.d/dbus status 2>/dev/null || true
/data/ai_cpe/hermes/venv/bin/python -V 2>&1 || true

echo "===== OPTIONAL COMMISSIONING RADIOS ====="
[ -d /sys/class/bluetooth ] && ls /sys/class/bluetooth || echo bluetooth=absent
zcat /proc/config.gz 2>/dev/null | grep -E "CONFIG_(BT|IEEE802154|6LOWPAN)=" || true
for d in /sys/bus/usb/devices/*; do [ -r "$d/idVendor" ] && echo "usb=$(cat "$d/idVendor"):$(cat "$d/idProduct") speed=$(cat "$d/speed" 2>/dev/null)"; done

echo "===== RESOURCE ====="
df -Pk /data
grep -E "MemTotal|MemAvailable|SwapTotal" /proc/meminfo

echo "===== PORT 5353 ====="
cat /proc/net/udp /proc/net/udp6 2>/dev/null | grep -i ":14E9 " || echo udp_5353=available
'

adb_cmd shell "$REMOTE_PROBE" 2>&1 | tr -d '\r' | tee "$RAW"

ARCH=$(adb_cmd shell 'uname -m' 2>/dev/null | tr -d '\r')
LAN_STATE=$(adb_cmd shell 'cat /sys/class/net/br-lan/operstate 2>/dev/null' | tr -d '\r')
LAN_IPV6=$(adb_cmd shell 'ip -6 addr show dev br-lan scope link 2>/dev/null' | tr -d '\r')
PYTHON=$(adb_cmd shell '/data/ai_cpe/hermes/venv/bin/python -V 2>&1' | tr -d '\r')
DATA_KB=$(adb_cmd shell "df -Pk /data | awk 'NR==2 {print \$4}'" | tr -d '\r')

RESULT=PASS
REASONS=""
[ "$ARCH" = "aarch64" ] || { RESULT=FAIL; REASONS="${REASONS} arch"; }
[ "$LAN_STATE" = "up" ] || { RESULT=FAIL; REASONS="${REASONS} br_lan_down"; }
printf '%s\n' "$LAN_IPV6" | grep -q 'inet6 fe80:' || { RESULT=FAIL; REASONS="${REASONS} no_ipv6_link_local"; }
printf '%s\n' "$PYTHON" | grep -q '^Python 3\.' || { RESULT=FAIL; REASONS="${REASONS} python_missing"; }
case "$DATA_KB" in ''|*[!0-9]*) RESULT=FAIL; REASONS="${REASONS} data_space_unknown" ;; *) [ "$DATA_KB" -ge 102400 ] || { RESULT=FAIL; REASONS="${REASONS} data_space_low"; } ;; esac

{
    echo "RESULT=$RESULT"
    echo "reasons=${REASONS:-none}"
    echo "controller_scope=on-network_Matter_over_WiFi_or_Ethernet"
    echo "ble_commissioning=unavailable"
    echo "thread_border_router=unavailable"
} | tee "$VERDICT"

[ "$RESULT" = "PASS" ]
