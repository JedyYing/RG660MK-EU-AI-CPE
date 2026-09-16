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
import re, threading, itertools

os.environ.setdefault("HOME", "/data/hermes/home")

# 真 VAD 语音端点检测（流式、说完即停）。vad.py 与本脚本同目录；
# 缺失时回退到固定时长录音 record()，保证降级环境也能跑。
try:
    import vad
except ImportError:
    vad = None

# ---- 路径与设备 ----
WHISPER = "/data/ai_cpe/whisper/whisper-cli"
WHISPER_LIB = "/data/ai_cpe/whisper/lib"
MODEL = "/data/ai_cpe/whisper/models/ggml-base.bin"
MPG123 = "/data/ai_cpe/bin/mpg123"
BULB = "/data/ai_cpe/bulb_control.py"
SNAPSHOT = "/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot"
VISION = "/data/ai_cpe/vision_control.py"
PIPELINE = "/data/ai_cpe/hermes/home/photo_pipeline.py"
FACE_RECOG = "/data/ai_cpe/face_recognize.py"
REC_DEV = os.environ.get("REC_DEV", "plughw:2,0")
PLAY_DEV = os.environ.get("PLAY_DEV", "plughw:2,0")

# ---- LLM ----
LLM_URL = "https://qlitellm.phicotek.com/v1/chat/completions"
LLM_MODEL = os.environ.get("VOICE_LLM_MODEL", "deepseek-v4-flash")
VOICE = "zh-CN-XiaoxiaoNeural"

# ---- 参数 ----
RATE = 48000
LISTEN_SECS = 5
RMS_THRESHOLD = 200
ACTIVE_TIMEOUT = 24
WAKE_WORDS = ["小皮", "下皮", "小屁", "下屁", "小批", "小披", "小pipi", "你好小皮", "小皮皮"]
EXIT_WORDS = ["休息", "睡觉", "退下", "再见", "拜拜", "晚安"]

# ---- 双超时(设计文档 §4)----
WAIT_PROMPT_SECS = 30      # 30 秒仍无结果 -> 先播"请稍等。"
HARD_TIMEOUT_SECS = 60     # 60 秒仍无结果 -> 终止本轮并播"我还没有学会这个问题。"
WAIT_PROMPT_TEXT = "请稍等。"
GIVEUP_TEXT = "我还没有学会这个问题。"

TIME_PATTERN = re.compile(r"^(?:请问|告诉我|帮我查一下)?(?:今天|现在)?(?:是)?(?:几点(?:钟)?(?:了)?|几月几[日号]|几号|星期几|周几|什么日期|什么时间|日期|时间)(?:了|呢|呀|啊|吗)?$")
WEATHER_KEYS = ["天气", "气温", "温度", "下雨", "下雪", "几度", "冷不冷", "热不热"]

