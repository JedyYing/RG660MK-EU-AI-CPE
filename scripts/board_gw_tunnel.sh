#!/bin/sh
# 板子 -> 网关 本地转发（Immich 2283 / CasaOS 10080），pidfile 单实例 + 断线重连
# 用法：sh /data/ai_cpe/tools/gw_tunnel.sh   （由 cron 每分钟保活）
GW=47.116.191.31
KEY=/data/ai_cpe/.ssh/gw_board
PIDF=/var/run/gw_tunnel.pid

# 已有实例在跑就直接退出
if [ -f "$PIDF" ]; then
  O=$(cat "$PIDF" 2>/dev/null)
  [ -n "$O" ] && [ -d "/proc/$O" ] && exit 0
fi
rm -f "$PIDF"

# 外层循环：ssh 退出后 5 秒重连
(
  while :; do
    ssh -N -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
        -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=accept-new \
        -L 127.0.0.1:2283:127.0.0.1:18082 \
        -L 127.0.0.1:10080:127.0.0.1:18083 \
        root@$GW
    sleep 5
  done
) &
echo $! > "$PIDF"
