#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唤醒测量（v2）：区分「唤醒」「对话态下听到唤醒词（说明已在对话中）」「未判定的近似唤醒」「噪声」。
用法：python3 wake_rate.py [窗口秒]
"""
import json, os, sys, time

LOG = "/data/ai_cpe/logs/voice.jsonl"
win = int(sys.argv[1]) if len(sys.argv) > 1 else 180
start = time.time() - win
KW = ("小皮", "小屁", "小提", "小披", "下皮", "好小")

rows = []
if os.path.exists(LOG):
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                t = time.mktime(time.strptime(rec.get("ts", ""), "%Y-%m-%d %H:%M:%S"))
            except Exception:
                continue
            if t >= start:
                rows.append(rec)

wake = [r for r in rows if r.get("kind") == "wake"]
wake_ts = {r.get("ts") for r in wake}
turn_kw = [r for r in rows if r.get("kind") in ("turn",) and any(k in (r.get("asr") or "") for k in KW)]
miss_kw = [r for r in rows if r.get("kind") == "miss" and r.get("ts") not in wake_ts
           and any(k in (r.get("asr") or "") for k in KW)]
noise = [r for r in rows if r.get("kind") in ("turn", "miss", "drop", "drop_short")
         and not any(k in (r.get("asr") or "") for k in KW)]

print("窗口: 最近 %d 秒" % win)
print("唤醒成功(wake)            = %d" % len(wake))
print("对话态听到唤醒词(turn)    = %d   ← 已在对话中，不会重复计 wake" % len(turn_kw))
print("听到但未判定(miss)        = %d" % len(miss_kw))
print("噪声回合/丢弃             = %d" % len(noise))
heard = len(wake) + len(turn_kw) + len(miss_kw)
print("「听到唤醒词」总数        = %d；其中判定成功 %d，未判定 %d"
      % (heard, len(wake) + len(turn_kw), len(miss_kw)))
for r in miss_kw[:6]:
    print("  未判定 %s asr=%r" % (r.get("ts"), (r.get("asr") or "")[:26]))
for r in turn_kw[:4]:
    print("  对话态 %s asr=%r" % (r.get("ts"), (r.get("asr") or "")[:26]))
