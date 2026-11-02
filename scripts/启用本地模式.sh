#!/bin/sh
set -eu
# 2026-11-02：看板已支持运行中切模式（/mode 接口），不再需要重启服务。
curl -s -m 8 -X POST http://127.0.0.1:8099/mode -H "Content-Type: application/json" -d "{\"mode\":\"local\"}" || true
echo
printf "%s\n" "已切到本地模式（MT200 deepseek-7b-npu）。Hermes 本体配置未改动。"
