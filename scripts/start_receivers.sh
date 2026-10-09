#!/bin/sh
# 三流接收端启动器（幂等）：camav6(:8092 转发) + rtg3_probe(:8094 手机接收页)
# 2026-10-09 恢复落地；由 rc.local 开机自启、rg660mk_recover.sh 部署。
cd /data/ai_cpe/v14 || exit 1
[ -f rtg_player.html ] && cp -f rtg_player.html /tmp/rtg_player.html
pgrep -f "[c]amav6.py" >/dev/null || { nohup /usr/bin/python3 camav6.py 8092 >>/data/ai_cpe/v14/camav6.log 2>&1 & sleep 1; }
pgrep -f "[r]tg3_probe.py" >/dev/null || { nohup /usr/bin/python3 rtg3_probe.py --port 8094 --media-port 8092 >>/data/ai_cpe/v14/rtg3_probe.log 2>&1 & sleep 1; }
C1=$(ps w | grep -c "[c]amav6.py"); C2=$(ps w | grep -c "[r]tg3_probe.py")
echo "receivers: camav6=$C1 probe=$C2"
