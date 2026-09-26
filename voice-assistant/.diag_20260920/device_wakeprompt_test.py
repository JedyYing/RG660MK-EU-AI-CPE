#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""唤醒词 prompt 偏置实验：能否把弱语音捞出来？同时验证噪声会不会被幻觉成唤醒词（假阳性）。"""
import subprocess, time, os, sys
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va

def run(tag, path, prompt, timeout=15):
    cmd = [va.WHISPER, "-m", va.MODEL, "-f", path, "-l", "zh", "--no-timestamps", "-np",
           "-ac", str(va.asr_context(path))]
    if prompt:
        cmd += ["--prompt", prompt]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True,
                           env=dict(os.environ, LD_LIBRARY_PATH=va.WHISPER_LIB), timeout=timeout)
        out = r.stdout.decode("utf-8", "replace").strip()
        print("%-26s P=%-16r %5.1fs %r" % (tag, (prompt or "")[:14], time.time() - t0, out[:70]), flush=True)
    except subprocess.TimeoutExpired:
        print("%-26s P=%-16r TIMEOUT" % (tag, (prompt or "")[:14]), flush=True)

cases = [
    ("keep_last 正控", "/tmp/keep_last.wav"),
    ("cap02_224144 2.5s", "/tmp/cap_02_224144.wav"),
    ("cap13_213341 1.8s", "/tmp/cap_13_213341.wav"),
    ("cap02_201542 噪声负控", "/tmp/cap_02_201542.wav"),
    ("cap05_210516 噪声负控", "/tmp/cap_05_210516.wav"),
]
prompts = [None, "你好小皮", "小皮。你好小皮。小皮。"]
for tag, f in cases:
    if not os.path.exists(f):
        print("MISSING", f, flush=True)
        continue
    for p in prompts:
        run(tag, f, p)
print("DONE", flush=True)
