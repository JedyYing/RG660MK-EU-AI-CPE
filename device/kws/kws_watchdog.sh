#!/bin/sh
# KWS 常驻服务守护（每分钟由 cron 调用；不碰其他服务）
# 用 [k]ws_server 写法避免 pgrep -f 自匹配（脚本自身命令行也含 kws_server）
if pgrep -f "[k]ws_server /data/ai_cpe/sherpa-onnx/model" >/dev/null 2>&1; then
  exit 0
fi
# 清理残留 socket
rm -f /tmp/kws.sock 2>/dev/null
nohup /data/ai_cpe/glibc/ld-linux-aarch64.so.1 --library-path /data/ai_cpe/glibc \
  /data/ai_cpe/sherpa-onnx/bin/kws_server /data/ai_cpe/sherpa-onnx/model /tmp/kws.sock \
  >> /data/ai_cpe/logs/kws_server.log 2>&1 &