SYSTEM_PROMPT = ("你是小皮，智能音箱助手。回答供语音播报，只用两三句中文，不超过100字，不用标题、列表或Markdown。"
                 "查天气、控制灯泡、拍照、人脸检测、坐姿检测时务必调用对应工具获取真实信息。"
                 "注意：语音转写可能不准（如'人脸'可能被听成'冷凉/人年/人联'，'坐姿'可能被听成'作子/坐支'），"
                 "遇到'XX检测/检查'类命令时，优先判断用户是要人脸检测还是坐姿检测，调用 detect_face 或 detect_posture 工具。")

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
    {"type": "function", "function": {
        "name": "detect_face",
        "description": "打开摄像头做人脸检测，判断画面里是否有人脸",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "detect_posture",
        "description": "打开摄像头做坐姿检测，分析坐姿是否良好",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]


def load_api_key():
    try:
        for line in open("/data/hermes/.hermes/.env"):
            line = line.strip()
            if line.startswith("PHICOTEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        # 设备外(如本机离线测试)无 .env 时不崩,允许模块被 import
        pass
    return ""

API_KEY = load_api_key()


def record(secs, path="/tmp/voice_rec.wav"):
    if os.path.exists(path):
        os.unlink(path)
    subprocess.run(["arecord", "-D", REC_DEV, "-f", "S16_LE", "-r", str(RATE),
                    "-c", "1", "-d", str(secs), "-q", path],
                   check=True, stderr=subprocess.DEVNULL, timeout=secs + 5)
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


def vad_record(path="/tmp/voice_rec.wav", max_s=None, start_wait_s=None,
               end_sil_ms=None):
    """用真 VAD 录一句：流式端点检测，说完即停。返回 True=录到语音。
    vad 缺失时回退到固定 5 秒录音 + RMS 门限（兼容降级环境）。"""
    if vad is None:
        record(LISTEN_SECS, path)
        return rms(path) >= RMS_THRESHOLD
    got = vad.record_utterance(
        path, rate=RATE, dev=REC_DEV,
        max_s=max_s if max_s is not None else vad.MAX_UTTER_S,
        start_wait_s=start_wait_s if start_wait_s is not None else vad.START_WAIT_S,
        end_sil_ms=end_sil_ms if end_sil_ms is not None else vad.END_SIL_MS,
        gain=4.0)
    if got:
        boost(path)
    return got


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
        if r.returncode != 0:
            print("[ASR] failed rc=%s" % r.returncode, flush=True)
            return ""
        lines = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(("whisper_", "main:", "system_info:", "read_audio",
                                "whisper_print", "ggml_", "CPU", "processing",
                                "[BLANK_AUDIO]")):
                continue
            # 括号里的音效/字幕是非语音标注，不能当成唤醒词或命令。
            if re.fullmatch(r"[（(\[].*[）)\]]", line):
                continue
            lines.append(line)
        return to_simplified("".join(lines))
    except Exception as e:
        print("[ASR] error=%s" % type(e).__name__, flush=True)
        return ""


def get_time_now():
    now = datetime.datetime.now()
    return "现在是 %d月%d日 %s%d点%d分，星期%s" % (
        now.month, now.day,
        "下午" if now.hour >= 12 else "上午",
        now.hour % 12 or 12, now.minute,
        ["一", "二", "三", "四", "五", "六", "日"][now.weekday()])


# ---- 本地计算器(设计文档 §3.1: 安全表达式解析,禁止 eval)----
_CN_NUM = {"零": "0", "〇": "0", "一": "1", "二": "2", "两": "2", "三": "3",
           "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
_CN_OP = {"加": "+", "减": "-", "乘": "*", "除以": "/", "除": "/",
          "×": "*", "÷": "/", "＋": "+", "－": "-", "x": "*", "X": "*"}


def _normalize_calc(text):
    """把中文算式口语归一成 ASCII 算术串。仅保留数字与 + - * / ( ) 和小数点。"""
    # 已观察到的 ASR 尾音误识别，仅在后续整串通过算式白名单时生效。
    t = re.sub(r"等一[節节]$", "等于几", text)
    for k, v in _CN_OP.items():
        t = t.replace(k, v)
    for k, v in _CN_NUM.items():
        t = t.replace(k, v)
    # 去掉"等于几/是多少/等于多少/得多少/结果"等尾巴与空白
    t = re.sub(r"(?:等[於于]?|=|得)(?:多少|几|是多少)?$|(?:是多少|多少|结果)$", "", t)
    t = re.sub(r"^(?:请问|帮我算|算一下|计算)", "", t)
    t = t.replace(" ", "").replace("（", "(").replace("）", ")")
    return t


def is_calc(text):
    """判断是否为纯算术问题。命中返回归一化表达式,否则 None。"""
    norm = _normalize_calc(text)
    # 必须含运算符,且整体只由 数字/运算符/括号/小数点 组成
    if not re.search(r"[+\-*/]", norm):
        return None
    if not re.fullmatch(r"[0-9+\-*/().]+", norm):
        return None
    if not re.search(r"\d", norm):
        return None
    return norm


def safe_calc(expr):
    """用 AST 白名单求值,绝不 eval 用户输入。返回口语化结果字符串或 None。"""
    import ast, operator
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}

    def _ev(node):
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("bad const")
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](_ev(node.left), _ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](_ev(node.operand))
        raise ValueError("unsupported")

    try:
        tree = ast.parse(expr, mode="eval")
        val = _ev(tree)
        if isinstance(val, float):
            val = round(val, 6)
            if val == int(val):
                val = int(val)
        return "%s" % val
    except Exception:
        return None


def get_weather_now(city):
    """优先原天气服务；失败时通过独立 HTTPS 服务查询，始终校验证书。"""
    try:
        url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1&lang=zh"
        with urllib.request.urlopen(url, timeout=8) as response:
            cur = json.load(response)["current_condition"][0]
        desc = (cur.get("lang_zh") or cur["weatherDesc"])[0]["value"]
        return "%s现在%s，气温%s度，体感%s度，湿度百分之%s" % (
            city, desc, cur["temp_C"], cur["FeelsLikeC"], cur["humidity"])
    except Exception as e:
        print("[WEATHER] primary failed: %s" % e, flush=True)
    coordinates = {"上海": (31.23, 121.47), "北京": (39.90, 116.41)}
    if city in coordinates:
        lat, lon = coordinates[city]
    else:
        url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
            {"name": city, "count": 5, "language": "zh"})
        with urllib.request.urlopen(url, timeout=8) as response:
            places = json.load(response).get("results", [])
        place = next((p for p in places if p.get("country_code") in ("CN", "TW", "HK", "MO")), None)
        if place is None:
            return "没有查到这个城市的天气位置，请换一个城市名。"
        lat, lon = place["latitude"], place["longitude"]
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code",
        "timezone": "Asia/Shanghai"})
    with urllib.request.urlopen(url, timeout=10) as response:
        cur = json.load(response)["current"]
    descriptions = {0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "有雾", 48: "有雾凇",
                    51: "小毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
                    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
                    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒", 80: "阵雨", 81: "阵雨",
                    82: "强阵雨", 85: "阵雪", 86: "强阵雪", 95: "雷雨", 96: "雷雨伴冰雹", 99: "雷雨伴冰雹"}
    code = cur["weather_code"]
    values = [cur[k] for k in ("temperature_2m", "apparent_temperature", "relative_humidity_2m")]
    if code not in descriptions or any(v is None for v in values):
        raise ValueError("incomplete weather data")
    print("[WEATHER] source=open-meteo city=%s time=%s" % (city, cur["time"]), flush=True)
    return "%s现在%s，气温%s度，体感%s度，湿度百分之%s" % (
        city, descriptions[code], *values)


