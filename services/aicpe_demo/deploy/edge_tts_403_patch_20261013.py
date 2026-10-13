#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""edge_tts_403_patch_20261013.py — edge-tts 2026-10 端点变更修补（幂等）

背景：2026-10 起微软停用旧合成端点（wss://speech.platform.bing.com/.../edge/v1），
合成请求一律 403（'Invalid response status'），全球波及；见 rany2/edge-tts #490 / PR #491。
症状：/data/ai_cpe/tts_say.py 报 403；演示"AI 智能语音对话"的 TTS 子项失败（整体 6/7）。

修法（PR #491 同款）：合成改走 Edge 155 新端点，凭据参数换名：
  wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1?Ocp-Apim-Subscription-Key=<原TrustedClientToken>

注意：
  - 音色列表仍走旧端点（不在本补丁范围）
  - pip 升级/重装 edge-tts 会覆盖此补丁 → 重新执行本脚本即可
  - 原文件自动备份为 constants.py.bak_20261013
用法（设备侧）：
  /data/hermes/venv/bin/python edge_tts_403_patch_20261013.py
  验证： /data/hermes/venv/bin/python /data/ai_cpe/tts_say.py "测试一二三"
"""
import glob
import sys

CANDS = glob.glob("/data/hermes/venv/lib/python3*/site-packages/edge_tts/constants.py")
if not CANDS:
    print("未找到 edge_tts/constants.py")
    sys.exit(2)
p = CANDS[0]
s = open(p, encoding="utf-8").read()

if "api.msedgeservices.com" in s:
    print("已打过补丁，无需处理:", p)
    sys.exit(0)

OLD = 'WSS_URL = f"wss://{BASE_URL}/edge/v1?TrustedClientToken={TRUSTED_CLIENT_TOKEN}"'
if OLD not in s:
    print("未找到预期旧行（可能 edge-tts 版本不同），请人工检查:", p)
    sys.exit(3)

NEW = ("# 2026-10: 旧端点停用(403)；改走 Edge 155 新端点（凭据改用 Ocp-Apim-Subscription-Key）\n"
       "WSS_URL = (\n"
       '    "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"\n'
       '    f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}"\n'
       ")")

open(p + ".bak_20261013", "w", encoding="utf-8").write(s)
open(p, "w", encoding="utf-8").write(s.replace(OLD, NEW))
print("patched:", p)
