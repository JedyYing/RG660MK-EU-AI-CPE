# -*- coding: utf-8 -*-
"""本地 TTS（piper 优先 / sherpa-onnx 兜底）——替代 edge-tts 云端合成。
生成者：hermes 2026-10-24

piper：加载 1.4s + 推理 RTF≈0.76（A55），比 sherpa VITS 快约 4 倍。
"""
import os
import re
import subprocess

LOADER = "/data/ai_cpe/glibc/ld-linux-aarch64.so.1"
LIBP = "/data/ai_cpe/glibc"

# ---- piper ----
PIPER_HOME = os.environ.get("PIPER_HOME", "/data/ai_cpe/piper")
PIPER_BIN = os.path.join(PIPER_HOME, "piper-bin")
PIPER_MODEL = os.environ.get("PIPER_MODEL", os.path.join(PIPER_HOME, "zh_CN-huayan-medium.onnx"))

# ---- sherpa 兜底 ----
BIN = "/data/ai_cpe/sherpa-onnx/bin/sherpa-onnx-offline-tts"
MODEL_DIR = os.environ.get("TTS_MODEL_DIR", "/data/ai_cpe/sherpa-onnx/vits-zh-hf-fanchen-C")
MODEL = os.path.join(MODEL_DIR, "vits-zh-hf-fanchen-C.onnx")
TOKENS = os.path.join(MODEL_DIR, "tokens.txt")
LEXICON = os.path.join(MODEL_DIR, "lexicon.txt")
DICT = os.path.join(MODEL_DIR, "dict")


# ---- 百度 TTS（在线，标准中文女声；网络失败自动回落 piper）----
BAIDU_URL = "https://fanyi.baidu.com/gettts"
MPG123_BIN = os.environ.get("MPG123_BIN", "/data/ai_cpe/mpg123/usr/bin/mpg123")
MPG123_LIB = os.environ.get("MPG123_LIB", "/data/ai_cpe/mpg123/usr/lib")
BAIDU_SPD = os.environ.get("VOICE_BAIDU_SPD", "7")
TTS_ENGINE = os.environ.get("VOICE_TTS_ENGINE", "baidu")
BAIDU_CHUNK = 60


def _baidu_ready():
    return os.path.exists(MPG123_BIN)


def _piper_ready():
    return os.path.exists(PIPER_BIN) and os.path.exists(PIPER_MODEL)


def _sherpa_ready():
    return all(os.path.exists(p) for p in (LOADER, BIN, MODEL, TOKENS))


def available():
    return _piper_ready() or _sherpa_ready()


def engine():
    return "piper" if _piper_ready() else ("sherpa" if _sherpa_ready() else "none")


def _synth_piper(text, out_path, timeout):
    env = dict(os.environ)
    env["ESPEAK_DATA_PATH"] = PIPER_HOME
    env["LD_LIBRARY_PATH"] = PIPER_HOME + ":" + env.get("LD_LIBRARY_PATH", "")
    # 2026-11-06：用户反馈“有点老外腔调”，默认放慢 6%（VOICE_TTS_LENGTH_SCALE 可调）
    _ls = os.environ.get("VOICE_TTS_LENGTH_SCALE", "1.06")
    cmd = [LOADER, "--library-path", LIBP + ":" + PIPER_HOME, PIPER_BIN,
           "--model", PIPER_MODEL, "--length_scale", _ls, "--output_file", out_path]
    r = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True,
                       timeout=timeout, env=env)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).decode("utf-8", "replace")[-200:]
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 2000:
        return False, "empty audio"
    return True, ""


def _synth_sherpa(text, out_path, timeout, num_threads=4):
    cmd = [LOADER, "--library-path", LIBP, BIN,
           "--vits-model=" + MODEL, "--vits-tokens=" + TOKENS,
           "--vits-lexicon=" + LEXICON, "--vits-dict-dir=" + DICT,
           "--output-filename=" + out_path,
           "--num-threads=%d" % num_threads,
           text]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).decode("utf-8", "replace")[-200:]
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 2000:
        return False, "empty audio"
    return True, ""




# ---- 长文本分块（piper 在 A55 上合成长句会吃满内存被 OOM 杀掉，2026-10-24 实测 rc=137） ----
MAX_CHUNK = 24


def _chunk_text(text, max_len=MAX_CHUNK):
    """按标点优先切分，单块不超过 max_len 字。"""
    parts = re.split(r"(?<=[，。！？；：、,.!?;:])", text)
    chunks, cur = [], ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        while len(part) > max_len:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(part[:max_len])
            part = part[max_len:]
        if len(cur) + len(part) <= max_len:
            cur += part
        else:
            if cur:
                chunks.append(cur)
            cur = part
    if cur:
        chunks.append(cur)
    return chunks or [text[:max_len]]


