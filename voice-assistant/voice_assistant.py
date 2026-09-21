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
import re, threading, itertools, hashlib, tempfile

os.environ.setdefault("HOME", "/data/hermes/home")

# 新疆口音近音纠错 + 热词偏置（设计《新疆口音识别优化 V1.0》§6.1/§6.3）。
# accent_correct.py 与本脚本同目录；缺失时口音优化整体降级为不启用，主链路照常。
try:
    import accent_correct
except ImportError:
    accent_correct = None

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
ASR_TIMEOUT = 8
# 热词 prompt 开关（whisper --prompt）：默认关。2026-09-20 晚真机 A/B 证据：
# 全量热词 prompt 会把含混的真实语音解码成热词碎片（"你好小皮"→"阿梅，鲁，河田"），
# 且每次转写固定 +2.2s（约基线2倍）。待真实口音命令录音 A/B 通过后再启用（VOICE_ASR_PROMPT=1）。
ASR_HOTWORD_PROMPT = os.environ.get("VOICE_ASR_PROMPT", "0") == "1"

# ---- ASR 引擎（whisper | paraformer）----
# 2026-09-21 新增：Paraformer（sherpa-onnx-offline + glibc 陪跑）作为可切换引擎。
# 默认 whisper 不变；切换：VOICE_ASR_ENGINE=paraformer（详见 麦克风切换与唤醒验证记录_20260921.md）。
ASR_ENGINE = os.environ.get("VOICE_ASR_ENGINE", "whisper")
SHERPA_GLIBC = "/data/ai_cpe/glibc/ld-linux-aarch64.so.1"
SHERPA_LIB = "/data/ai_cpe/glibc"
SHERPA_BIN = "/data/ai_cpe/sherpa-onnx/bin/sherpa-onnx-offline"
SHERPA_MODEL = os.environ.get("VOICE_SHERPA_MODEL",
                              "/data/ai_cpe/sherpa-onnx/paraformer-zh-small/model.int8.onnx")
SHERPA_TOKENS = os.environ.get("VOICE_SHERPA_TOKENS",
                               "/data/ai_cpe/sherpa-onnx/paraformer-zh-small/tokens.txt")
SHERPA_THREADS = os.environ.get("VOICE_SHERPA_THREADS", "2")
SHERPA_TIMEOUT = int(os.environ.get("VOICE_SHERPA_TIMEOUT", "30"))
# 常驻识别服务（sherpa-onnx-offline-websocket-server，由 /etc/init.d/sherpa-asr 管理）
SHERPA_WS = os.environ.get("VOICE_SHERPA_WS", "ws://127.0.0.1:6006")
TTS_TIMEOUT = 5
TTS_CACHE = os.environ.get("VOICE_TTS_CACHE", "/data/ai_cpe/tts_cache")
FAST_TIMEOUT_TEXT = "这次处理有点慢，请再试一次。"
CACHED_SPEECH = {"在呢，请说", "我在听，请说", "请再说一下，我没听清",
                 "好的，再见", "好的，先不打扰你了", "2",
                 "已发送开灯指令。", "已发送关灯指令。",
                 "灯泡离线了，请检查灯泡电源和网络连接。", FAST_TIMEOUT_TEXT,
                 "天气服务暂时连接失败，请稍后再试。",
                 "行情服务暂时连接失败，请稍后再试。",
                 "没有找到这只股票，请换一个名称或代码。"}
WAKE_WORDS = ["小皮", "下皮", "小屁", "下屁", "小批", "小披", "小pipi", "你好小皮", "小皮皮", "下题"]
EXIT_WORDS = ["休息", "睡觉", "退下", "再见", "拜拜", "晚安"]

# ---- 双超时(设计文档 §4)----
WAIT_PROMPT_SECS = 30      # 30 秒仍无结果 -> 先播"请稍等。"
HARD_TIMEOUT_SECS = 60     # 60 秒仍无结果 -> 终止本轮并播"我还没有学会这个问题。"
WAIT_PROMPT_TEXT = "请稍等。"
GIVEUP_TEXT = "我还没有学会这个问题。"

