#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""RG660MK 智能音箱语音对话助手（三路分流）
唤醒"你好小皮" -> 录音(绿联mic) -> whisper ASR -> 分流 -> TTS(3.5mm耳机口)
分流:
  1. 时间/日期    -> 本地即刻回答（不调 LLM/Hermes）
  2. 天气/灯泡/拍照 -> LLM function calling 工具查询，快速播报
  3. 复杂问题      -> 投递 Hermes，先播"请稍等"
"""
import os, subprocess, sys, time, math, wave, struct, json, urllib.request, urllib.parse, asyncio, datetime

os.environ.setdefault("HOME", "/data/hermes/home")

# ---- 路径与设备 ----
WHISPER = "/data/ai_cpe/whisper/whisper-cli"
WHISPER_LIB = "/data/ai_cpe/whisper/lib"
MODEL = "/data/ai_cpe/whisper/models/ggml-base.bin"
MPG123 = "/data/ai_cpe/bin/mpg123"
BULB = "/data/ai_cpe/bulb_control.py"
SNAPSHOT = "/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot"
REC_DEV = os.environ.get("REC_DEV", "plughw:2,0")
PLAY_DEV = os.environ.get("PLAY_DEV", "plughw:2,0")

# ---- LLM ----
LLM_URL = "https://qlitellm.phicotek.com/v1/chat/completions"
LLM_MODEL = "deepseek-v4-pro"
VOICE = "zh-CN-XiaoxiaoNeural"

# ---- 参数 ----
RATE = 48000
LISTEN_SECS = 5
RMS_THRESHOLD = 200
ACTIVE_TIMEOUT = 24
WAKE_WORDS = ["小皮", "下皮", "小屁", "下屁", "小批", "小披", "小pipi", "你好小皮", "小皮皮"]
EXIT_WORDS = ["休息", "睡觉", "退下", "再见", "拜拜", "晚安"]

TIME_KEYS = ["几点", "时间", "日期", "几号", "星期", "今天", "现在", "什么时候"]

SYSTEM_PROMPT = ("你是小皮，智能音箱助手。回答简洁口语化。"
                 "查天气、控制灯泡、拍照时务必调用对应工具获取真实信息。")

TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "查询指定城市的实时天气",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string", "description": "城市名"}},
                       "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "control_bulb",
        "description": "控制智能灯泡开关",
        "parameters": {"type": "object",
                       "properties": {"action": {"type": "string", "enum": ["on", "off", "toggle", "status"]}},
                       "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "take_photo",
        "description": "用摄像头拍照",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]


def load_api_key():
    for line in open("/data/hermes/.hermes/.env"):
        line = line.strip()
        if line.startswith("PHICOTEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""

API_KEY = load_api_key()


def record(secs, path="/tmp/voice_rec.wav"):
    subprocess.run(["arecord", "-D", REC_DEV, "-f", "S16_LE", "-r", str(RATE),
                    "-c", "1", "-d", str(secs), "-q", path],
                   check=False, stderr=subprocess.DEVNULL)
    boost(path)


def boost(path, factor=4.0):
    try:
        w = wave.open(path)
        n = w.getnframes()
        if n == 0:
            w.close(); return
        data = w.readframes(n)
        w.close()
        a = struct.unpack("<%dh" % n, data[:n * 2])
        a = [max(-32767, min(32767, int(x * factor))) for x in a]
        w = wave.open(path, "w")
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % n, *a))
        w.close()
    except Exception:
        pass


def rms(path):
    try:
        w = wave.open(path)
        n = w.getnframes()
        if n == 0:
            w.close(); return 0
        d = w.readframes(n)
        w.close()
        a = struct.unpack("<%dh" % n, d[:n * 2])
        return int(math.sqrt(sum(x * x for x in a) / n))
    except Exception:
        return 0


FAN2JIAN = str.maketrans({
    "氣": "气", "溫": "温", "間": "间", "點": "点", "號": "号", "幾": "几",
    "時": "时", "現": "现", "樣": "样", "麼": "么", "嗎": "吗", "開": "开",
    "關": "关", "燈": "灯", "請": "请", "說": "说", "話": "话", "問": "问",
    "來": "来", "們": "们", "個": "个", "這": "这", "會": "会", "為": "为",
    "還": "还", "裡": "里", "後": "后", "對": "对", "過": "过", "電": "电",
    "腦": "脑", "網": "网", "攝": "摄", "頭": "头", "麼": "么", "瞭": "了",
    "沒": "没", "國": "国", "學": "学", "發": "发", "車": "车", "馬": "马",
    "風": "风", "雲": "云", "雨": "雨", "陽": "阳", "陰": "阴", "熱": "热",
    "冷": "冷", "乾": "干", "濕": "湿", "風": "风", "龍": "龙", "鳥": "鸟",
})


def to_simplified(text):
    return text.translate(FAN2JIAN)


def transcribe(path):
    env = dict(os.environ, LD_LIBRARY_PATH=WHISPER_LIB)
    try:
        r = subprocess.run([WHISPER, "-m", MODEL, "-f", path, "-l", "zh",
                            "--no-timestamps", "-np"],
                           capture_output=True, text=True, env=env, timeout=90)
        for line in reversed(r.stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("whisper_", "main:", "system_info:", "read_audio",
                                "whisper_print", "ggml_", "CPU", "processing")):
                continue
            return to_simplified(line)
        return ""
    except Exception:
        return ""


def get_time_now():
    now = datetime.datetime.now()
    return "现在是 %d月%d日 %s%d点%d分，星期%s" % (
        now.month, now.day,
        "下午" if now.hour >= 12 else "上午",
        now.hour % 12 or 12, now.minute,
        ["一", "二", "三", "四", "五", "六", "日"][now.weekday()])


def execute_tool(name, args):
    try:
        if name == "get_weather":
            city = args.get("city", "上海")
            url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1"
            r = json.loads(urllib.request.urlopen(url, timeout=15).read())
            cur = r["current_condition"][0]
            desc = cur["weatherDesc"][0]["value"]
            return "%s现在%s，气温%s度，体感%s度，湿度百分之%s" % (
                city, desc, cur["temp_C"], cur["FeelsLikeC"], cur["humidity"])
        elif name == "control_bulb":
            action = args.get("action", "status")
            r = subprocess.run(["python3", BULB, action], capture_output=True, text=True, timeout=30)
            return (r.stdout or r.stderr).strip() or "灯泡操作完成"
        elif name == "take_photo":
            r = subprocess.run([SNAPSHOT], capture_output=True, text=True, timeout=30)
            return "拍照完成，" + (r.stdout or "").strip()
    except Exception as e:
        return "工具执行失败: %s" % e
    return "未知工具"


def llm_tools(text):
    """LLM function calling：处理天气/灯泡/拍照。返回 (reply, used_tool)。used_tool=False 表示复杂问题需转 Hermes。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}]
    body = json.dumps({"model": LLM_MODEL, "messages": messages,
                       "tools": TOOLS, "max_tokens": 200})
    req = urllib.request.Request(LLM_URL, data=body.encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + API_KEY})
    r = json.loads(urllib.request.urlopen(req, timeout=40).read())
    msg = r["choices"][0]["message"]
    if not msg.get("tool_calls"):
        return "", False  # 没调工具 => 复杂问题
    # 执行工具
    messages.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": msg["tool_calls"]})
    for tc in msg["tool_calls"]:
        fn = tc["function"]
        args = json.loads(fn.get("arguments") or "{}")
        result = execute_tool(fn["name"], args)
        print("[tool] %s(%s) => %s" % (fn["name"], fn.get("arguments"), result), flush=True)
        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    # 二次调用生成自然语言回复
    body2 = json.dumps({"model": LLM_MODEL, "messages": messages, "max_tokens": 200})
    req2 = urllib.request.Request(LLM_URL, data=body2.encode(),
                                  headers={"Content-Type": "application/json",
                                           "Authorization": "Bearer " + API_KEY})
    r2 = json.loads(urllib.request.urlopen(req2, timeout=40).read())
    return (r2["choices"][0]["message"].get("content") or "").strip(), True


