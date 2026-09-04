#!/bin/sh
# Start only the loopback AI service. This script never changes USB or network state.
set -eu

DEMO_ROOT=${DEMO_ROOT:-/data/ai_cpe/demo}
PYTHON_BIN=${PYTHON_BIN:-/data/ai_cpe/hermes/venv/bin/python}
CONFIG=${AI_SERVICE_CONFIG:-$DEMO_ROOT/config/ai-service.json}
SERVICE=$DEMO_ROOT/services/ai_service.py
CLIENT=$DEMO_ROOT/services/hermes_ai_tool.py
RUN_DIR=$DEMO_ROOT/run
LOG_DIR=$DEMO_ROOT/logs
PID_FILE=$RUN_DIR/ai-service.pid
TOKEN_FILE=${AI_SERVICE_TOKEN_FILE:-$DEMO_ROOT/data/api-token}
GATE3_VERDICT=${GATE3_VERDICT:-$DEMO_ROOT/data/gate3-runtime.verdict}
LOCK_DIR=$RUN_DIR/ai-service.start.lock

mkdir -p "$RUN_DIR" "$LOG_DIR" "$DEMO_ROOT/data"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "another ai-service start operation is in progress" >&2
    exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT INT TERM
[ -x "$PYTHON_BIN" ] || { echo "Python is not executable: $PYTHON_BIN" >&2; exit 1; }
[ -r "$CONFIG" ] || { echo "Configuration is unavailable: $CONFIG" >&2; exit 1; }
[ -r "$SERVICE" ] || { echo "AI service is unavailable: $SERVICE" >&2; exit 1; }

configured_token=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("auth_token_file", ""))' "$CONFIG")
[ -z "$configured_token" ] || TOKEN_FILE=$configured_token
runtime_enabled=$("$PYTHON_BIN" -c 'import json,sys; print("true" if json.load(open(sys.argv[1]))["runtime"].get("enabled") is True else "false")' "$CONFIG")
if [ "$runtime_enabled" = "true" ] && ! grep -q '^RESULT=PASS\([[:space:]]\|$\)' "$GATE3_VERDICT" 2>/dev/null; then
    echo "BLOCKED: enabled runtime requires Gate 3 PASS at $GATE3_VERDICT" >&2
    exit 2
fi
if [ ! -s "$TOKEN_FILE" ]; then
    umask 077
    "$PYTHON_BIN" -c 'import secrets,sys; open(sys.argv[1],"x").write(secrets.token_hex(32)+"\n")' "$TOKEN_FILE"
fi
chmod 0600 "$TOKEN_FILE"

if [ -r "$PID_FILE" ]; then
    old_pid=$(cat "$PID_FILE" 2>/dev/null || true)
    case "$old_pid" in
        ''|*[!0-9]*) rm -f "$PID_FILE" ;;
        *)
            if [ -r "/proc/$old_pid/cmdline" ] && tr '\000' ' ' < "/proc/$old_pid/cmdline" | grep -q 'ai_service.py'; then
                echo "ai-service already running: PID $old_pid"
                exit 0
            fi
            rm -f "$PID_FILE"
            ;;
    esac
fi

nohup "$PYTHON_BIN" "$SERVICE" --config "$CONFIG" >> "$LOG_DIR/ai-service.log" 2>&1 &
pid=$!
echo "$pid" > "$PID_FILE"

n=0
while [ "$n" -lt 10 ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "ai-service exited during startup; inspect $LOG_DIR/ai-service.log" >&2
        exit 1
    fi
    if AI_SERVICE_TOKEN_FILE="$TOKEN_FILE" "$PYTHON_BIN" "$CLIENT" health --timeout 2 >/dev/null 2>&1; then
        echo "ai-service started: PID $pid"
        exit 0
    fi
    sleep 1
    n=$((n + 1))
done

kill -TERM "$pid" 2>/dev/null || true
rm -f "$PID_FILE"
echo "ai-service did not become healthy; inspect $LOG_DIR/ai-service.log" >&2
exit 1