TIME_PATTERN = re.compile(r"^(?:请问|告诉我|帮我查一下)?(?:今天|现在)?(?:是)?(?:几点(?:钟)?(?:了)?|几月几[日号]|几号|星期几|周几|什么日期|什么时间|日期|时间)(?:了|呢|呀|啊|吗)?$")
WEATHER_KEYS = ["天气", "气温", "温度", "下雨", "下雪", "几度", "冷不冷", "热不热"]

# ---- L1 实时行情(设计文档 §2.1 L1「实时联网」,与天气处理器同款形状)----
QUOTE_URL = "https://qt.gtimg.cn/q="
QUOTE_BACKUP_URL = "https://hq.sinajs.cn/list="
QUOTE_SUGGEST_URL = "https://smartbox.gtimg.cn/s3/"
QUOTE_FAIL_TEXT = "行情服务暂时连接失败，请稍后再试。"
QUOTE_NOTFOUND_TEXT = "没有找到这只股票，请换一个名称或代码。"
STOCK_KEYS = ["股价", "股票", "行情", "收盘", "开盘", "涨跌", "涨幅", "跌幅", "涨停",
              "跌停", "大盘", "成交量", "成交额", "市值", "市盈率", "换手率"]
# 本地别名表：覆盖主力用例与已观察到的 ASR 形近误听；其余名称走联网检索。
STOCK_ALIASES = {
    "移远通信": "sh603236", "移远通讯": "sh603236", "一元通信": "sh603236", "亿元通信": "sh603236",
    "以远通信": "sh603236", "以远通讯": "sh603236", "宜远通信": "sh603236",
    "意远通信": "sh603236", "义远通信": "sh603236", "一远通信": "sh603236",
    "上证指数": "sh000001", "上证综指": "sh000001", "沪指": "sh000001",
    "深证成指": "sz399001", "创业板指": "sz399006", "沪深300": "sh000300", "科创50": "sh000688",
}
# 显式代码:无交易所前缀时靠首位推断,推断不出(如"123456")就不当股票处理。
_STOCK_CODE = re.compile(r"(?<![0-9A-Za-z])(?:(sh|sz|bj|hk|us)\s*)?(\d{6})(?![0-9])", re.I)
# 疑问词不是股票名:"什么是股票"应转 Hermes 解释概念,不该去查行情。
_NAME_STOPWORDS = {"什么", "怎么", "如何", "哪些", "哪个", "多少", "为什么", "啥", "什么样"}

# ---- 「拒绝不得成为终答」(设计文档 §3.3/§11: 复杂问题必须稳定进入 Hermes)----
# 实测 deepseek-v4-flash 对实时数据的回绝措辞,见 2026-09-18 设备日志。
_REFUSAL_PATTERN = re.compile(
    r"我(?:暂时|这边)?(?:没法|无法|不能|查不了|查不到|没有|不具备)"
    r"|没有[^，。！？]{0,10}的功能"
    r"|帮不了|做不到"
    r"|建议(?:您|你)(?:打开|用|上|去|使用)"
    r"|只能帮(?:你|您)")
# 实时数据域：模型给不出实时值，编造一个数字比回绝更糟(§1.2 同一原则)。
LIVE_DOMAINS = ["股价", "股票", "行情", "收盘", "开盘", "汇率", "油价", "汽油", "柴油",
                "金价", "比分", "彩票", "快递", "航班", "限行", "新闻", "涨停", "涨跌"]
LIVE_MARKERS = ["今天", "现在", "最新", "实时", "当前", "刚刚", "多少", "几"]

