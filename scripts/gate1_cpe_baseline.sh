#!/bin/sh
# Gate 1: finite CPE stability test. Run only after Gate 0 PASS.
set -u

ADB=${ADB:-adb}
SERIAL=${SERIAL:-}
DURATION_S=${DURATION_S:-1800}
INTERVAL_S=${INTERVAL_S:-30}
TARGET=${TARGET:-223.5.5.5}
REPORT_DIR=${REPORT_DIR:-"$(pwd)/reports/evidence"}
OUT_DIR=${OUT_DIR:-"$REPORT_DIR/gate1_$(date +%Y%m%d_%H%M%S)"}
mkdir -p "$OUT_DIR"
RAW="$OUT_DIR/raw.log"
VERDICT="$OUT_DIR/verdict.txt"

latest_gate_verdict() {
    prefix=$1
    latest=
    for candidate in "$REPORT_DIR"/${prefix}_*/verdict.txt; do
        [ -r "$candidate" ] || continue
        latest=$candidate
    done
    printf '%s' "$latest"
}

require_gate_pass() {
    prefix=$1
    prerequisite=$(latest_gate_verdict "$prefix")
    if [ -z "$prerequisite" ] || ! grep -q '^RESULT=PASS\([[:space:]]\|$\)' "$prerequisite"; then
        echo "RESULT=BLOCKED reason=${prefix}_not_pass prerequisite=${prerequisite:-missing}" | tee "$VERDICT"
        exit 2
    fi
}

adb_cmd() {
    if [ -n "$SERIAL" ]; then "$ADB" -s "$SERIAL" "$@"; else "$ADB" "$@"; fi
}

require_gate_pass gate0

case "$DURATION_S:$INTERVAL_S" in
    *[!0-9:]*|0:*|*:0) echo "Invalid DURATION_S/INTERVAL_S" >&2; exit 2 ;;
esac

if [ "$(adb_cmd get-state 2>/dev/null | tr -d '\r')" != "device" ]; then
    echo "RESULT=BLOCKED: ADB unavailable" | tee "$VERDICT"
    exit 2
fi

START=$(date +%s)
END=$((START + DURATION_S))
FAILS=0
SAMPLES=0
{
    echo "===== GATE1 START $(date -Iseconds) ====="
    adb_cmd shell 'echo "--- baseline"; ip -br addr 2>/dev/null || ip addr; ip route; ip rule; df -hT 2>/dev/null || df -h; grep -E "MemTotal|MemAvailable|SwapTotal" /proc/meminfo; ps w; dmesg | tail -100' | tr -d '\r'
    while [ "$(date +%s)" -lt "$END" ]; do
        SAMPLES=$((SAMPLES + 1))
        echo "--- sample=$SAMPLES time=$(date -Iseconds)"
        if ! adb_cmd shell "ip route | grep -q '^default ' && ping -c 1 -W 3 '$TARGET' >/dev/null"; then
            FAILS=$((FAILS + 1))
            echo "connectivity=FAIL"
        else
            echo "connectivity=PASS"
        fi
        adb_cmd shell 'grep -E "MemAvailable|SwapFree" /proc/meminfo; for f in /sys/class/thermal/thermal_zone*/temp; do [ -r "$f" ] && echo "$f=$(cat "$f")"; done' | tr -d '\r'
        NOW=$(date +%s)
        [ "$NOW" -ge "$END" ] && break
        REMAIN=$((END - NOW))
        [ "$REMAIN" -lt "$INTERVAL_S" ] && sleep "$REMAIN" || sleep "$INTERVAL_S"
    done
    echo "--- final kernel errors"
    adb_cmd shell 'dmesg | grep -Ei "oom|out of memory|watchdog|reset|panic|usb.*(disconnect|error)|xhci.*error" | tail -200 || true' | tr -d '\r'
    echo "===== GATE1 END $(date -Iseconds) samples=$SAMPLES failures=$FAILS ====="
} >"$RAW" 2>&1
cat "$RAW"

if [ "$SAMPLES" -gt 0 ] && [ "$FAILS" -eq 0 ]; then
    echo "RESULT=PASS samples=$SAMPLES failures=0 duration_s=$DURATION_S" | tee "$VERDICT"
    exit 0
fi
echo "RESULT=FAIL samples=$SAMPLES failures=$FAILS duration_s=$DURATION_S" | tee "$VERDICT"
exit 1
