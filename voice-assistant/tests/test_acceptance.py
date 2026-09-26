# -*- coding: utf-8 -*-
"""RG660MK 智能音箱优化 —— 离线验收测试(T01-T10)

覆盖设计文档《RG660MK 智能音箱优化设计 V1.0》第 9 章验收用例中
**不依赖真实音频/whisper/联网/Hermes** 的逻辑部分:
  - 路由分类(classify): T01 T02 T03 T04 T05 T06 T10
  - 双超时状态机(handle_turn): T07 T08
  - turn_id 隔离 / 迟到结果丢弃: T09

真实端到端(麦克风->whisper->TTS 播报、真实 wttr.in / Hermes 时延)需在
RG660MK 设备上跑,见报告"需设备实测"部分。

用法: python3 tests/test_acceptance.py
"""
import sys, os, time, threading
_here = os.path.dirname(os.path.abspath(__file__))
# 兼容两种布局: tests/ 与 voice_assistant.py 同级(仓库), 或在 ../voice-assistant/(工作区)
for _cand in (os.path.dirname(_here), os.path.join(_here, "..", "voice-assistant"), os.path.join(_here, "..")):
    if os.path.exists(os.path.join(_cand, "voice_assistant.py")):
        sys.path.insert(0, _cand)
        break
import voice_assistant as va

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("[PASS] " if cond else "[FAIL] ") + name + ("  " + detail if detail else ""))


# ---------- 一个假时钟:让 60 秒超时在毫秒内跑完 ----------
class FakeClock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


# ================= 路由分类测试 (纯逻辑,零副作用) =================
print("\n===== 路由分类 classify() =====")

# T01: 1+1 等于几 -> LOCAL_CALCULATOR,不进 Hermes
intent, slots = va.classify("1+1等于几")
check("T01 1+1等于几 -> LOCAL_CALCULATOR", intent == "LOCAL_CALCULATOR",
      "got=%s expr=%s" % (intent, slots.get("expr")))
check("T01b safe_calc(1+1)==2", va.safe_calc(slots.get("expr", "")) == "2")

# 计算器补充:中文口语算式
for q, want in [("三加五等于几", "8"), ("100除以4", "25"),
                ("二十三乘七", None), ("23*7", "161"), ("10减3等于多少", "7")]:
    i, s = va.classify(q)
    if want is None:
        # "二十三"这类多位中文数暂不支持,允许落到 LLM/Hermes,不算失败但记录
        check("CALC %s -> 分类=%s" % (q, i), i in ("LOCAL_CALCULATOR", "LLM_OR_HERMES"), "got=%s" % i)
    else:
        val = va.safe_calc(s.get("expr", "")) if i == "LOCAL_CALCULATOR" else None
        check("CALC %s = %s" % (q, want), i == "LOCAL_CALCULATOR" and val == want,
              "intent=%s val=%s" % (i, val))

# T02: 今天星期几 -> LOCAL_DATE_TIME
intent, _ = va.classify("今天星期几")
check("T02 今天星期几 -> LOCAL_DATE_TIME", intent == "LOCAL_DATE_TIME", "got=%s" % intent)

# T03: 今天上海什么天气 -> WEATHER (关键!"今天"不得误命中时间)
intent, slots = va.classify("今天上海什么天气")
check("T03 今天上海什么天气 -> WEATHER", intent == "WEATHER", "got=%s" % intent)
check("T03b 提取城市=上海", slots.get("city") == "上海", "got=%s" % slots.get("city"))

# T04: 北京明天会下雨吗 -> WEATHER
intent, slots = va.classify("北京明天会下雨吗")
check("T04 北京明天会下雨吗 -> WEATHER", intent == "WEATHER", "got=%s" % intent)
check("T04b 提取城市=北京", slots.get("city") == "北京", "got=%s" % slots.get("city"))

# T05: 相对论是怎么回事 -> 交给 Hermes (LLM_OR_HERMES)
intent, _ = va.classify("相对论是怎么回事")
check("T05 相对论 -> LLM_OR_HERMES(兜底Hermes)", intent == "LLM_OR_HERMES", "got=%s" % intent)

# T06: 给我把灯打开 -> DEVICE_CONTROL
intent, slots = va.classify("给我把灯打开")
check("T06 把灯打开 -> DEVICE_CONTROL/on", intent == "DEVICE_CONTROL" and slots.get("action") == "on",
      "got=%s %s" % (intent, slots))

# T10: 极短口令 开灯 -> DEVICE_CONTROL (ASR 已得有效文本的前提下)
intent, slots = va.classify("开灯")
check("T10 开灯 -> DEVICE_CONTROL/on", intent == "DEVICE_CONTROL" and slots.get("action") == "on",
      "got=%s %s" % (intent, slots))

