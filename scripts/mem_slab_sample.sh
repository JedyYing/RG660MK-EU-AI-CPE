#!/bin/bash
# 30 分钟内核内存/printk 相关性采样（每 5 分钟一次，6 次）
OUT="$HOME/rg660_diag/mem_slab.csv"
[ -f "$OUT" ] || echo "epoch,local,uptime_s,mem_avail_kb,slab_kb,sunreclaim_kb,skb_warn,load1" > "$OUT"
for i in $(seq 1 6); do
  R=$(timeout 12 ssh -o ConnectTimeout=6 root@192.168.1.1 'U=$(cut -d. -f1 /proc/uptime); A=$(awk "/MemAvailable/{print \$2}" /proc/meminfo); S=$(awk "/^Slab:/{print \$2}" /proc/meminfo); SU=$(awk "/SUnreclaim/{print \$2}" /proc/meminfo); W=$(dmesg | grep -c skb_headroom); L=$(awk "{print \$1}" /proc/loadavg); echo "$U,$A,$S,$SU,$W,$L"' 2>/dev/null)
  [ -n "$R" ] && echo "$(date +%s),$(date '+%F %T'),$R" >> "$OUT"
  sleep 300
done
echo "DONE"
