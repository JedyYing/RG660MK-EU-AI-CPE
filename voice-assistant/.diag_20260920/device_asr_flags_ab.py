#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""ASR 解码参数 A/B：验证温度回退/束搜索能否打破解码循环。"""
import subprocess, time, os, sys
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va
import accent_correct as ac

WHISPER = va.WHISPER
MODEL = va.MODEL
LIB = va.WHISPER_LIB
print("whisper=%s model=%s" % (WHISPER, MODEL), flush=True)

def run(path, flags, prompt=None, timeout=20):
    cmd = [WHISPER, "-m", MODEL, "-f", path, "-l", "zh", "--no-timestamps", "-np",
           "-ac", str(va.asr_context(path))] + flags
    if prompt:
        cmd += ["--prompt", prompt]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True,
                           env=dict(os.environ, LD_LIBRARY_PATH=LIB), timeout=timeout)
        dt = time.time() - t0
        out = r.stdout.decode("utf-8", "replace").strip().replace("\n", " / ")
        return dt, out[:90]
    except subprocess.TimeoutExpired:
        return time.time() - t0, "<8s+ TIMEOUT>"

FULL = None
try:
    FULL = ac.whisper_prompt()
except Exception as e:
    print("prompt err:", e, flush=True)

configs = [
    ("A cur   -bs1-bo1-nf", ["-bs", "1", "-bo", "1", "-nf"]),
    ("B fall  -bs1-bo1", ["-bs", "1", "-bo", "1"]),
    ("D beam  -bs5-bo5", ["-bs", "5", "-bo", "5"]),
]
files = [("keep_last", "/tmp/keep_last.wav"), ("loop_src", "/tmp/loopback_src.wav")]

for fname, fpath in files:
    if not os.path.exists(fpath):
        print("SKIP missing: %s" % fpath, flush=True)
        continue
    for cname, flags in configs:
        for mname, prompt in (("none", None), ("full", FULL)):
            if mname == "full" and not prompt:
                continue
            dt, out = run(fpath, flags, prompt)
            print("[%s][%s][%s] %.2fs %r" % (fname, cname, mname, dt, out), flush=True)
print("AB DONE", flush=True)
