#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""设备端 新疆口音优化 部署自测。不录音/不联网/不播报，纯逻辑层验证。"""
import sys, os
for p in (os.getcwd(), "/data/ai_cpe"):
    if p not in sys.path:
        sys.path.insert(0, p)

import accent_correct as ac
import voice_assistant as va

fails = []
def ck(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (("  " + detail) if detail else ""))
    if not cond:
        fails.append(name)

print("== 1. 口音纠错专项 correct_command() ==")
cases = [("打开客厅登", "打开客厅灯"), ("关闭客厅登", "关闭客厅灯"), ("打开厨房登", "打开厨房灯"),
         ("打开卧市灯", "打开卧室灯"), ("打开卧市登", "打开卧室灯"),
         ("打开窗连", "打开窗帘"), ("关闭空挑", "关闭空调"),
         ("关登", "关灯"), ("关闭登", "关闭灯")]
for wrong, want in cases:
    r = ac.correct_command(wrong)
    ck("%s -> %s" % (wrong, want), r is not None and r["corrected"] == want, repr(r))

print("== 2. classify 端到端：口音控制命令落 DEVICE_CONTROL ==")
for wrong in ["打开客厅登", "关闭客厅登", "打开厨房登", "打开卧市登"]:
    intent, slots = va.classify(wrong)
    ck("%s -> DEVICE_CONTROL" % wrong,
       intent == "DEVICE_CONTROL" and "accent_fix" in slots,
       "%s tool=%s action=%s" % (intent, slots.get("tool"), slots.get("action")))

print("== 3. 标准普通话回归（不退化） ==")
for text, want in [("今天上海什么天气", "WEATHER"), ("今天星期几", "LOCAL_DATE_TIME"),
                   ("1+1等于几", "LOCAL_CALCULATOR"), ("相对论是怎么回事", "LLM_OR_HERMES"),
                   ("给我把灯打开", "DEVICE_CONTROL"), ("开灯", "DEVICE_CONTROL"),
                   ("关灯", "DEVICE_CONTROL")]:
    intent, slots = va.classify(text)
    ck("%s -> %s" % (text, want), intent == want, intent)
_, slots = va.classify("给我把灯打开")
ck("标准命令不带 accent_fix 溯源", "accent_fix" not in slots)

print("== 4. 新疆地名天气 ==")
for city in ["乌鲁木齐", "喀什", "伊犁", "克拉玛依", "吐鲁番", "库尔勒", "哈密"]:
    intent, slots = va.classify(city + "今天天气怎么样")
    ck("%s 天气" % city, intent == "WEATHER" and slots.get("city") == city,
       "%s %s" % (intent, slots.get("city")))

print("== 5. 误纠红线（开放域不得变设备命令） ==")
for t in ["相对论是怎么回事", "讲个笑话", "我想听音乐", "给妈妈打电话", "明天要开会", "今天心情不好"]:
    intent, _ = va.classify(t)
    ck("%s 不落 DEVICE_CONTROL" % t, intent != "DEVICE_CONTROL", intent)

print("== 6. Sep16-18 功能保留（不回归） ==")
intent, slots = va.classify("移远通信的股价")
ck("移远通信的股价 -> STOCK_QUOTE", intent == "STOCK_QUOTE", intent)

print("== 7. 热词偏置 ==")
hot = ac.get_hotwords()
ck("热词含新疆地名", ("乌鲁木齐" in hot) and ("喀什" in hot))
p = ac.whisper_prompt()
ck("whisper_prompt 可构造", len(p) > 0, "len=%d chars" % len(p))

print("== 8. 唤醒词剥离/循环收敛（2026-09-20 晚修复） ==")
strip_cases = [
    ("你好小皮", ""),
    ("你好下皮你好下皮你好下皮", ""),
    ("你好下题你好下题", ""),
    ("小皮", ""),
    ("你好下皮打开客厅灯", "打开客厅灯"),
    ("你好小皮，打开卧室灯", "打开卧室灯"),
    ("给我把灯打开", "给我把灯打开"),
]
for raw, want in strip_cases:
    got = va.strip_wake(raw)
    ck("strip_wake %r -> %r" % (raw[:16], want), got == want, repr(got))
ck("is_wake 你好下题(裸你好)", va.is_wake("你好下题"))
ck("is_wake 小皮", va.is_wake("小皮"))
ck("非唤醒句不误判", not va.is_wake("灯光开一下"))
ck("热词prompt默认关闭", va.ASR_HOTWORD_PROMPT is False)

print()
if fails:
    print("DEVICE SELFTEST: FAIL=%d -> %s" % (len(fails), fails))
    sys.exit(1)
print("DEVICE SELFTEST: ALL PASS")
sys.exit(0)