SYSTEM_PROMPT = ("你是小皮，智能音箱助手。回答供语音播报，只用两三句中文，不超过100字，不用标题、列表或Markdown。"
                 "查天气、查股票行情、控制灯泡、拍照、人脸检测、坐姿检测时务必调用对应工具获取真实信息。"
                 "股价、汇率、油价、新闻、比分等实时数据一律不得凭记忆作答，没有工具就说明无法获取，绝不编造数字。"
                 "注意：语音转写可能不准（如'人脸'可能被听成'冷凉/人年/人联'，'坐姿'可能被听成'作子/坐支'，"
                 "'移远通信'可能被听成'亿元通信/一元通信'），"
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
        "name": "get_stock_quote",
        "description": "查询股票或指数的实时行情、收盘价、涨跌幅",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string", "description": "股票名称或6位代码"}},
                       "required": ["name"]}}},
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


def asr_context(path):
    # Whisper 音频上下文每秒约 50 个位置；留至少一秒余量。
    try:
        with wave.open(path) as wav:
            duration = wav.getnframes() / wav.getframerate()
        return min(1500, max(384, math.ceil((duration + 1) * 50 / 128) * 128))
    except (OSError, wave.Error):
        return 1500


def _paraformer_payload(path):
    """wav → 16k float32 载荷（8 字节头: 采样率+字节数，官方 ws 协议）。失败返回 None。"""
    try:
        import array
        import audioop
        import struct
        with wave.open(path) as w:
            sr = w.getframerate()
            frames = w.readframes(w.getnframes())
        if sr != 16000:
            frames, _ = audioop.ratecv(frames, 2, 1, sr, 16000, None)
            sr = 16000
        a = array.array("h")
        a.frombytes(frames)
        f32 = array.array("f", (x / 32768.0 for x in a))
        return struct.pack("<ii", sr, len(f32) * 4) + f32.tobytes()
    except Exception:
        return None


def _paraformer_ws_once(path):
    """常驻服务路径：模型只加载一次，单句毫秒级。失败抛异常，由上层回退。"""
    import asyncio
    import websockets

    payload = _paraformer_payload(path)
    if payload is None:
        raise RuntimeError("bad wav")

    async def _run():
        async with websockets.connect(SHERPA_WS, max_size=20 * 1024 * 1024,
                                      open_timeout=3) as ws:
            buf = payload
            while len(buf) > 10240:
                await ws.send(buf[:10240])
                buf = buf[10240:]
            if buf:
                await ws.send(buf)
            r = await asyncio.wait_for(ws.recv(), timeout=15)
            if isinstance(r, bytes):
                r = r.decode("utf-8", "replace")
            return json.loads(r).get("text", "")

    return asyncio.run(_run())


def _paraformer_oneshot(path):
    """回退路径：sherpa-onnx-offline 单次调用（含模型加载，慢但独立可用）。"""
    tmp16 = path + ".16k.wav"
    try:
        import audioop
        with wave.open(path) as w:
            sr = w.getframerate()
            frames = w.readframes(w.getnframes())
        if sr != 16000:
            frames, _ = audioop.ratecv(frames, 2, 1, sr, 16000, None)
        with wave.open(tmp16, "wb") as o:
            o.setnchannels(1)
            o.setsampwidth(2)
            o.setframerate(16000)
            o.writeframes(frames)
        cmd = [SHERPA_GLIBC, "--library-path", SHERPA_LIB, SHERPA_BIN,
               "--tokens=" + SHERPA_TOKENS,
               "--paraformer=" + SHERPA_MODEL,
               "--num-threads=" + SHERPA_THREADS,
               tmp16]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=SHERPA_TIMEOUT)
        text = ""
        for line in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    got = json.loads(line).get("text", "")
                    if got:
                        text = got
                except ValueError:
                    pass
        return to_simplified(text)
    except Exception as e:
        print("[ASR-PF] oneshot error=%s" % type(e).__name__, flush=True)
        return ""

    finally:
        try:
            os.unlink(tmp16)
        except OSError:
            pass


