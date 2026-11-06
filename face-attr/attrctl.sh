#!/bin/sh
# attrctl — 人脸属性服务（性别+年龄）控制入口
# 用法: attrctl start|stop|restart|status|health|bench|log
set -e
DIR="$HOME/Documents/RG660MK人脸属性"
PY=python3
LOG=/tmp/attr_service.log
PIDF=/tmp/attr_service.pid

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
        echo "✓ 已启动 (PID $(cat $PIDF))  http://127.0.0.1:8093"
    else
        echo "✗ 启动失败，看日志: $LOG"; tail -5 "$LOG"; exit 1
    fi
    ;;
  stop)
    if running; then kill "$(cat $PIDF)" && rm -f "$PIDF" && echo "✓ 已停止"; else echo "未在运行"; fi
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
  *)
    echo "用法: attrctl {start|stop|restart|status|health|bench [N]|log [N]}"; exit 1
    ;;
esac
