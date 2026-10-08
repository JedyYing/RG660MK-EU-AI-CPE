#!/bin/bash
# ai-net-manager 部署到 RG660MK-EU（/data/ai_net），默认 shadow 模式。
# 用法（宿主机执行）:
#   deploy/install.sh                # 部署 + 启动 + 打印状态（shadow）
#   deploy/install.sh --restart      # 仅重启服务
#   deploy/install.sh --probe        # 只跑只读 capacity probe 并取回报告
#   deploy/install.sh --enable-execute   # ⚠ 人工确认后开启 execute（需 rules.yaml 同步）
set -euo pipefail

DEVICE=${DEVICE:-root@192.168.1.1}
HERE=$(cd "$(dirname "$0")/.." && pwd)
REMOTE=/data/ai_net
SSH="ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new $DEVICE"

usage() { sed -n '2,8p' "$0"; exit 1; }
[ $# -gt 0 ] || MODE=install
MODE=${1:-install}

wait_device() {
  for i in $(seq 1 20); do
    if $SSH true 2>/dev/null; then return 0; fi
    echo "  waiting device... ($i/20)"; sleep 3
  done
  echo "device unreachable: $DEVICE"; exit 1
}

case "$MODE" in
  --restart)
    $SSH "/etc/init.d/ai-net-manager restart" && $SSH "/etc/init.d/ai-net-manager status" ;;
  --probe)
    wait_device
    $SSH "mkdir -p $REMOTE/reports"
    $SSH "cd $REMOTE && PYTHONPATH=$REMOTE/app/src:$REMOTE/app python3 -u -m ai_net.executor.capability --out $REMOTE/reports/modem_capability.json"
    scp -q "$DEVICE:$REMOTE/reports/modem_capability.json" "$HERE/reports/modem_capability.json"
    echo "→ $HERE/reports/modem_capability.json" ;;
  --enable-execute)
    echo "⚠ execute 会真实写 Modem（AT+EMMCHLCK）。请同时确认 $HERE/config/rules.yaml"
    echo "  safety.executor_enabled=true 且 config/main.yaml service.mode=execute，并已做锁往返验证。"
    read -r -p "确认现场已授权并完成验证？(yes/NO) " ans
    [ "$ans" = "yes" ] || exit 1
    echo "配置改动需随下次 install 同步；此处仅重启服务。" ;;
  install|"")
    wait_device
    echo "== 同步代码/配置（不覆盖设备端数据目录）"
    $SSH "mkdir -p $REMOTE/app $REMOTE/config $REMOTE/reports $REMOTE/models"
    rsync -a --delete --exclude '__pycache__' "$HERE/src/" "$DEVICE:$REMOTE/app/src/"
    for f in main.yaml rules.yaml traffic_profiles.yaml; do
      [ -f "$HERE/config/$f" ] && scp -q "$HERE/config/$f" "$DEVICE:$REMOTE/config/$f"
    done
    rsync -a --exclude '__pycache__' "$HERE/tools/" "$DEVICE:$REMOTE/app/tools/" 2>/dev/null || true
    echo "== 安装 init 脚本"
    scp -q "$HERE/deploy/ai-net-manager.init" "$DEVICE:/etc/init.d/ai-net-manager"
    $SSH "chmod +x /etc/init.d/ai-net-manager"
    echo "== 设备环境自检（Step 0）"
    $SSH "sh -s" <<'EOS' | tee "$HERE/reports/env_probe.txt"
echo "== uname: $(uname -a)"
echo "== os-release:"; cat /etc/os-release 2>/dev/null | head -4
echo "== python: $(python3 --version 2>&1)"
echo "== /data 空间:"; df -h /data /overlay 2>/dev/null | head -4
echo "== AT 通道:"; ls -l /dev/adb_atci_socket /dev/ccci_at /dev/ttyCMIPC2 2>/dev/null
echo "== 现有 modem 进程:"; ps w 2>/dev/null | grep -Ei "atcid|quectel|mipc|qmi" | grep -v grep
echo "== ccmni 网卡:"; ip -br link 2>/dev/null | grep -E "ccmni|wwan" || echo "(无)"
echo "== 默认路由:"; ip route 2>/dev/null | head -3
echo "== conntrack:"; ls -l /proc/net/nf_conntrack 2>/dev/null || echo "(缺 nf_conntrack)"
EOS
    echo "== 启动服务"
    $SSH "/etc/init.d/ai-net-manager enable && /etc/init.d/ai-net-manager restart"
    sleep 5
    $SSH "/etc/init.d/ai-net-manager status || true"
    echo "== 本地状态接口（设备内回环）"
    $SSH "wget -qO- http://127.0.0.1:8787/health 2>/dev/null || echo '(api 未就绪)'" ;;
  *) usage ;;
esac
