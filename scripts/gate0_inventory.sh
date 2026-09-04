#!/bin/sh
# Gate 0: read-only RG660MK-EU USB Host/SuperSpeed inventory from an Ubuntu host.
set -u

ADB=${ADB:-adb}
SERIAL=${SERIAL:-}
OUT_DIR=${OUT_DIR:-"$(pwd)/reports/evidence/gate0_$(date +%Y%m%d_%H%M%S)"}
mkdir -p "$OUT_DIR"
RAW="$OUT_DIR/raw.log"
VERDICT="$OUT_DIR/verdict.txt"

adb_cmd() {
    if [ -n "$SERIAL" ]; then
        "$ADB" -s "$SERIAL" "$@"
    else
        "$ADB" "$@"
    fi
}

if ! command -v "$ADB" >/dev/null 2>&1; then
    echo "BLOCKED: adb not found: $ADB" | tee "$VERDICT"
    exit 2
fi
if [ "$(adb_cmd get-state 2>/dev/null | tr -d '\r')" != "device" ]; then
    echo "BLOCKED: RG660MK-EU is not reachable over ADB" | tee "$VERDICT"
    exit 2
fi

REMOTE_PROBE='echo "===== SYSTEM ====="
id
uname -a
uname -m
cat /etc/os-release 2>/dev/null || true
getenforce 2>/dev/null || true

echo "===== USB DEVICE TREE ====="
for d in /sys/firmware/devicetree/base/*usb* /proc/device-tree/*usb*; do
    [ -d "$d" ] || continue
    echo "--- $d"
    for p in status dr_mode maximum-speed compatible; do
        if [ -r "$d/$p" ]; then
            printf "%s=" "$p"
            tr "\000" "\n" < "$d/$p"
        fi
    done
done

echo "===== USB ROLE ====="
for f in /sys/class/usb_role/*/role /sys/devices/platform/*/usb_role/*/role; do
    [ -r "$f" ] || continue
    echo "$f=$(cat "$f")"
done

echo "===== USB HOST BUSES ====="
for f in /sys/bus/usb/devices/usb*/speed; do
    [ -r "$f" ] || continue
    echo "$f=$(cat "$f")"
done

echo "===== ENUMERATED USB DEVICES ====="
for d in /sys/bus/usb/devices/*; do
    [ -r "$d/idVendor" ] || continue
    vid=$(cat "$d/idVendor")
    pid=$(cat "$d/idProduct")
    speed=$(cat "$d/speed" 2>/dev/null || echo unknown)
    product=$(cat "$d/product" 2>/dev/null || echo unknown)
    echo "$(basename "$d") vid:pid=$vid:$pid speed=$speed product=$product"
done

echo "===== UDC/GADGET ====="
for d in /sys/class/udc/*; do
    [ -e "$d" ] || continue
    echo "--- $d"
    for p in state current_speed maximum_speed; do
        [ -r "$d/$p" ] && echo "$p=$(cat "$d/$p")"
    done
done

echo "===== CONTROLLERS ====="
find /sys/bus/platform/drivers -maxdepth 2 -type l 2>/dev/null | grep -Ei "dwc3|xhci|ehci|ohci|mtu3|usb" || true

echo "===== KERNEL LOG ====="
dmesg 2>&1 | grep -Ei "usb|xhci|mtu3|type.?c|role" | tail -200 || true

echo "===== NETWORK/STORAGE/RESOURCE ====="
ip -br addr 2>/dev/null || ip addr
ip route
ip rule
df -hT 2>/dev/null || df -h
cat /proc/meminfo | head -20
cat /proc/cpuinfo | head -80
'

adb_cmd shell "$REMOTE_PROBE" 2>&1 | tr -d '\r' | tee "$RAW"

DT_MODE=$(adb_cmd shell "tr '\000' '\n' < /proc/device-tree/usb@11591000/dr_mode 2>/dev/null" | tr -d '\r')
DT_MAX=$(adb_cmd shell "tr '\000' '\n' < /proc/device-tree/usb@11591000/maximum-speed 2>/dev/null" | tr -d '\r')
HOST_SPEEDS=$(adb_cmd shell 'for f in /sys/bus/usb/devices/usb*/speed; do [ -r "$f" ] && cat "$f"; done' | tr -d '\r')
ROLE=$(adb_cmd shell 'for f in /sys/class/usb_role/*/role /sys/devices/platform/*/usb_role/*/role; do [ -r "$f" ] && cat "$f"; done' | tr -d '\r')

CAPABLE=0
ACTIVE_HOST=0
SUPER=0
[ "$DT_MODE" = "host" ] || [ "$DT_MODE" = "otg" ] && CAPABLE=1
case "$DT_MAX" in super-speed|super-speed-plus) CAPABLE=1 ;; esac
[ "$ROLE" = "host" ] && ACTIVE_HOST=1
if printf '%s\n' "$HOST_SPEEDS" | grep -Eq '^(5000|10000|20000)$'; then
    ACTIVE_HOST=1
    SUPER=1
fi

{
    echo "device_tree_dr_mode=${DT_MODE:-unknown}"
    echo "device_tree_maximum_speed=${DT_MAX:-unknown}"
    echo "runtime_role=${ROLE:-unavailable}"
    echo "host_bus_speeds=${HOST_SPEEDS:-none}"
    if [ "$CAPABLE" -eq 1 ] && [ "$ACTIVE_HOST" -eq 1 ] && [ "$SUPER" -eq 1 ]; then
        echo "RESULT=PASS"
        echo "DECISION=Gate 1 may proceed; attach accelerator and record its VID:PID and speed."
    elif [ "$CAPABLE" -eq 1 ]; then
        echo "RESULT=BLOCKED"
        echo "DECISION=Controller capability is declared, but active Host plus >=5000M is not proven. Do not switch role blindly. Establish RJ45/serial management and confirm carrier-board routing first."
    else
        echo "RESULT=FAIL"
        echo "DECISION=No Host/SuperSpeed capability evidence. Use an external AI host fallback."
    fi
} | tee "$VERDICT"

grep -q '^RESULT=PASS$' "$VERDICT" && exit 0
grep -q '^RESULT=BLOCKED$' "$VERDICT" && exit 2
exit 1
