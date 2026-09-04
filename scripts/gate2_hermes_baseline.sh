#!/bin/sh
# Gate 2: validate the already-installed Hermes baseline without reinstalling it.
set -u

ADB=${ADB:-adb}
SERIAL=${SERIAL:-}
EXPECTED=${EXPECTED:-HERMES_BASELINE_PASS}
REPORT_DIR=${REPORT_DIR:-"$(pwd)/reports/evidence"}
OUT_DIR=${OUT_DIR:-"$REPORT_DIR/gate2_$(date +%Y%m%d_%H%M%S)"}
mkdir -p "$OUT_DIR"
RAW="$OUT_DIR/raw.log"
VERDICT="$OUT_DIR/verdict.txt"
RESPONSE="$OUT_DIR/response.txt"

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
require_gate_pass gate1

if [ "$(adb_cmd get-state 2>/dev/null | tr -d '\r')" != "device" ]; then
    echo "RESULT=BLOCKED reason=ADB_unavailable" | tee "$VERDICT"
    exit 2
fi

REMOTE_ENV='export HOME=/data/ai_cpe/hermes/home HERMES_HOME=/data/ai_cpe/hermes/.hermes PATH=/data/ai_cpe/hermes/venv/bin:$PATH'
{
    echo "===== ARCH/VERSION ====="
    adb_cmd shell "uname -m; $REMOTE_ENV; hermes --version"
    echo "===== PROCESS/GATEWAY ====="
    adb_cmd shell "$REMOTE_ENV; ps w | grep -i '[h]ermes'; hermes gateway status"
    echo "===== CONFIG SUMMARY (NO SECRET VALUES) ====="
    adb_cmd shell "$REMOTE_ENV; hermes status" | sed -E 's/((token|secret|key)[^:]*:).*/\1 <REDACTED>/Ig'
    echo "===== RESOURCE BEFORE ====="
    adb_cmd shell "grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo"
} 2>&1 | tr -d '\r' | tee "$RAW"

PROMPT="这是RG660MK-EU Hermes基线验收。请只回复：$EXPECTED"
START=$(date +%s)
adb_cmd shell "$REMOTE_ENV; hermes -z '$PROMPT'" >"$RESPONSE.tmp" 2>&1
CLI_RC=$?
tr -d '\r' <"$RESPONSE.tmp" >"$RESPONSE"
rm -f "$RESPONSE.tmp"
END=$(date +%s)
cat "$RESPONSE" | tee -a "$RAW"
echo "cli_rc=$CLI_RC latency_s=$((END - START))" | tee -a "$RAW"

# Hermes 0.19.0 may return rc=0 even when the provider returns HTTP 429.
# The exact sentinel is therefore the authoritative success condition.
ACTUAL=$(sed '/^[[:space:]]*$/d' "$RESPONSE" | tail -1)
if [ "$ACTUAL" = "$EXPECTED" ]; then
    echo "RESULT=PASS expected_reply_received=true" | tee "$VERDICT"
    exit 0
fi
if grep -Eqi 'HTTP 429|额度超限|rate.?limit|quota' "$RESPONSE"; then
    echo "RESULT=BLOCKED reason=model_provider_quota expected_reply_received=false cli_rc=$CLI_RC" | tee "$VERDICT"
    exit 2
fi
echo "RESULT=FAIL reason=unexpected_response expected_reply_received=false cli_rc=$CLI_RC" | tee "$VERDICT"
exit 1