def hermes_ask(text):
    env = dict(os.environ, HOME="/data/hermes/home", HERMES_HOME="/data/hermes/.hermes")
    try:
        r = subprocess.run(["/data/hermes/venv/bin/hermes", "chat", "-q",
                            text + "（请用两三句话简洁回答）", "-Q"],
                           capture_output=True, text=True, env=env, timeout=180)
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return "抱歉，这个问题我想得有点久，你换个说法试试"
    except Exception:
        return ""


def speak(text):
    if not text:
        return
    # 防护：截断超长文本 + 过滤乱码，避免长时间占用音频设备
    text = text.strip()
    if len(text) > 120:
        text = text[:120]
    import re
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    if len(text) > 10 and cn / len(text) < 0.3:
        text = "抱歉，我没听清，请再说一次"
    mp3 = "/tmp/voice_tts.mp3"
    try:
        import edge_tts
        async def _synth():
            await edge_tts.Communicate(text, VOICE).save(mp3)
        asyncio.run(_synth())
        p1 = subprocess.Popen([MPG123, "-q", "-s", "--rate", "48000", mp3],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p2 = subprocess.Popen(["aplay", "-D", PLAY_DEV, "-f", "S16_LE", "-r", "48000",
                               "-c", "1", "-q"], stdin=p1.stdout, stderr=subprocess.DEVNULL)
        p1.stdout.close()
        p2.communicate()
    except Exception as e:
        print("speak error:", e)


def strip_wake(text):
    t = text
    for w in WAKE_WORDS:
        t = t.replace(w, "")
    return t.strip().strip("，,。.!！?？ ")


CITIES = ["上海", "北京", "广州", "深圳", "杭州", "南京", "成都", "重庆", "武汉",
          "西安", "天津", "苏州", "长沙", "郑州", "青岛", "沈阳", "大连", "厦门",
          "福州", "合肥", "昆明", "哈尔滨", "济南", "宁波", "无锡", "香港", "澳门", "台北"]


def extract_city(text):
    for c in CITIES:
        if c in text:
            return c
    return None


def route(text):
    """分流。返回 (reply, need_hermes)。need_hermes=True 表示复杂问题需转 Hermes。"""
    # 1. 时间/日期：本地即刻
    if any(k in text for k in TIME_KEYS):
        return get_time_now(), False
    # 2. 天气：本地关键词 + 城市提取，直接查 wttr.in
    if any(k in text for k in ["天气", "气温", "温度", "下雨", "下雪", "几度", "冷不冷", "热不热"]):
        city = extract_city(text)
        if not city:
            return "你想查哪个城市的天气呢？", False
        return execute_tool("get_weather", {"city": city}), False
    # 3. 灯泡：本地关键词
    if "灯" in text:
        action = "on" if "开" in text else ("off" if "关" in text else "status")
        return execute_tool("control_bulb", {"action": action}), False
    # 4. 拍照：本地关键词
    if any(k in text for k in ["拍照", "照相", "拍一张", "拍个照", "拍个照片"]):
        return execute_tool("take_photo", {}), False
    # 5. 复杂问题：转 Hermes
    return None, True


def main():
    print("voice_assistant started: rec=%s play=%s" % (REC_DEV, PLAY_DEV), flush=True)
    while True:
        # IDLE: 监听唤醒词
        record(LISTEN_SECS)
        if rms("/tmp/voice_rec.wav") < RMS_THRESHOLD:
            continue
        text = transcribe("/tmp/voice_rec.wav")
        print("[listen] %r" % text, flush=True)
        if "你好" in text or any(w in text for w in WAKE_WORDS) or any(c in text for c in ["皮", "吉", "批", "屁", "披"]):
            print(">>> WAKE", flush=True)
            speak("在呢，请说")
            silent_accum = 0
            while True:
                record(LISTEN_SECS)
                if rms("/tmp/voice_rec.wav") < RMS_THRESHOLD:
                    silent_accum += LISTEN_SECS
                    if silent_accum >= ACTIVE_TIMEOUT:
                        speak("我先休息了")
                        break
                    continue
                silent_accum = 0
                t = transcribe("/tmp/voice_rec.wav")
                print("[said] %r" % t, flush=True)
                if not t:
                    continue
                if any(w in t for w in EXIT_WORDS):
                    speak("好的，再见")
                    break
                clean = strip_wake(t)
                if not clean:
                    speak("我在听，请说")
                    continue
                try:
                    reply, need_hermes = route(clean)
                    if need_hermes:
                        speak("请稍等")   # 即时反馈
                        reply = hermes_ask(clean)
                    print("[reply] %r" % reply, flush=True)
                    speak(reply)
                except Exception as e:
                    print("route error:", e, flush=True)
                    speak("我走神了，再说一次")


if __name__ == "__main__":
    main()