# 反例:优先级验证 —— 天气必须赢过时间
intent, _ = va.classify("现在几点天气")  # 同时含"现在/几点"(时间) 和 "天气"
check("PRIO 天气优先于时间", intent == "WEATHER", "got=%s" % intent)


# ================= 双超时状态机 handle_turn() =================
print("\n===== 双超时 handle_turn() =====")

# T07: 模拟 Hermes 35 秒返回 —— 30 秒说"请稍等。",35 秒正常回答
def run_T07():
    clk = FakeClock()
    spoken = []
    released = threading.Event()   # handler 阻塞直到时钟到 35s 才放行
    def handler(text, turn):
        released.wait(timeout=5)
        return "相对论的简洁回答"
    def tts(t):
        spoken.append((round(clk(), 1), t))
    def fake_sleep(dt):
        clk.advance(1.0)  # 每次 poll 推进 1 秒(假时钟)
        if clk() >= 35 and not released.is_set():
            released.set()
            # 让后台 handler 先跑完(设 turn.done),再回到主循环
            time.sleep(0.02)
    turn = va.handle_turn("相对论是怎么回事", handler, tts,
                          clock=clk, sleep=fake_sleep, poll=0.2)
    return turn, spoken

turn, spoken = run_T07()
said_texts = [t for _, t in spoken]
check("T07 播报了'请稍等。'", va.WAIT_PROMPT_TEXT in said_texts, "spoken=%s" % spoken)
check("T07 最终播报了结果(35s)", "相对论的简洁回答" in said_texts, "spoken=%s" % spoken)
check("T07 未播报'我还没学会'(未到60s)", va.GIVEUP_TEXT not in said_texts)
check("T07 turn 未 expired", not turn.expired)

# T08: 模拟 Hermes 70 秒返回 —— 30 秒请稍等;60 秒说未学会;70 秒迟到结果不得播放
late_played = {"v": False}
def run_T08():
    clk = FakeClock()
    spoken = []
    def handler(text, turn):
        while clk() < 70:
            pass
        # 70 秒才产出;此时应已 expired,结果必须被丢弃
        return "迟到70秒的答案"
    def tts(t):
        spoken.append((round(clk(), 1), t))
        if t == "迟到70秒的答案":
            late_played["v"] = True
    def fake_sleep(dt):
        clk.advance(1.0)
    turn = va.handle_turn("模拟70秒", handler, tts,
                          clock=clk, sleep=fake_sleep, poll=0.2)
    # 由于 handler 在后台忙等到 70s,handle_turn 在 60s 已 return;
    # 再等后台线程真正跑完,验证迟到结果不会被播
    import time as _t
    _t.sleep(0.05)
    return turn, spoken

turn8, spoken8 = run_T08()
said8 = [t for _, t in spoken8]
check("T08 播报了'请稍等。'(30s)", va.WAIT_PROMPT_TEXT in said8, "spoken=%s" % spoken8)
check("T08 播报了'我还没有学会这个问题。'(60s)", va.GIVEUP_TEXT in said8, "spoken=%s" % spoken8)
check("T08 turn 已 expired", turn8.expired)
# 迟到结果丢弃:accept_result 对已 expired 的 turn 必须拒绝
check("T08 70秒迟到结果被 accept_result 拒绝",
      va.accept_result(turn8, "迟到70秒的答案") is False)


# ================= turn_id 隔离 / 防串话 (T09) =================
print("\n===== turn_id 隔离 / 防串话 (T09) =====")

# 连续三轮:天气 -> 相对论 -> 1+1,每轮 turn_id 递增且互不串
ids = []
def quick_handler(text, turn):
    return "reply-for:" + text
def noop_tts(t):
    pass
for q in ["今天上海什么天气", "相对论是怎么回事", "1+1等于几"]:
    t = va.handle_turn(q, quick_handler, noop_tts, clock=time.monotonic)
    ids.append(t.id)
check("T09 三轮 turn_id 严格递增", ids[0] < ids[1] < ids[2], "ids=%s" % ids)

# 迟到结果错位:旧轮结果在新轮开始后必须被丢弃
import time as _time
old = va.new_turn("旧问题:相对论", _time.monotonic)
new = va.new_turn("新问题:1+1", _time.monotonic)   # 新轮开启 => current 切到 new
check("T09 旧轮迟到结果被丢弃(current 已切新轮)",
      va.accept_result(old, "相对论旧答案") is False)
check("T09 新轮结果可正常接受",
      va.accept_result(new, "2") is True)


# ================= 汇总 =================
print("\n===== 汇总 =====")
print("PASS: %d   FAIL: %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED:")
    for n in FAIL:
        print("  - " + n)
    sys.exit(1)
print("ALL GREEN")
sys.exit(0)
