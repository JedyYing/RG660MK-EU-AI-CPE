#!/bin/sh
# Explicit Hermes rollback. Does not touch WAN/LAN/DHCP/DNS/NAT/firewall.
set -u
PID_FILE=${HERMES_HOME:-/data/ai_cpe/hermes/.hermes}/gateway.pid

if [ ! -r "$PID_FILE" ]; then
    echo "Hermes gateway PID file not found; nothing changed"
    exit 0
fi
pid=$(cat "$PID_FILE" 2>/dev/null || true)
case "$pid" in ''|*[!0-9]*) echo "Invalid Hermes PID file: $PID_FILE" >&2; exit 1 ;; esac
if [ ! -r "/proc/$pid/cmdline" ]; then
    echo "Hermes gateway is already stopped"
    exit 0
fi
cmd=$(tr '\000' ' ' < "/proc/$pid/cmdline")
case "$cmd" in
    *hermes*gateway*run*) ;;
    *) echo "Refusing to stop PID $pid; command is not Hermes gateway: $cmd" >&2; exit 1 ;;
esac
kill -TERM "$pid"
n=0
while [ -d "/proc/$pid" ] && [ "$n" -lt 20 ]; do sleep 1; n=$((n + 1)); done
if [ -d "/proc/$pid" ]; then
    echo "Hermes gateway did not stop after 20s; manual review required" >&2
    exit 1
fi
echo "Hermes gateway stopped; CPE networking and AI services were not modified"
