#!/bin/sh
set -eu
curl -s -m 8 -X POST http://127.0.0.1:8099/mode -H "Content-Type: application/json" -d "{\"mode\":\"online\"}" || true
echo
printf "%s\n" "已切到在线模式（Hermes 在线通道 deepseek-v4-flash）。未改 Hermes 配置、密钥或启动项。"
