#!/bin/bash
# rg660mk_recover.sh — 网线恢复后的"一键恢复流水线"（2026-10-09 编写，逐段可独立重跑）
# 跑法: bash ~/rg660mk_recover.sh        （全流程）
#       bash ~/rg660mk_recover.sh qos    （只重跑 QoS）
#       bash ~/rg660mk_recover.sh link   （只查链路）
set -u
K=/home/jedyying/.ssh/id_ed25519_termux
OPTS="-o ConnectTimeout=8 -o BatchMode=yes -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"
V14=/home/jedyying/rg660mk_rtg_v14
STAGE=/tmp/rg660mk_stage/hermes_home/aicpe_demo

detect() {
  T=""
  for t in "root@fdc3:8285:b409::1" "root@192.168.1.1"; do
    if timeout 10 ssh -i $K $OPTS $t 'echo OK' 2>/dev/null | grep -q OK; then T=$t; break; fi
  done
  [ -z "$T" ] && { echo "✗ 链路不可达（fdc3 / 192.168.1.1 均不通）"; return 1; }
  case "$T" in *fdc3*) SCPT="root@[fdc3:8285:b409::1]";; *) SCPT="root@192.168.1.1";; esac
  SSH="ssh -i $K $OPTS $T"
  echo "✓ 连接: $T"; return 0
}

case "${1:-all}" in
link) detect ;;
qos)
  detect || exit 1
  echo "== QoS 重跑（修复版） =="
  $SSH 'sh /data/ai_cpe/hermes/home/aicpe_demo/run_qos_demo.sh'
  echo; echo "== 取回结果 =="
  rm -rf "$HOME/Desktop/RG660MK-QoS结果"
  scp -q -i $K $OPTS -r "$SCPT:/data/ai_cpe/hermes/home/aicpe_demo/deliver_qos" "$HOME/Desktop/RG660MK-QoS结果" \
    && echo "✓ 已取回 ~/Desktop/RG660MK-QoS结果" && cat "$HOME/Desktop/RG660MK-QoS结果/报告.md" 2>/dev/null
  ;;
all)
  detect || exit 1
  echo "== [1/8] 清理残留 =="
  $SSH 'for p in $(pgrep -f "[q]os_sim"); do kill $p 2>/dev/null; done; echo cleaned; ls /data/ai_cpe/hermes/home/aicpe_demo/deliver_qos 2>/dev/null | head -5'
  echo "== [2/8] 推送修复文件 =="
  scp -q -i $K $OPTS $STAGE/qos_sim.py $STAGE/run_qos_demo.sh "$SCPT:/data/ai_cpe/hermes/home/aicpe_demo/" && echo "✓ 脚本已更新"
  $SSH 'mkdir -p /data/ai_cpe/v14'
  scp -q -i $K $OPTS $V14/three_stream_test_20261006/rtg3_probe.py $V14/scripts/camav6.py $V14/scripts/rtg_player.html "$SCPT:/data/ai_cpe/v14/" && echo "✓ 接收端已上传"
  scp -q -i $K $OPTS /home/jedyying/rg660mk_start_receivers.sh "$SCPT:/data/ai_cpe/v14/start_receivers.sh" && echo "✓ 启动器已上传"
  echo "== [3/8] 防火墙（8094/8092/8095 TCP 放行） =="
  $SSH 'for P in 8094 8092 8095; do
          if ! uci show firewall 2>/dev/null | grep -qE "dest_port=.?$P"; then
            uci add firewall rule >/dev/null
            uci set firewall.@rule[-1].name="rtg3-$P"
            uci set firewall.@rule[-1].src="wan"
            uci set firewall.@rule[-1].proto="tcp"
            uci set firewall.@rule[-1].dest_port="$P"
            uci set firewall.@rule[-1].target="ACCEPT"
          fi
        done; uci commit firewall; /etc/init.d/firewall reload >/dev/null 2>&1; echo fw4_reloaded; nft list chain inet fw4 input_wan 2>/dev/null | grep -E "dport (8094|8092|8095)"'
  echo "== [4/8] 启动接收端 =="
  $SSH 'sh /data/ai_cpe/v14/start_receivers.sh'
  sleep 4
  echo "== [5/8] 设备本地自检 =="
  $SSH 'echo "--8094 /time:"; curl -s -m 6 http://[::1]:8094/time | head -c 200; echo; echo "--8094 /status:"; curl -s -m 6 http://[::1]:8094/status | head -c 200; echo; echo "--8092 via camav6:"; curl -s -m 6 http://[::1]:8092/status | head -c 150; echo; echo "--进程:"; ps w | grep -E "[r]tg3_probe|[c]amav6|[c]amav.py" | head -5; echo "--公网v6:"; ip -o -6 addr show 2>/dev/null | grep ccmni | grep -oE "inet6 [0-9a-f:]+" | head -3'
  echo "== [6/8] make_qr 复验（应自动重出二维码） =="
  sleep 35
  cd $V14/three_stream_test_20261006 && python3 make_qr.py 2>&1 | tail -3
  ls -la "$HOME/Desktop/三流测试_手机扫码打开.png"
  echo "== [7/8] 巡检看板复起 =="
  $SSH 'sh /data/ai_cpe/hermes/home/aicpe_demo/run_inspection.sh'
  echo "== [8/8] QoS 修复版重跑 =="
  bash "$0" qos
  ;;
esac