#!/bin/sh
# KWS 唤醒词检测（优先常驻服务 ~200ms；不可用时回退命令行 ~5s）
# 用法: kws_spot.sh <16k mono wav>
# 命中输出含 {"keyword": ...}；voice_assistant.py 的 kws_hit() 就是找这个字符串
WAV="$1"
[ -f "$WAV" ] || { echo '{"error":"no audio file"}'; exit 1; }

# —— 首选：常驻服务 kws_server（模型只加载一次）——
if [ -S /tmp/kws.sock ]; then
  OUT=$(/data/hermes/venv/bin/python -c '
import socket, sys
try:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(4)
    s.connect("/tmp/kws.sock")
    s.sendall((sys.argv[1] + "\n").encode())
    print(s.recv(4096).decode().strip())
    s.close()
except Exception:
    print("")
' "$WAV" 2>/dev/null)
  if [ -n "$OUT" ]; then echo "$OUT"; exit 0; fi
fi

# —— 回退：按需命令行（每次加载模型，~5s，仅在常驻服务挂了时用）——
BIN=/data/ai_cpe/sherpa-onnx/bin/sherpa-onnx-keyword-spotter
M=/data/ai_cpe/sherpa-onnx/model
GLIBC=/data/ai_cpe/glibc
[ -x "$BIN" ] || { echo '{"error":"binary missing"}'; exit 1; }
exec "$GLIBC/ld-linux-aarch64.so.1" --library-path "$GLIBC" "$BIN" \
  --tokens="$M/tokens.txt" \
  --encoder="$M/encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx" \
  --decoder="$M/decoder-epoch-13-avg-2-chunk-16-left-64.onnx" \
  --joiner="$M/joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx" \
  --keywords-file="${KWS_KEYWORDS:-$M/keywords.txt}" \
  --provider=cpu --num-threads=1 "$WAV" 2>/dev/null