def transcribe_paraformer(path):
    """Paraformer 引擎：优先常驻服务（ws://127.0.0.1:6006，毫秒级）；
    服务不可用时回退单次调用（慢但可用）。失败返回 ''（与 whisper 语义一致）。"""
    started = time.monotonic()
    try:
        try:
            return to_simplified(_paraformer_ws_once(path))
        except Exception as e:
            print("[ASR-PF] ws fallback (%s)" % type(e).__name__, flush=True)
            return _paraformer_oneshot(path)

    finally:
        print("[LATENCY] asr_s=%.3f" % (time.monotonic() - started), flush=True)


def transcribe(path, use_prompt=None):
    if ASR_ENGINE == "paraformer":
        return transcribe_paraformer(path)
    if use_prompt is None:
        use_prompt = ASR_HOTWORD_PROMPT
    started = time.monotonic()
    env = dict(os.environ, LD_LIBRARY_PATH=WHISPER_LIB)
    try:
        # 热词偏置(设计 §6.1): 业务词表作为 whisper initial-prompt 上文提示。
        # 默认关闭(ASR_HOTWORD_PROMPT 开关)；唤醒/待机路径恒不带(use_prompt=False)。
        # 开启与否以真实口音录音 A/B 为准（当前实测为负面：污染解码 + 每次 +2.2s）。
        cmd = [WHISPER, "-m", MODEL, "-f", path, "-l", "zh",
               "--no-timestamps", "-np", "-ac", str(asr_context(path)),
               "-bs", "1", "-bo", "1", "-nf"]
        if accent_correct is not None and use_prompt:
            try:
                cmd += ["--prompt", accent_correct.whisper_prompt()]
            except Exception:
                pass
        r = subprocess.run(cmd,
                           capture_output=True, text=True, env=env, timeout=ASR_TIMEOUT)
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

    finally:
        print("[LATENCY] asr_s=%.3f" % (time.monotonic() - started), flush=True)


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
    text = re.sub(r"^一家一等(?:一集|[于於]几)$", "一加一等于几", text)
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
        with urllib.request.urlopen(url, timeout=2) as response:
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
    with urllib.request.urlopen(url, timeout=4) as response:
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


# ---- L1 实时行情(设计文档 §2.1,与 get_weather_now 同款主备结构)----
def _unescape_u(text):
    """smartbox 把中文名写成 ASCII 的 \\uXXXX 转义,不是 GBK。"""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def _infer_prefix(code):
    head = code[0]
    if head in "569":
        return "sh"
    if head in "023":
        return "sz"
    if head in "48":
        return "bj"
    return None


def _code_to_symbol(code, market=None):
    if market:
        market = market.lower()
        return ("sh" if market == "ss" else market) + code
    prefix = _infer_prefix(code)
    return prefix + code if prefix else None


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _trim_num(value):
    return ("%.2f" % value).rstrip("0").rstrip(".") or "0"


