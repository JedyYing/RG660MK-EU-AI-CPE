#!/bin/sh
# health_watch.sh —— 多服务同时不可用时的现场取证（单实例/只读/有界）
# 10s：关键服务 PID + 监听端口 + 本地健康检查；30s：资源行
# 异常：先落盘现场（进程/端口/内核/路由/防火墙/挂载），不做任何恢复
# 日志轮转：单文件 10MB，保留 1 份（合计 <=20MB）
D=/data/ai_cpe/logs/health
mkdir -p $D
LOG=$D/health.log
PIDF=/var/run/health_watch.pid
if [ -f "$PIDF" ]; then
  OP=$(cat "$PIDF" 2>/dev/null)
  if [ -n "$OP" ] && [ -d "/proc/$OP" ] && tr '\0' ' ' < /proc/$OP/cmdline 2>/dev/null | grep -q "health_watch.sh"; then
    exit 0
  fi
fi
echo $$ > "$PIDF"
trap 'rm -f "$PIDF"' EXIT

rotate() {
  [ -f "$LOG" ] || return 0
  SZ=$(wc -c < "$LOG" 2>/dev/null || echo 0)
  [ "$SZ" -gt 10485760 ] && { mv -f "$LOG" "$LOG.1"; : > "$LOG"; }
}

SERVICES="sshd|22|dropbear
web|80|uhttpd
mqtt|1883|broker_daemon
asr|6006|websocket
demo|8099|aicpe_demo.py
gateway|0|hermes gateway
voice|0|voice_assistant.py
agent|0|smarthome/agent.py"

port_listen() {
  python3 - "$1" <<'PY' 2>/dev/null
import sys
p=int(sys.argv[1]); ok=0
for fn in ("/proc/net/tcp","/proc/net/tcp6"):
    try: ls=open(fn).read().splitlines()[1:]
    except: continue
    for l in ls:
        f=l.split()
        if len(f)>3 and f[3]=="0A":
            try:
                if int(f[1].split(":")[1],16)==p: ok=1
            except: pass
print(ok)
PY
}

pids_of() { ps w 2>/dev/null | grep -v grep | grep -F "$1" | awk '{print $1}' | tr '\n' ',' | sed 's/,$//'; }
proc_state() { [ -r "/proc/$1/stat" ] && echo "$(awk '{print $3}' /proc/$1/stat 2>/dev/null)/$(cat /proc/$1/wchan 2>/dev/null)" || echo "-"; }

snapshot() {
  TS=$(date +%s); F=$D/fail_${TS}_$1.txt
  {
    echo "### reason=$1 time=$(date '+%s %F %T') uptime=$(cut -d' ' -f1 /proc/uptime)"
    echo "-- services --"
    echo "$SERVICES" | while IFS='|' read -r name port pat; do
      [ -z "$name" ] && continue
      P=$(pids_of "$pat"); L="-"; [ "$port" != "0" ] && L=$(port_listen "$port")
      echo "$name pid=[$P] listen=$L state=$(proc_state "${P%%,*}")"
    done
    echo "-- mem --"; grep -E "MemTotal|MemAvailable|MemFree" /proc/meminfo
    echo "-- load/uptime --"; cat /proc/loadavg; cut -d' ' -f1 /proc/uptime
    echo "-- ps count --"; ps w 2>/dev/null | wc -l
    echo "-- top cpu/mem --"; top -bn1 2>/dev/null | head -16 | tail -11
    echo "-- printk skb 计数 --"; dmesg 2>/dev/null | grep -c skb_headroom
    echo "-- fd top10 --"; for p in /proc/[0-9]*; do echo "$(ls $p/fd 2>/dev/null | wc -l) ${p#/proc/}"; done | sort -rn | head -10
    echo "-- disk --"; df -h / /overlay /data 2>/dev/null; df -i / /data 2>/dev/null
    echo "-- mount --"; mount | grep -E " / | /data | /overlay "
    echo "-- route --"; ip route show 2>/dev/null | head -20; ip -brief addr 2>/dev/null | grep -E "br-lan|ccmni2|zt" | head -5
    echo "-- conntrack --"; cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null
    echo "-- tcp --"
    python3 - <<'PY' 2>/dev/null
L=E=O=0
for fn in ("/proc/net/tcp","/proc/net/tcp6"):
    try: ls=open(fn).read().splitlines()[1:]
    except: continue
    for l in ls:
        f=l.split()
        if len(f)<4: continue
        if f[3]=="0A": L+=1
        elif f[3]=="01": E+=1
        else: O+=1
print("listen=%d estab=%d other=%d"%(L,E,O))
PY
    echo "-- firewall --"; (nft list ruleset 2>/dev/null || iptables -L -n 2>/dev/null) | head -40
    echo "-- dmesg tail200 --"; dmesg 2>/dev/null | tail -200
    echo "-- logread tail100 --"; logread 2>/dev/null | tail -100
    echo "-- oom --"; dmesg 2>/dev/null | grep -iE "out of memory|oom-kill|killed process" | tail -20
    echo "-- cgroup --"; cat /sys/fs/cgroup/memory.events 2>/dev/null || cat /sys/fs/cgroup/memory/memory.oom_control 2>/dev/null || echo "(none)"
  } > "$F" 2>&1
  echo "$(date '+%F %T') SNAP $1 -> $F" >> $D/events.log
}