def bulb_reply(action, result):
    """把现有灯控 CLI 输出转成可播报结果；不将空输出视为成功。"""
    output = (result.stdout or result.stderr or "").strip()
    payload = None
    start = output.find("{")
    if start >= 0:
        try:
            payload = json.loads(output[start:])
        except (ValueError, TypeError):
            pass
    if not isinstance(payload, dict):
        payload = {}
    if "device is offline" in output.lower():
        return "灯泡离线了，请检查灯泡电源和网络连接。"
    if result.returncode != 0 or payload.get("success") is False:
        print("[BULB] failed rc=%s code=%s" % (result.returncode, payload.get("code")), flush=True)
        return "灯泡控制失败，请稍后再试。"
    if action == "status":
        if payload.get("success") is True and isinstance(payload.get("result"), list):
            for item in payload["result"]:
                if isinstance(item, dict) and item.get("code") == "switch_led" and isinstance(item.get("value"), bool):
                    return "灯泡当前是开着的。" if item["value"] else "灯泡当前是关着的。"
        return "暂时无法确认灯泡的开关状态。"
    # 兼容已有 CLI 的明确成功标记，不声称已确认物理状态。
    if "已发送开灯指令" in output:
        return "已发送开灯指令。"
    if "已发送关灯指令" in output:
        return "已发送关灯指令。"
    return "没有收到灯泡的有效确认，请稍后再试。"


