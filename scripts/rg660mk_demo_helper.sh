#!/bin/sh
# rg660mk_demo_helper.sh — RG660MK 桌面演示助手（连接自愈：fdc3 IPv6 优先，v4 兜底）
# 用法: sh rg660mk_demo_helper.sh {inspection|qos|conn|url}
SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no"

try_ssh() { timeout 7 ssh $SSH_OPTS "$1" 'echo __OK__' 2>/dev/null | grep -q __OK__; }

pick_target() {
  if try_ssh "root@fdc3:8285:b409::1"; then echo "root@fdc3:8285:b409::1"; return 0; fi
  if try_ssh "root@192.168.1.1"; then echo "root@192.168.1.1"; return 0; fi
  return 1
}

case "$1" in
  conn) pick_target ;;
  url)
    T=$(pick_target) || exit 1
    case "$T" in *fdc3*) echo "http://[fdc3:8285:b409::1]" ;; *) echo "http://192.168.1.1" ;; esac ;;
  inspection)
    T=$(pick_target) || { echo "✗ 设备不可用（fdc3 与 192.168.1.1 均不通）——请检查网线与设备电源，或稍后重试"; exit 1; }
    echo "连接方式：$T"
    ssh $SSH_OPTS "$T" 'sh /data/ai_cpe/hermes/home/aicpe_demo/run_inspection.sh'
    U=$(sh "$0" url)
    echo; echo "=== 打开巡检看板：$U:8099/ ==="
    xdg-open "$U:8099/" >/dev/null 2>&1 || true ;;
  qos)
    T=$(pick_target) || { echo "✗ 设备不可用（fdc3 与 192.168.1.1 均不通）——请检查网线与设备电源，或稍后重试"; exit 1; }
    echo "连接方式：$T"
    echo "=== 1/2 设备侧一键：仿真 + 出报告（约 60 秒）==="
    ssh $SSH_OPTS "$T" 'sh /data/ai_cpe/hermes/home/aicpe_demo/run_qos_demo.sh'
    echo; echo "=== 2/2 取回结果并打开 ==="
    case "$T" in *fdc3*) SC="root@[fdc3:8285:b409::1]" ;; *) SC="$T" ;; esac
    rm -rf "$HOME/Desktop/RG660MK-QoS结果"
    scp -q -r $SSH_OPTS "$SC:/data/ai_cpe/hermes/home/aicpe_demo/deliver_qos" "$HOME/Desktop/RG660MK-QoS结果" \
      && echo "已取回：~/Desktop/RG660MK-QoS结果"
    xdg-open "$HOME/Desktop/RG660MK-QoS结果" >/dev/null 2>&1 || true ;;
  *) echo "用法: sh rg660mk_demo_helper.sh {inspection|qos|conn|url}"; exit 2 ;;
esac
