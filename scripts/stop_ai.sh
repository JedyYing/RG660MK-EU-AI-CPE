#!/bin/sh
# Stop only demo AI processes. Hermes and CPE networking are intentionally preserved.
set -u
RUN_DIR=${RUN_DIR:-/data/ai_cpe/demo/run}

stop_pidfile() {
    name=$1
    file=$2
    expected=$3
    [ -r "$file" ] || return 0
    pid=$(cat "$file" 2>/dev/null || true)
    case "$pid" in ''|*[!0-9]*) echo "$name: invalid pid file $file"; return 1 ;; esac
    if [ ! -r "/proc/$pid/cmdline" ]; then
        rm -f "$file"
        echo "$name: already stopped"
        return 0
    fi
    cmd=$(tr '\000' ' ' < "/proc/$pid/cmdline")
    case "$cmd" in
        *"$expected"*) ;;
        *) echo "$name: refusing to stop PID $pid with unexpected command: $cmd"; return 1 ;;
    esac
    kill -TERM "$pid"
    n=0
    while [ -d "/proc/$pid" ] && [ "$n" -lt 20 ]; do sleep 1; n=$((n + 1)); done
    if [ -d "/proc/$pid" ]; then
        echo "$name: did not stop after 20s; manual review required"
        return 1
    fi
    rm -f "$file"
    echo "$name: stopped"
}

rc=0
stop_pidfile ai-service "$RUN_DIR/ai-service.pid" ai_service.py || rc=1
stop_pidfile capture-service "$RUN_DIR/capture-service.pid" capture_service || rc=1
stop_pidfile vision-service "$RUN_DIR/vision-service.pid" vision_service || rc=1
stop_pidfile audio-service "$RUN_DIR/audio-service.pid" audio_service || rc=1
exit "$rc"