def _market_closed(stamp):
    """行情时间戳 YYYYMMDDHHMMSS:当日 15:00 后,或已是更早的日期,都算已收盘。"""
    try:
        moment = datetime.datetime.strptime(stamp, "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return True
    return moment.date() < datetime.datetime.now().date() or moment.hour >= 15


def _format_quote(name, price, change, percent, stamp):
    if change > 0:
        move = "涨%s元，涨幅百分之%s" % (_trim_num(change), _trim_num(percent))
    elif change < 0:
        move = "跌%s元，跌幅百分之%s" % (_trim_num(-change), _trim_num(-percent))
    else:
        move = "与上一交易日收盘持平"
    return "%s今天%s%s元，%s。" % (
        name, "收盘" if _market_closed(stamp) else "最新价", _trim_num(price), move)


def _quote_from_tencent(symbol):
    with urllib.request.urlopen(QUOTE_URL + symbol, timeout=4) as response:
        body = response.read().decode("gbk", "replace")
    match = re.search(r'="([^"]*)"', body)
    if not match:
        raise ValueError("bad quote payload")
    field = match.group(1).split("~")
    if len(field) < 33 or not field[1]:
        raise ValueError("short quote payload")
    name, price = field[1], _num(field[3])
    if price <= 0:
        return "暂时查不到%s的行情，可能停牌或代码不正确。" % name
    return _format_quote(name, price, _num(field[31]), _num(field[32]), field[30])


def _quote_from_sina(symbol):
    req = urllib.request.Request(QUOTE_BACKUP_URL + symbol,
                                 headers={"Referer": "https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=4) as response:
        body = response.read().decode("gbk", "replace")
    match = re.search(r'="([^"]*)"', body)
    if not match:
        raise ValueError("bad quote payload")
    field = match.group(1).split(",")
    if len(field) < 32 or not field[0]:
        raise ValueError("short quote payload")
    name, price, previous = field[0], _num(field[3]), _num(field[2])
    if price <= 0:
        return "暂时查不到%s的行情，可能停牌或代码不正确。" % name
    change = price - previous
    stamp = re.sub(r"\D", "", field[30] + field[31])[:14]
    return _format_quote(name, price, change,
                         (change / previous * 100) if previous else 0.0, stamp)


def get_stock_quote(symbol):
    try:
        return _quote_from_tencent(symbol)
    except Exception as e:
        print("[QUOTE] primary failed: %s" % type(e).__name__, flush=True)
    try:
        return _quote_from_sina(symbol)
    except Exception as e:
        print("[QUOTE] backup failed: %s" % type(e).__name__, flush=True)
    return QUOTE_FAIL_TEXT


def _name_candidates(text):
    """剥掉问法与行情词,取出剩余的中文串作为候选股票名。"""
    stripped = text
    for word in ("请查看", "帮我查一下", "帮我查", "查一下", "请问", "告诉我", "看看",
                 "的", "是", "现在", "今天", "最新", "实时", "多少", "多少钱", "怎么样") + tuple(STOCK_KEYS):
        stripped = stripped.replace(word, " ")
    return [c for c in re.findall(r"[一-鿿]{2,8}", stripped) if c not in _NAME_STOPWORDS]


def _suggest_symbol(name):
    """联网把名称解析成代码;优先精确同名,再按 A股/港股/指数 顺序取。"""
    url = QUOTE_SUGGEST_URL + "?" + urllib.parse.urlencode({"q": name, "t": "all"})
    with urllib.request.urlopen(url, timeout=4) as response:
        body = response.read().decode("ascii", "ignore")
    if '"' not in body:
        return None
    hints = []
    for item in body.split('"')[1].split("^"):
        field = item.split("~")
        if len(field) < 5 or field[0] not in ("sh", "sz", "bj", "hk") or not field[1].isdigit():
            continue
        hints.append((field[4], _unescape_u(field[2]), field[0] + field[1]))
    for kind, label, symbol in hints:
        if label == name:
            return symbol, label
    for want in ("GP-A", "GP", "ZS", "ETF"):
        for kind, label, symbol in hints:
            if kind == want:
                return symbol, label
    return None


def _explicit_symbol(text):
    match = _STOCK_CODE.search(text)
    return _code_to_symbol(match.group(2), match.group(1)) if match else None


def resolve_symbol(text):
    """口语名/代码 -> 行情接口代码。返回 (symbol, 名称) 或 None。
    顺序: 显式代码 -> 本地别名 -> 联网检索。"""
    symbol = _explicit_symbol(text)
    if symbol:
        return symbol, None
    for name, symbol in STOCK_ALIASES.items():
        if name in text:
            return symbol, name
    for candidate in _name_candidates(text):
        try:
            found = _suggest_symbol(candidate)
        except Exception as e:
            print("[QUOTE] suggest failed: %s" % type(e).__name__, flush=True)
            continue
        if found:
            return found
    return None


def is_stock_query(text):
    if any(key in text for key in STOCK_KEYS):
        return True
    if any(name in text for name in STOCK_ALIASES):
        return True
    return _explicit_symbol(text) is not None


def is_capability_refusal(reply):
    """模型说"我查不了"并不等于回答了问题,不得作为终答(设计文档 §3.3/§11)。"""
    return bool(_REFUSAL_PATTERN.search(reply or ""))


def needs_live_data(text):
    """实时数据类问题:模型不带工具作答就有编造数字的风险,强制升级 Hermes。"""
    if not any(key in text for key in LIVE_DOMAINS):
        return False
    return any(key in text for key in LIVE_MARKERS)


_BOX_FRAME = re.compile(r"^\s*[┌└│├─┐┘┤]+")


def _cjk_ratio(text):
    return sum(1 for c in text if "一" <= c <= "鿿") / len(text) if text else 0.0


def extract_hermes_answer(output, min_ratio=0.3, max_chars=300):
    """Hermes 的 stdout 先打印英文推理框,再打印最终中文答案(实测 2026-09-18)。
    从末尾向前取连续的中文段落,遇到第一个不达标段落即停,避免把推理念给用户。"""
    kept = [line for line in (output or "").splitlines()
            if not _BOX_FRAME.match(line) and not line.strip().startswith("session_id:")]
    picked, total = [], 0
    for block in reversed("\n".join(kept).split("\n\n")):
        text = " ".join(block.split())
        if not text:
            continue
        if _cjk_ratio(text) < min_ratio:
            break
        picked.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "".join(reversed(picked))


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
        elif name == "get_stock_quote":
            found = resolve_symbol(args.get("name", ""))
            return get_stock_quote(found[0]) if found else QUOTE_NOTFOUND_TEXT
        elif name == "control_bulb":
            action = args.get("action", "status")
            r = subprocess.run(["python3", BULB, action], capture_output=True, text=True, timeout=6)
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
        if name == "get_stock_quote":
            return QUOTE_FAIL_TEXT
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
        # stdout 里带着推理框,必须先抽出最终中文答案,否则会被播报层当成乱码过滤掉。
        answer = extract_hermes_answer(output)
        print("[HERMES] raw=%d chars -> answer=%d chars" % (len(output), len(answer)), flush=True)
        return answer or "问答服务暂时没有返回结果，请稍后再试。"
    except subprocess.TimeoutExpired:
        return "抱歉，这个问题我想得有点久，你换个说法试试"
    except Exception:
        return ""


def prepare_tts(text, timeout=TTS_TIMEOUT):
    """固定句缓存，动态句临时文件；超时可控，不复用残缺音频。"""
    import edge_tts
    os.makedirs(TTS_CACHE, exist_ok=True)
    key = hashlib.sha256((VOICE + "\n" + text).encode()).hexdigest()
    cached = text in CACHED_SPEECH
    target = os.path.join(TTS_CACHE, key + ".mp3")
    if cached and os.path.exists(target) and os.path.getsize(target) > 0:
        return target, False
    fd, temporary = tempfile.mkstemp(suffix=".mp3", dir=TTS_CACHE)
    os.close(fd)
    async def synth():
        await asyncio.wait_for(edge_tts.Communicate(text, VOICE).save(temporary), timeout)
    try:
        asyncio.run(synth())
        if not os.path.getsize(temporary):
            raise ValueError("empty TTS audio")
        if cached:
            os.replace(temporary, target)
            return target, False
        return temporary, True
    except Exception:
        os.unlink(temporary)
        raise


def speak(text):
    if not text:
        return
    text = text.strip()
    # 跳过开头非中文（模型偶发英文思考残留），避免整条被误判"无法播报"（2026-09-21 实测）。
    _cn = re.search(r'[\u4e00-\u9fff]', text)
    if _cn and _cn.start() > 0:
        text = text[_cn.start():]
    text = text.strip()[:120]
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    if len(text) > 10 and cn / len(text) < 0.3:
        text = "回复内容暂时无法播报，请稍后再试。"
    started = time.monotonic()
    disposable = False
    mp3 = None
    try:
        try:
            mp3, disposable = prepare_tts(text)
        except Exception as e:
            print("[TTS] synthesis failed: %s" % type(e).__name__, flush=True)
            # 已预热的本地提示，无需再等待网络。
            mp3, disposable = prepare_tts(FAST_TIMEOUT_TEXT)
        print("[LATENCY] tts_ready_s=%.3f text=%r" % (time.monotonic() - started, text), flush=True)
        p1 = subprocess.Popen([MPG123, "-q", "-s", "--rate", "48000", mp3],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            p2 = subprocess.Popen(["aplay", "-D", PLAY_DEV, "-f", "S16_LE", "-r", "48000",
                                   "-c", "1", "-q"], stdin=p1.stdout, stderr=subprocess.DEVNULL)
            p1.stdout.close()
            try:
                p2.wait(timeout=25)
            except subprocess.TimeoutExpired:
                p2.kill()
                p2.wait()
                raise
        finally:
            if p1.poll() is None:
                p1.terminate()
            p1.wait(timeout=2)
    except Exception as e:
        print("speak error:", e, flush=True)
    finally:
        if disposable and mp3:
            os.unlink(mp3)


def respond(clean):
    intent, slots = classify(clean)
    # 只有"确定不会升级到 Hermes"的意图才享受 7 秒快速失败。LLM_OR_HERMES 现在可能
    # 因拒绝/实时数据升级到 Hermes(实测约 29 秒),必须走 §4 的 30/60 双超时。
    # STOCK_QUOTE 也移出快失败：股票名解析失败会升级 Hermes，7 秒必超时（2026-09-21 实测）。
    simple = intent in ("LOCAL_DATE_TIME", "LOCAL_CALCULATOR", "WEATHER", "EMPTY") or (
        intent == "DEVICE_CONTROL" and slots.get("tool") == "control_bulb")
    return handle_turn(clean, _turn_handler, speak,
                       hard_secs=7 if simple else HARD_TIMEOUT_SECS,
                       wait_secs=30,
                       giveup_text=FAST_TIMEOUT_TEXT if simple else GIVEUP_TEXT)


# 仅识别句首完整称呼；保留“你好”的兼容入口，去掉单字误触发。
_WAKE_PREFIX = re.compile(r"^(?:你好[，,、\s]*(?:(?:小皮皮|小pipi|小皮|下皮|下题|小屁|下屁|小批|小披|夏丁|下爹|夏皮))?|小皮皮|小pipi|小皮|下皮|下题|小屁|下屁|小批|小披)[，,。.!！?？\s]*", re.I)


def is_wake(text):
    return bool(_WAKE_PREFIX.match(text.strip()))


def strip_wake(text):
    """剥离开头唤醒词。ASR 循环输出（如"你好下皮"×70）全部剥掉：
    剥完为空 = 纯唤醒呼叫（按"在呢/我在听"处理），避免循环垃圾被当成命令送后端。"""
    s = text.strip().strip("，,。.!！?？ ")
    while s:
        s2 = _WAKE_PREFIX.sub("", s, count=1).strip("，,。.!！?？ ")
        if s2 == s:
            break
        s = s2
    return s


CITIES = ["上海", "北京", "广州", "深圳", "杭州", "南京", "成都", "重庆", "武汉",
          "西安", "天津", "苏州", "长沙", "郑州", "青岛", "沈阳", "大连", "厦门",
          "福州", "合肥", "昆明", "哈尔滨", "济南", "宁波", "无锡", "香港", "澳门", "台北",
          # 新疆地名（口音优化设计 §6.1：加入实际业务需要的地名）
          "乌鲁木齐", "喀什", "伊犁", "克拉玛依", "吐鲁番", "阿克苏",
          "和田", "库尔勒", "昌吉", "哈密", "石河子", "阿勒泰", "博乐", "奎屯"]


def extract_city(text):
    for c in CITIES:
        if c in text:
            return c
    return None


def classify(text, _accent_tried=False):
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
    # 残句兜底：只听到"上传/immich"而丢了"拍照"时也要能兜住（2026-09-21 实测漏失）。
    if any(k in text for k in ["上传", "传到", "同步"]) and any(
            k in text for k in ["照片", "相片", "immich", "相册", "拍"]):
        return "DEVICE_CONTROL", {"tool": "photo_upload"}
    if any(k in text for k in ["坐姿", "姿势", "体态", "座姿", "做姿", "坐子"]):
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
    # 2b. 实时行情(同属 §2.2 的 REALTIME 带,故先于时间/计算器)。
    #     这里只做纯关键词判定,代码解析(可能联网)留给 dispatch。
    if is_stock_query(text):
        return "STOCK_QUOTE", {"query": text}
    # 3. 时间/日期
    if TIME_PATTERN.fullmatch(text):
        return "LOCAL_DATE_TIME", {}
    # 4. 本地计算器
    expr = is_calc(text)
    if expr:
        return "LOCAL_CALCULATOR", {"expr": expr}
    # 5. 新疆口音近音纠错(设计 §6.3): 前面都没命中时,才尝试把疑似"口音误识别的
    #    控制命令"拉回已知设备命令,再分类一次。只在此兜底位置触发 => 标准普通话
    #    已在上面命中的意图绝不会被纠错改写,保护基线不退化(设计 §2)。
    if accent_correct is not None and not _accent_tried:
        fix = accent_correct.correct_command(text)
        if fix is not None:
            fixed_intent, fixed_slots = classify(fix["corrected"], _accent_tried=True)
            if fixed_intent == "DEVICE_CONTROL":
                fixed_slots = dict(fixed_slots)
                fixed_slots["accent_fix"] = fix  # 保留纠错溯源,便于日志与回滚
                print("[ACCENT] %r -> %r (dist=%d)" % (text, fix["corrected"], fix["distance"]), flush=True)
                return fixed_intent, fixed_slots
    # 6. 交给上层: 先 LLM 函数调用兜底,再 Hermes
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
    if intent == "STOCK_QUOTE":
        found = resolve_symbol(slots.get("query", text))
        if not found:
            # 名称解析不出来(常见于 ASR 误听)时交给 Hermes,不要谎称查过了。
            return None, True
        return get_stock_quote(found[0]), False
    if intent == "LOCAL_DATE_TIME":
        return get_time_now(), False
    if intent == "LOCAL_CALCULATOR":
        val = safe_calc(slots["expr"])
        if val is not None:
            return "%s" % val, False
        # 解析失败则兜底给 LLM/Hermes
        intent = "LLM_OR_HERMES"
    # LLM 函数调用兜底(抗误听)。注意:模型的"我查不了"不是答案,
    # 实时数据类问题不带工具作答也有编造风险 —— 两种都必须转 Hermes(§3.3/§11)。
    try:
        reply, used_tool = llm_tools(text)
        if reply and not is_capability_refusal(reply) and (used_tool or not needs_live_data(text)):
            return reply, False
        if reply:
            print("[ROUTE] escalate reason=%s reply=%r" % (
                "refusal" if is_capability_refusal(reply) else "live-data", reply[:40]), flush=True)
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
                poll=0.2, giveup_text=GIVEUP_TEXT):
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
            tts(giveup_text)
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
    print("voice_assistant started: rec=%s play=%s vad=%s asr=%s" % (REC_DEV, PLAY_DEV, vad is not None, ASR_ENGINE), flush=True)
    while True:
        # IDLE: VAD 监听唤醒词（流式端点检测，说完即停；无人说话则持续等待）
        # 唤醒收尾等待 800ms；固定确认语音在部署时预热到本地。
        if not vad_record("/tmp/voice_rec.wav", max_s=8, start_wait_s=86400, end_sil_ms=800):
            continue
        # 待机唤醒转写不加热词 prompt: 唤醒词不在热词表内,加了只会 +2.2s 且空耗 CPU。
        text = transcribe("/tmp/voice_rec.wav", use_prompt=False)
        print("[listen] %r" % text, flush=True)
        if is_wake(text):
            print(">>> WAKE", flush=True)
            clean = strip_wake(text)
            if clean:
                respond(clean)
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
                    respond(clean)
                except Exception as e:
                    print("route error:", e, flush=True)
                    speak("我走神了，再说一次")


if __name__ == "__main__":
    main()