LAST=""; CNT=0
while true; do
  rotate; TS=$(date '+%F %T')
  echo "$SERVICES" | while IFS='|' read -r name port pat; do
    [ -z "$name" ] && continue
    P=$(pids_of "$pat"); L="-"
    if [ "$port" != "0" ]; then
      L=$(port_listen "$port"); [ "$L" = "1" ] && ST=UP || ST=DOWN
    else
      [ -n "$P" ] && ST=UP || ST=DOWN
    fi
    echo "$TS svc $name pid=[$P] listen=$L stat=$ST" >> $LOG
  done

  echo "$SERVICES" | while IFS='|' read -r name port pat; do
    [ -z "$name" ] && continue
    B=""
    if [ "$port" != "0" ]; then
      [ "$(port_listen "$port")" = "1" ] || B="no_listen"
      if [ -z "$B" ] && [ "$port" = "8099" ]; then
        C=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:8099/ 2>/dev/null)
        [ "$C" = "200" ] || B="http_$C"
      fi
    else
      [ -n "$(pids_of "$pat")" ] || B="exit"
    fi
    [ -n "$B" ] && echo "$name:$B"
  done > $D/.cur_bad 2>/dev/null
  CUR=$(tr '\n' ' ' < $D/.cur_bad 2>/dev/null)
  [ -n "$CUR" ] && [ "$CUR" != "$LAST" ] && snapshot "$(echo $CUR | tr ' :' '__')"
  LAST="$CUR"

  CNT=$((CNT+1))
  if [ $((CNT % 3)) -eq 1 ]; then
    MEM=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    PC=$(ps w 2>/dev/null | wc -l)
    DU=$(df /data 2>/dev/null | awk 'NR==2{gsub("%","",$5);print $5}')
    DI=na  # busybox df 无 -i
    RO=$(mount | grep " /data " | grep -c "(ro,")
    L1=$(awk '{print $1}' /proc/loadavg)
    CT=$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null)
    FD=0; for p in /proc/[0-9]*; do FD=$((FD+$(ls $p/fd 2>/dev/null | wc -l))); done
    UP=$(cut -d' ' -f1 /proc/uptime)
    EXT=0
    EC=$(curl -s -m 3 -o /dev/null -w "%{http_code}" http://192.168.1.212:2283/ 2>/dev/null)
    [ "$EC" = "200" ] && EXT=1
    if [ "$EXT" = "0" ] && [ "$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8099/ 2>/dev/null)" = "200" ]; then
      echo "$TS res_ext_unreachable local8099=200" >> $LOG
      snapshot "ext_unreachable"
    fi
    echo "$TS res mem_avail=$MEM procs=$PC data_use=$DU% data_inode=$DI data_ro=$RO load1=$L1 conntrack=$CT fd=$FD ext=$EXT uptime=$UP" >> $LOG
  fi
  sleep 10
done
