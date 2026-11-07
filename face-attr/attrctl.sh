#!/bin/sh
# attrctl — 人脸属性服务（性别+年龄）控制入口
# 用法: attrctl start|stop|restart|status|health|bench|log
set -e
DIR="$HOME/Documents/RG660MK人脸属性"
PY=python3
LOG=/tmp/attr_service.log
PIDF=/tmp/attr_service.pid
PIDF_LIVE=/tmp/attr_live.pid
DIRLIVE="$DIR"

running() {
    [ -f "$PIDF" ] && kill -0 "$(cat $PIDF)" 2>/dev/null
}

case "${1:-status}" in
  start)
    if running; then echo "已在运行 (PID $(cat $PIDF))"; exit 0; fi
    cd "$DIR"
    nohup nice -n 10 $PY -u attr_service_pc.py --port 8093 > "$LOG" 2>&1 &
    echo $! > "$PIDF"
    sleep 3
    if curl -s -m 3 http://127.0.0.1:8093/health > /dev/null 2>&1; then
        echo "✓ API 已启动 (PID $(cat $PIDF))  http://127.0.0.1:8093"
    else
        echo "✗ API 启动失败，看日志: $LOG"; tail -5 "$LOG"; exit 1
    fi
    # 实时预览页（8094）
    if ! (curl -s -m 2 -o /dev/null http://127.0.0.1:8094/ 2>/dev/null); then
        cd "$DIR"
        nohup nice -n 10 $PY -u attr_live.py --port 8094 > /tmp/attr_live.log 2>&1 &
        echo $! > "$PIDF_LIVE"
        sleep 3
        if curl -s -m 3 -o /dev/null http://127.0.0.1:8094/ 2>/dev/null; then
            echo "✓ 预览页已启动 (PID $(cat $PIDF_LIVE))  http://127.0.0.1:8094"
        else
            echo "✗ 预览页启动失败，看 /tmp/attr_live.log"; tail -5 /tmp/attr_live.log
        fi
    fi
    ;;
  stop)
    if running; then kill "$(cat $PIDF)" && rm -f "$PIDF" && echo "✓ API 已停止"; else echo "API 未在运行"; fi
    if [ -f "$PIDF_LIVE" ] && kill -0 "$(cat $PIDF_LIVE)" 2>/dev/null; then
        kill "$(cat $PIDF_LIVE)" && rm -f "$PIDF_LIVE" && echo "✓ 预览页已停止"
    fi
    ;;
  restart)
    "$0" stop; sleep 1; "$0" start
    ;;
  status)
    if running; then
        echo "● 运行中 (PID $(cat $PIDF))"
        curl -s -m 3 http://127.0.0.1:8093/health | $PY -m json.tool 2>/dev/null || true
    else
        echo "○ 未运行"
    fi
    ;;
  health)
    curl -s -m 3 http://127.0.0.1:8093/health; echo
    ;;
  bench)
    cd "$DIR"; $PY -u attr_service_pc.py --bench "${2:-40}"
    ;;
  log)
    tail -"${2:-30}" "$LOG"
    ;;
  check)
    # 演示前 60 秒自检
    ok=0; fail=0
    echo "── 演示前自检 ──"
    # 1 USB 网卡
    if ip -br addr 2>/dev/null | grep -q "enx.*192.168.1.212"; then
        echo "  ✓ USB 网卡在位 (192.168.1.212)"; ok=$((ok+1))
    else
        echo "  ✗ USB 网卡不在位 —— 插回那个 USB 转网口转换器"; fail=$((fail+1))
    fi
    # 2 设备 8099
    c=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://192.168.1.1:8099/ 2>/dev/null)
    if [ "$c" = "200" ]; then echo "  ✓ 8099 演示页正常"; ok=$((ok+1));
    else echo "  ✗ 8099 返回 $c"; fail=$((fail+1)); fi
    # 3 摄像头
    c=$(curl -s -m 6 -o /dev/null -w '%{http_code}' http://192.168.1.1:8090/snapshot 2>/dev/null)
    if [ "$c" = "200" ]; then echo "  ✓ 摄像头 8090 出帧正常"; ok=$((ok+1));
    else echo "  ✗ 摄像头 8090 返回 $c"; fail=$((fail+1)); fi
    # 4 属性服务
    if curl -s -m 5 http://127.0.0.1:8093/health >/dev/null 2>&1; then
        echo "  ✓ 属性服务在跑"; ok=$((ok+1))
    else
        echo "  ✗ 属性服务不通 —— 跑 ~/attrctl start"; fail=$((fail+1))
    fi
    # 4.5 预览页
    if curl -s -m 4 -o /dev/null http://127.0.0.1:8094/ 2>/dev/null; then
        echo "  ✓ 实时预览页在跑 (http://192.168.1.212:8094/)"; ok=$((ok+1))
    else
        echo "  ✗ 预览页不通 —— 跑 ~/attrctl start"; fail=$((fail+1))
    fi
    # 5 端到端识别
    r=$(curl -s -m 20 -X POST http://127.0.0.1:8093/vision/attribute \
        -H "Content-Type: application/json" -d '{"snapshot":true}' 2>/dev/null)
    n=$(echo "$r" | sed -n 's/.*"face_count": *\([0-9]*\).*/\1/p')
    if [ -n "$n" ] && [ "$n" -gt 0 ]; then
        g=$(echo "$r" | sed -n 's/.*"gender": *"\([a-z]*\)".*/\1/p')
        b=$(echo "$r" | sed -n 's/.*"age_band": *"\([^"]*\)".*/\1/p')
        echo "  ✓ 端到端正常：检出 $n 张脸 → $g / $b"; ok=$((ok+1))
    else
        echo "  △ 服务正常但画面里没检出人脸（请站到摄像头前，50–80 cm，正对）"; fail=$((fail+1))
    fi
    echo "── $ok 项通过，$fail 项待处理 ──"
    ;;
  *)
    echo "用法: attrctl {start|stop|restart|status|health|check|bench [N]|log [N]}"; exit 1
    ;;
esac