def _concat_wavs(paths, out_path):
    """把同参数 wav 顺序拼接（piper 输出参数固定）。"""
    import wave as _w
    with _w.open(paths[0], "rb") as w0:
        params = w0.getparams()
    with _w.open(out_path, "wb") as out:
        out.setparams(params)
        for p in paths:
            with _w.open(p, "rb") as w:
                out.writeframes(w.readframes(w.getnframes()))


def _synth_piper_long(text, out_path, timeout):
    chunks = _chunk_text(text)
    tmp = []
    try:
        for i, c in enumerate(chunks):
            part = "%s.part%d.wav" % (out_path, i)
            ok, err = _synth_piper(c, part, timeout)
            if not ok:
                return False, "chunk%d %s" % (i, err)
            tmp.append(part)
        _concat_wavs(tmp, out_path)
        return True, ""
    finally:
        for t in tmp:
            try:
                os.unlink(t)
            except OSError:
                pass


def _synth_baidu_one(text, out_path, timeout):
    """百度 TTS：GET 拿 MP3，mpg123 转 WAV。返回 (ok, err)。"""
    import urllib.parse
    import urllib.request
    import tempfile
    qs = urllib.parse.urlencode({"lan": "zh", "text": text, "spd": BAIDU_SPD, "source": "web"})
    with urllib.request.urlopen(BAIDU_URL + "?" + qs, timeout=min(6, timeout)) as r:
        data = r.read()
    if len(data) < 1500 or data[:1] == b"{":
        return False, "bad response %d bytes" % len(data)
    fd, tmp_mp3 = tempfile.mkstemp(suffix=".mp3", dir="/tmp")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = MPG123_LIB + ":" + env.get("LD_LIBRARY_PATH", "")
    try:
        r2 = subprocess.run([MPG123_BIN, "-q", "-w", out_path, tmp_mp3],
                            capture_output=True, timeout=10, env=env)
    finally:
        try:
            os.unlink(tmp_mp3)
        except OSError:
            pass
    if r2.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 2000:
        return False, "mpg123 rc=%d %s" % (r2.returncode, (r2.stderr or b"")[-120:])
    return True, ""


def _synth_baidu_long(text, out_path, timeout):
    chunks = _chunk_text(text, BAIDU_CHUNK)
    tmp = []
    try:
        for i, c in enumerate(chunks):
            part = "%s.bt%d.wav" % (out_path, i)
            ok, err = _synth_baidu_one(c, part, timeout)
            if not ok:
                return False, "chunk%d %s" % (i, err)
            tmp.append(part)
        _concat_wavs(tmp, out_path)
        return True, ""
    finally:
        for t in tmp:
            try:
                os.unlink(t)
            except OSError:
                pass


def synthesize(text, out_path, timeout=90, num_threads=4):
    """合成中文语音到 wav。返回 (ok, err)。百度优先（音色自然），失败回落 piper/sherpa。"""
    text = (text or "").strip()
    if not text:
        return False, "empty text"
    try:
        os.unlink(out_path)
    except OSError:
        pass
    last = "no engine"
    if TTS_ENGINE == "baidu" and _baidu_ready():
        try:
            if len(text) > BAIDU_CHUNK:
                ok, last = _synth_baidu_long(text, out_path, timeout)
            else:
                ok, last = _synth_baidu_one(text, out_path, timeout)
            if ok:
                return True, ""
            last = "baidu " + str(last)
        except Exception as e:
            last = "baidu %s: %s" % (type(e).__name__, str(e)[:100])
    if _piper_ready():
        try:
            if len(text) > MAX_CHUNK:
                ok, last = _synth_piper_long(text, out_path, timeout)
            else:
                ok, last = _synth_piper(text, out_path, timeout)
            if ok:
                return True, ""
        except subprocess.TimeoutExpired:
            last = "piper timeout"
        except Exception as e:
            last = "piper %s: %s" % (type(e).__name__, str(e)[:100])
    if _sherpa_ready():
        try:
            ok, err = _synth_sherpa(text, out_path, timeout, num_threads=num_threads)
            return (True, "") if ok else (False, "sherpa " + err)
        except subprocess.TimeoutExpired:
            return False, "sherpa timeout"
        except Exception as e:
            return False, "sherpa %s: %s" % (type(e).__name__, str(e)[:100])
    return False, last