def execute_tool(name, args):
    try:
        if name == "get_weather":
            return get_weather_now(args.get("city", "上海"))
        elif name == "control_bulb":
            action = args.get("action", "status")
            r = subprocess.run(["python3", BULB, action], capture_output=True, text=True, timeout=30)
            return bulb_reply(action, r)
        elif name == "take_photo":
            r = subprocess.run([SNAPSHOT], capture_output=True, text=True, timeout=30)
            return "拍照完成，" + (r.stdout or "").strip()
        elif name == "detect_face":
            r = subprocess.run(["python3", VISION, "face"], capture_output=True, text=True, timeout=60)
            return (r.stdout or r.stderr).strip() or "人脸检测完成"
        elif name == "detect_posture":
            r = subprocess.run(["python3", VISION, "posture"], capture_output=True, text=True, timeout=60)
            return (r.stdout or r.stderr).strip() or "坐姿检测完成"
        elif name == "photo_upload":
            r = subprocess.run(["python3", PIPELINE], capture_output=True, text=True, timeout=90)
            out = (r.stdout or r.stderr).strip()
            if r.returncode == 0 and "[FAIL]" not in out:
                return "拍照并上传到服务器完成"
            return "拍照上传失败"
        elif name == "face_recognize":
            r = subprocess.run(["python3", FACE_RECOG], capture_output=True, text=True, timeout=90)
            return (r.stdout or r.stderr).strip() or "人脸识别完成"
    except Exception as e:
        print("[TOOL_ERROR] tool=%s error=%s" % (name, e), flush=True)
        if name == "get_weather":
            return "天气服务暂时连接失败，请稍后再试。"
        return "设备操作失败，请稍后再试。"
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
        return (msg.get("content") or "").strip(), False
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
                            text + "（请用两三句话简洁回答）", "-Q", "-m", LLM_MODEL],
                           capture_output=True, text=True, env=env, timeout=180)
        output = r.stdout.strip()
        if r.returncode != 0 or re.search(r"HTTP [45]\d\d|Invalid model|Traceback", output, re.I):
            print("[HERMES] failed rc=%s" % r.returncode, flush=True)
            return "问答服务暂时不可用，请稍后再试。"
        return output or "问答服务暂时没有返回结果，请稍后再试。"
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
        text = "回复内容暂时无法播报，请稍后再试。"
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


# 仅识别句首完整称呼；保留“你好”的兼容入口，去掉单字误触发。
_WAKE_PREFIX = re.compile(r"^(?:你好[，,、\s]*(?:(?:小皮皮|小pipi|小皮|下皮|小屁|下屁|小批|小披|夏丁|下爹|夏皮))?|小皮皮|小pipi|小皮|下皮|小屁|下屁|小批|小披)[，,。.!！?？\s]*", re.I)


def is_wake(text):
    return bool(_WAKE_PREFIX.match(text.strip()))


def strip_wake(text):
    return _WAKE_PREFIX.sub("", text.strip(), count=1).strip("，,。.!！?？ ")


CITIES = ["上海", "北京", "广州", "深圳", "杭州", "南京", "成都", "重庆", "武汉",
          "西安", "天津", "苏州", "长沙", "郑州", "青岛", "沈阳", "大连", "厦门",
          "福州", "合肥", "昆明", "哈尔滨", "济南", "宁波", "无锡", "香港", "澳门", "台北"]


def extract_city(text):
    for c in CITIES:
        if c in text:
            return c
    return None


def classify(text):
    """纯逻辑意图分类(无副作用,可离线测试)。返回 (intent, slots)。
    优先级(设计文档 §2.2): DEVICE_CONTROL > WEATHER > LOCAL_DATE_TIME > LOCAL_CALCULATOR > (LLM兜底) > HERMES
    关键: WEATHER 必须先于 LOCAL_DATE_TIME,否则"今天上海什么天气"里的"今天"会误命中时间。
    """
    text = to_simplified(text).strip().strip("，,。!！?？ ")
    if not text:
        return "EMPTY", {}
    # 1. 设备控制(最高优先级): 灯泡 / 拍照 / 检测
    if "灯" in text:
        action = "on" if "开" in text else ("off" if "关" in text else "status")
        return "DEVICE_CONTROL", {"tool": "control_bulb", "action": action}
    if any(k in text for k in ["拍照", "照相", "拍一张", "拍个照", "拍个照片"]):
        if any(k in text for k in ["上传", "传到", "服务器", "immich", "相册", "同步", "保存"]):
            return "DEVICE_CONTROL", {"tool": "photo_upload"}
        return "DEVICE_CONTROL", {"tool": "take_photo"}
    if any(k in text for k in ["坐姿", "姿势", "体态"]):
        return "DEVICE_CONTROL", {"tool": "detect_posture"}
    if any(k in text for k in ["识别", "是谁", "谁在", "脸识别"]):
        return "DEVICE_CONTROL", {"tool": "face_recognize"}
    if any(k in text for k in ["人脸", "脸检测"]):
        return "DEVICE_CONTROL", {"tool": "detect_face"}
    if any(k in text for k in ["检测", "檢查", "检查"]):
        return "DEVICE_CONTROL", {"tool": "detect_face"}
    # 2. 天气/实时(必须先于时间,处理"今天...天气"的关键词重叠)
    if any(k in text for k in WEATHER_KEYS):
        return "WEATHER", {"city": extract_city(text)}
    # 3. 时间/日期
    if TIME_PATTERN.fullmatch(text):
        return "LOCAL_DATE_TIME", {}
    # 4. 本地计算器
    expr = is_calc(text)
    if expr:
        return "LOCAL_CALCULATOR", {"expr": expr}
    # 5. 交给上层: 先 LLM 函数调用兜底,再 Hermes
    return "LLM_OR_HERMES", {}


