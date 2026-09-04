#!/bin/sh
# Host-side, Gate-protected deployment of the vision service interface.
# This does not install HailoRT, models, drivers, or change USB/network state.
set -eu

ADB=${ADB:-adb}
SERIAL=${SERIAL:-}
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REPORT_DIR=${REPORT_DIR:-$PROJECT_DIR/reports/evidence}
REMOTE_ROOT=${REMOTE_ROOT:-/data/ai_cpe/demo}

adb_cmd() {
    if [ -n "$SERIAL" ]; then "$ADB" -s "$SERIAL" "$@"; else "$ADB" "$@"; fi
}

require_gate_pass() {
    prefix=$1
    latest=
    for verdict in "$REPORT_DIR"/${prefix}_*/verdict.txt; do
        [ -r "$verdict" ] || continue
        latest=$verdict
    done
    if [ -z "$latest" ]; then
        echo "BLOCKED: no $prefix verdict exists under $REPORT_DIR" >&2
        return 1
    fi
    if grep -q '^RESULT=PASS\([[:space:]]\|$\)' "$latest"; then
        return 0
    fi
    echo "BLOCKED: latest $prefix verdict is not PASS: $latest" >&2
    return 1
}

command -v "$ADB" >/dev/null 2>&1 || { echo "BLOCKED: adb not found: $ADB" >&2; exit 2; }
[ "$(adb_cmd get-state 2>/dev/null | tr -d '\r')" = "device" ] || { echo "BLOCKED: RG660MK-EU is not reachable over ADB" >&2; exit 2; }
require_gate_pass gate0 || exit 2
require_gate_pass gate1 || exit 2
require_gate_pass gate2 || exit 2

adb_cmd shell "mkdir -p '$REMOTE_ROOT/services' '$REMOTE_ROOT/config' '$REMOTE_ROOT/ai_models' '$REMOTE_ROOT/ai_runtime' '$REMOTE_ROOT/media' '$REMOTE_ROOT/data' '$REMOTE_ROOT/logs' '$REMOTE_ROOT/run' '$REMOTE_ROOT/rollback'"
adb_cmd push "$PROJECT_DIR/services/ai_service.py" "$REMOTE_ROOT/services/ai_service.py"
adb_cmd push "$PROJECT_DIR/services/face_gallery.py" "$REMOTE_ROOT/services/face_gallery.py"
adb_cmd push "$PROJECT_DIR/services/hermes_ai_tool.py" "$REMOTE_ROOT/services/hermes_ai_tool.py"
adb_cmd push "$PROJECT_DIR/scripts/start_ai.sh" "$REMOTE_ROOT/services/start_ai.sh"
adb_cmd push "$PROJECT_DIR/scripts/stop_ai.sh" "$REMOTE_ROOT/rollback/stop_ai.sh"

if adb_cmd shell "test -r '$REMOTE_ROOT/config/ai-service.json'"; then
    echo "Preserving existing $REMOTE_ROOT/config/ai-service.json"
else
    adb_cmd push "$PROJECT_DIR/config/ai-service.example.json" "$REMOTE_ROOT/config/ai-service.json"
fi
adb_cmd shell "chmod 0755 '$REMOTE_ROOT/services/ai_service.py' '$REMOTE_ROOT/services/face_gallery.py' '$REMOTE_ROOT/services/hermes_ai_tool.py' '$REMOTE_ROOT/services/start_ai.sh' '$REMOTE_ROOT/rollback/stop_ai.sh'; chmod 0700 '$REMOTE_ROOT/data'"

echo "Interface deployed to $REMOTE_ROOT. Runtime remains disabled until Gate 3 and model conversion pass."
echo "Do not start inference until runner.command, HEF artifacts, and runtime.enabled are configured."
