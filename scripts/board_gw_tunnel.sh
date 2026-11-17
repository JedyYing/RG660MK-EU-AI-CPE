#!/bin/sh
# 板子(dropbear) -> 网关 本地转发：Immich 2283 / CasaOS 10080
# pidfile 单实例；断线 5 秒重连；由 cron 每分钟保活
GW=47.116.191.31
KEY=/data/ai_cpe/.ssh/gw_board_ed25519
PIDF=/var/run/gw_tunnel.pid

[ -f "$PIDF" ] && kill -0 "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null && exit 0
rm -f "$PIDF"

(
  while :; do
    ssh -N -y -K 20 -i "$KEY" \
      -L 127.0.0.1:2283:127.0.0.1:18082 \
      -L 127.0.0.1:10080:127.0.0.1:18083 \
      root@"$GW"
    sleep 5
  done
) &
echo $! > "$PIDF"