def dispatch(intent, slots, text):
    """按分类结果执行(有副作用)。返回 (reply, need_hermes)。"""
    if intent == "EMPTY":
        return "请再说一下，我没听清", False
    if intent == "DEVICE_CONTROL":
        tool = slots["tool"]
        if tool == "control_bulb":
            return execute_tool("control_bulb", {"action": slots["action"]}), False
        return execute_tool(tool, {}), False
    if intent == "WEATHER":
        city = slots.get("city")
        if not city:
            return "你想查哪个城市的天气呢？", False
        return execute_tool("get_weather", {"city": city}), False
    if intent == "LOCAL_DATE_TIME":
        return get_time_now(), False
    if intent == "LOCAL_CALCULATOR":
        val = safe_calc(slots["expr"])
        if val is not None:
            return "%s" % val, False
        # 解析失败则兜底给 LLM/Hermes
        intent = "LLM_OR_HERMES"
    # LLM 函数调用兜底(抗误听),失败再转 Hermes
    try:
        reply, used_tool = llm_tools(text)
        if reply:
            return reply, False
    except Exception:
        pass
    return None, True


def route(text):
    """分流(保持原签名向后兼容)。返回 (reply, need_hermes)。"""
    intent, slots = classify(text)
    return dispatch(intent, slots, text)


# ======================================================================
# Conversation Manager: turn_id 隔离 + 30/60 秒双超时(设计文档 §4/§5/§7)
# ======================================================================
_turn_seq = itertools.count(1000)
_current_turn_id = None       # 全局"当前有效轮",用于迟到结果丢弃
_turn_lock = threading.Lock()


class Turn:
    """单轮对话状态。每轮全新创建 = 每轮状态重置(§5.1)。"""
    __slots__ = ("id", "text", "start", "wait_prompt_played",
                 "expired", "cancelled", "done", "result")

    def __init__(self, text, clock):
        self.id = next(_turn_seq)
        self.text = text
        self.start = clock()
        self.wait_prompt_played = False
        self.expired = False
        self.cancelled = False
        self.done = False
        self.result = None


def new_turn(text, clock=time.monotonic):
    """开启新一轮:分配 turn_id 并设为当前有效轮(§5:新问题优先,取消旧轮)。"""
    global _current_turn_id
    turn = Turn(text, clock)
    with _turn_lock:
        _current_turn_id = turn.id
    return turn


def accept_result(turn, response):
    """进入 TTS 前的统一校验(§4.1/§5.3):turn_id/expired/cancelled 三态,任一不符即丢弃。"""
    if response is None:
        return False
    if turn.expired or turn.cancelled:
        print("[TURN=%d] DISCARD reason=expired/cancelled" % turn.id, flush=True)
        return False
    with _turn_lock:
        if turn.id != _current_turn_id:
            print("[TURN=%d] DISCARD reason=stale current=%s" % (turn.id, _current_turn_id), flush=True)
            return False
    return True


