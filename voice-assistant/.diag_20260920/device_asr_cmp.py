#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""对已保存录音做三种 prompt 模式转写对比：none / full / 去掉query组。"""
import sys
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va
import accent_correct as ac

wav = sys.argv[1] if len(sys.argv) > 1 else "/tmp/keep_last.wav"
orig = ac.whisper_prompt

def run(tag, mode):
    if mode == "none":
        va.accent_correct = None
    else:
        va.accent_correct = ac
        if mode == "full":
            ac.whisper_prompt = orig
        elif mode == "noquery":
            noq = "，".join(w for grp in ("device", "room", "verb", "place") for w in ac.HOTWORDS[grp])
            ac.whisper_prompt = (lambda p: (lambda: p))(noq)
    r = va.transcribe(wav)
    print("[%-8s] %r" % (tag, r), flush=True)

run("none", "none")
run("full", "full")
run("noquery", "noquery")
ac.whisper_prompt = orig
va.accent_correct = ac
print("DONE")
