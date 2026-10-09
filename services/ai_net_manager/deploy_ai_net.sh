#!/bin/bash
# 部署 ai-net-manager 到 RG660MK（Shadow 模式）
set -u
K=/home/jedyying/.ssh/id_ed25519_termux
OPTS="-o ConnectTimeout=8 -o BatchMode=yes -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
T=""
for t in "root@fdc3:8285:b409::1" "root@192.168.1.1"; do
  if timeout 10 ssh -i $K $OPTS $t 'echo OK' 2>/dev/null | grep -q OK; then T=$t; break; fi
done
[ -z "$T" ] && { echo "✗ 设备不可达（fdc3 / 192.168.1.1）"; exit 1; }
echo "✓ 目标: $T"
HERE="$(cd "$(dirname "$0")" && pwd)"

STAGE=$(mktemp -d /tmp/ainet_stage.XXXXXX)
mkdir -p "$STAGE/config" "$STAGE/reports" "$STAGE/logs"
cp "$HERE"/config/*.json "$STAGE/config/"
cp -r "$HERE/src/ai_net" "$STAGE/ai_net"
cp -r "$HERE/schemas" "$STAGE/schemas"
cp -r "$HERE/tools" "$STAGE/tools"
[ -d "$HERE/reports" ] && cp "$HERE"/reports/*.txt "$HERE"/reports/*.json "$STAGE/reports/" 2>/dev/null

echo "▸ 推送（$(du -sh "$STAGE" | cut -f1)）…"
tar czf - -C "$STAGE" . | ssh -i $K $OPTS $T 'mkdir -p /data/ai_cpe/ai_net && tar xzf - -C /data/ai_cpe/ai_net && rm -rf /data/ai_cpe/ai_net/__pycache__'

echo "▸ 安装 procd 服务…"
cat "$HERE/ai_net.init" | ssh -i $K $OPTS $T 'cat > /etc/init.d/ai-net && chmod 755 /etc/init.d/ai-net && /etc/init.d/ai-net enable'

echo "▸ 能力探测（只读）…"
ssh -i $K $OPTS $T 'cd /data/ai_cpe/ai_net && python3 -m ai_net.executor.capability 2>&1 | tail -14' || true
scp -q -i $K $OPTS "$T:/data/ai_cpe/ai_net/reports/modem_capability.json" "$HERE/reports/" 2>/dev/null && echo "  capability 报告已取回 repo"

echo "▸ 启动服务（shadow）…"
ssh -i $K $OPTS $T '/etc/init.d/ai-net restart 2>&1; sleep 6;
  echo "--- 进程:"; ps w | grep "[a]i_net.service" | head -2
  echo "--- API:"; curl -s -m 6 http://[::1]:8123/v1/status | head -c 600; echo
  echo "--- 数据:"; ls -la /data/ai_cpe/ai_net/data/ 2>/dev/null | head -8'

rm -rf "$STAGE"
echo "✓ 部署完成（Shadow 已运行；日志 /data/ai_cpe/ai_net/logs/service.log）"