def handle_turn(text, handler, tts,
                clock=time.monotonic, sleep=time.sleep,
                wait_secs=WAIT_PROMPT_SECS, hard_secs=HARD_TIMEOUT_SECS,
                poll=0.2):
    """最外层轮管理(§7 状态机的非阻塞实现)。
      handler(text, turn) -> reply 字符串 (在后台线程执行,可能耗时/联网/调 Hermes)
      tts(text)          -> 播报
    计时起点 = 进入本函数(对应 ASR 得到最终文本)。返回本轮 Turn 对象(便于测试断言)。
    """
    turn = new_turn(text, clock)
    print("[TURN=%d] ROUTER text=%r" % (turn.id, text), flush=True)

    box = {}
    def _work():
        try:
            box["result"] = handler(text, turn)
        except Exception as e:
            box["error"] = e
        finally:
            turn.done = True

    th = threading.Thread(target=_work, daemon=True)
    th.start()

    while not turn.done:
        elapsed = clock() - turn.start
        if elapsed >= hard_secs:
            # 60 秒:强制结束本轮,迟到结果后续会被 accept_result 丢弃
            turn.expired = True
            turn.cancelled = True
            print("[TURN=%d] TIMEOUT hard=%ds -> giveup" % (turn.id, hard_secs), flush=True)
            tts(GIVEUP_TEXT)
            return turn
        if elapsed >= wait_secs and not turn.wait_prompt_played:
            # 30 秒:仅一次"请稍等。",不标记完成,后台继续
            turn.wait_prompt_played = True
            print("[TURN=%d] WAIT_PROMPT at=%ds" % (turn.id, wait_secs), flush=True)
            tts(WAIT_PROMPT_TEXT)
        sleep(poll)

    # 后台已完成:再次校验才允许进入 TTS
    reply = box.get("result")
    if "error" in box:
        print("[TURN=%d] HANDLER_ERROR %s" % (turn.id, box["error"]), flush=True)
    if accept_result(turn, reply):
        print("[TURN=%d] RESPONSE=%r" % (turn.id, reply), flush=True)
        print("[TURN=%d] TTS_START" % turn.id, flush=True)
        tts(reply)
        print("[TURN=%d] TTS_DONE" % turn.id, flush=True)
    turn.result = reply
    return turn


def _turn_handler(clean, turn):
    """真实业务 handler:本地/天气/计算立即返回;复杂问题走 Hermes。"""
    reply, need_hermes = route(clean)
    if need_hermes:
        # 复杂问题:先不在这里播"请稍等",交给 30 秒超时统一处理
        reply = hermes_ask(clean)
    return reply


def main():
    print("voice_assistant started: rec=%s play=%s vad=%s" % (REC_DEV, PLAY_DEV, vad is not None), flush=True)
    while True:
        # IDLE: VAD 监听唤醒词（流式端点检测，说完即停；无人说话则持续等待）
        # end_sil_ms 调大到 2000ms：唤醒词"你好…小皮"中间有自然停顿，别把它切成两段。
        if not vad_record("/tmp/voice_rec.wav", max_s=8, start_wait_s=86400, end_sil_ms=2000):
            continue
        text = transcribe("/tmp/voice_rec.wav")
        print("[listen] %r" % text, flush=True)
        if is_wake(text):
            print(">>> WAKE", flush=True)
            clean = strip_wake(text)
            if clean:
                handle_turn(clean, _turn_handler, speak)
            else:
                speak("在呢，请说")
            while True:
                # VAD 录一句；ACTIVE_TIMEOUT 内无人说话则自动退出对话
                if not vad_record("/tmp/voice_rec.wav", start_wait_s=ACTIVE_TIMEOUT):
                    speak("好的，先不打扰你了")
                    break
                t = transcribe("/tmp/voice_rec.wav")
                print("[TURN? ] ASR_TEXT=%r" % t, flush=True)
                if not t:
                    speak("请再说一下，我没听清")
                    continue
                if any(w in t for w in EXIT_WORDS):
                    speak("好的，再见")
                    break
                clean = strip_wake(t)
                if not clean:
                    speak("我在听，请说")
                    continue
                try:
                    # 每轮独立: turn_id 隔离 + 30/60 秒双超时统一在 handle_turn 内管理
                    handle_turn(clean, _turn_handler, speak)
                except Exception as e:
                    print("route error:", e, flush=True)
                    speak("我走神了，再说一次")


if __name__ == "__main__":
    main()
