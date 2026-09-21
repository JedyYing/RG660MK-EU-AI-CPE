#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""循环复现用例：keep_last + 去query组prompt，测温度回退能否打破循环。"""
import subprocess, time, os, sys
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va
import accent_correct as ac

MODE = " ".join(sys.argv[1:]) or "nq"
NOQ = "，".join(
    w for grp in ("device", "room", "verb", "place")
    for w in ac.HOTWORDS.get(grp, []))
if MODE == "nq":
    PROMPT = NOQ
elif MODE == "none":
    PROMPT = None
else:
    PROMPT = ac.whisper_prompt()

def run(tag, flags, timeout=25):
    cmd = [va.WHISPER, "-m", va.MODEL, "-f", "/tmp/keep_last.wav", "-l", "zh",
           "--no-timestamps", "-np", "-ac", str(va.asr_context("/tmp/keep_last.wav"))] + flags
    if PROMPT:
        cmd += ["--prompt", PROMPT]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True,
                           env=dict(os.environ, LD_LIBRARY_PATH=va.WHISPER_LIB), timeout=timeout)
        dt = time.time() - t0
        out = r.stdout.decode("utf-8", "replace").strip().replace("\n", " / ")
        print("[%s] %.2fs %r" % (tag, dt, out[:100]), flush=True)
    except subprocess.TimeoutExpired:
        print("[%s] >%ds TIMEOUT (循环未断)" % (tag, timeout), flush=True)

run("A cur  -bs1-bo1-nf       ", ["-bs", "1", "-bo", "1", "-nf"])
run("B fall -bs1-bo1          ", ["-bs", "1", "-bo", "1"])
run("C fall -bs1-bo1-et1.5    ", ["-bs", "1", "-bo", "1", "-et", "1.5"])
run("E fall -bs1-bo1-et2.0    ", ["-bs", "1", "-bo", "1", "-et", "2.0"])
print("DONE mode=%s" % MODE, flush=True)
