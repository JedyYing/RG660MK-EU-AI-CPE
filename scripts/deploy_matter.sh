#!/usr/bin/env bash
# Deploy the validated Matter controller package to RG660MK-EU over ADB.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
ADB=${ADB:-adb}
SERIAL=${SERIAL:-}
ARTIFACT=${ARTIFACT:-"$PROJECT_DIR/artifacts/matter-v1.6.0.0-rg660/chip-tool"}
REMOTE_ROOT=/data/ai_cpe/demo/matter
PYTHON=/data/ai_cpe/hermes/venv/bin/python
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

adb_cmd() {
    if [ -n "$SERIAL" ]; then "$ADB" -s "$SERIAL" "$@"; else "$ADB" "$@"; fi
}

if [ "$(adb_cmd get-state 2>/dev/null | tr -d '\r')" != "device" ]; then
    echo "ERROR: RG660MK-EU is not reachable over ADB" >&2
    exit 2
fi
if [ ! -x "$ARTIFACT" ]; then
    echo "ERROR: Matter artifact is missing or not executable: $ARTIFACT" >&2
    echo "Run scripts/build_matter_chip_tool.sh first." >&2
    exit 2
fi
if ! file "$ARTIFACT" | grep -q 'ELF 64-bit.*ARM aarch64'; then
    echo "ERROR: artifact is not an aarch64 ELF binary" >&2
    exit 2
fi
if readelf -l "$ARTIFACT" | grep -q 'Requesting program interpreter'; then
    echo "ERROR: artifact is dynamically linked; refusing to deploy across the RG660 musl ABI" >&2
    exit 2
fi

OUT_DIR="$PROJECT_DIR/reports/evidence/matter_deploy_$TIMESTAMP"
mkdir -p "$OUT_DIR"
"$SCRIPT_DIR/matter_preflight.sh" | tee "$OUT_DIR/preflight.log"

adb_cmd shell "mkdir -p '$REMOTE_ROOT/bin' '$REMOTE_ROOT/config' '$REMOTE_ROOT/credentials' '$REMOTE_ROOT/services' '$REMOTE_ROOT/logs' && chmod 0700 '$REMOTE_ROOT/credentials'"

if adb_cmd shell "test -e '$REMOTE_ROOT/bin/chip-tool'"; then
    adb_cmd shell "cp '$REMOTE_ROOT/bin/chip-tool' '$REMOTE_ROOT/bin/chip-tool.backup.$TIMESTAMP'"
fi
if adb_cmd shell "test -e '$REMOTE_ROOT/config/controller.json'"; then
    adb_cmd shell "cp '$REMOTE_ROOT/config/controller.json' '$REMOTE_ROOT/config/controller.json.backup.$TIMESTAMP'"
fi
SKILL_DIR=/data/ai_cpe/hermes/.hermes/skills/embedded/rg660mk-matter
if adb_cmd shell "test -e '$SKILL_DIR/SKILL.md'"; then
    adb_cmd shell "cp '$SKILL_DIR/SKILL.md' '$SKILL_DIR/SKILL.md.backup.$TIMESTAMP'"
fi
adb_cmd shell "mkdir -p '$SKILL_DIR'"

adb_cmd push "$ARTIFACT" "$REMOTE_ROOT/bin/chip-tool" >/dev/null
adb_cmd push "$PROJECT_DIR/config/matter-controller.example.json" "$REMOTE_ROOT/config/controller.json" >/dev/null
adb_cmd push "$PROJECT_DIR/services/hermes_matter_tool.py" "$REMOTE_ROOT/services/hermes_matter_tool.py" >/dev/null
adb_cmd push "$PROJECT_DIR/hermes-skill/rg660mk-matter/SKILL.md" "$SKILL_DIR/SKILL.md" >/dev/null
adb_cmd shell "chmod 0755 '$REMOTE_ROOT/bin/chip-tool' '$REMOTE_ROOT/services/hermes_matter_tool.py'; chmod 0600 '$REMOTE_ROOT/config/controller.json'; chmod 0700 '$REMOTE_ROOT/credentials'; chmod 0644 '$SKILL_DIR/SKILL.md'"

adb_cmd shell "$PYTHON '$REMOTE_ROOT/services/hermes_matter_tool.py' status" 2>&1 | tr -d '\r' | tee "$OUT_DIR/status.json"
STATUS_RC=${PIPESTATUS[0]}
if [ "$STATUS_RC" -ne 0 ] || ! grep -q '"runtime_compatible":true' "$OUT_DIR/status.json"; then
    echo "RESULT=FAIL reason=device_runtime_probe_failed" | tee "$OUT_DIR/verdict.txt"
    exit 1
fi

REMOTE_SHA=$(adb_cmd shell "sha256sum '$REMOTE_ROOT/bin/chip-tool'" | tr -d '\r' | awk '{print $1}')
LOCAL_SHA=$(sha256sum "$ARTIFACT" | awk '{print $1}')
if [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
    echo "RESULT=FAIL reason=sha256_mismatch" | tee "$OUT_DIR/verdict.txt"
    exit 1
fi
{
    echo "RESULT=PASS"
    echo "binary=$REMOTE_ROOT/bin/chip-tool"
    echo "sha256=$LOCAL_SHA"
    echo "scope=on-network_Matter_over_WiFi_or_Ethernet"
    echo "ble_commissioning=unavailable"
    echo "thread_border_router=unavailable"
} | tee "$OUT_DIR/verdict.txt"
