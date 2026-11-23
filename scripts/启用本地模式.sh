#!/bin/sh
set -eu
# 2026-11-02：看板已支持运行中切模式（/mode 接口），不再需要重启服务。
# 2026-11-20：/mode 需要运维令牌（设备 /data/ai_cpe/secrets/ops_token）。
TOK=$(cat /data/ai_cpe/secrets/ops_token 2>/dev/null) || { echo "缺少 /data/ai_cpe/secrets/ops_token"; exit 2; }
OUT=$(curl -s -m 8 -w '\n%{http_code}' -X POST http://127.0.0.1:8099/mode -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d '{"mode":"local"}') || true
CODE=$(printf '%s\n' "$OUT" | tail -n 1)
printf '%s\n' "$OUT" | sed '$d'
[ "$CODE" = 200 ] || { echo "切换失败（HTTP ${CODE:-无响应}）"; exit 1; }
printf "%s\n" "已切到本地模式（MT200 deepseek-7b-npu）。Hermes 本体配置未改动。"
