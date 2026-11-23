#!/bin/sh
set -eu
# 2026-11-20：/mode 需要运维令牌（设备 /data/ai_cpe/secrets/ops_token）。
TOK=$(cat /data/ai_cpe/secrets/ops_token 2>/dev/null) || { echo "缺少 /data/ai_cpe/secrets/ops_token"; exit 2; }
OUT=$(curl -s -m 8 -w '\n%{http_code}' -X POST http://127.0.0.1:8099/mode -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d '{"mode":"online"}') || true
CODE=$(printf '%s\n' "$OUT" | tail -n 1)
printf '%s\n' "$OUT" | sed '$d'
[ "$CODE" = 200 ] || { echo "切换失败（HTTP ${CODE:-无响应}）"; exit 1; }
printf "%s\n" "已切到在线模式（Hermes 在线通道 deepseek-v4-flash）。未改 Hermes 配置、密钥或启动项。"
