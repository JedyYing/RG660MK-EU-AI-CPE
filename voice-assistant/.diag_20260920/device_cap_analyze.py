#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""分析抓取的现场录音：电平 + 双模式转写 + 唤醒判定。"""
import sys, os, wave, struct, math, glob
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va
import accent_correct as ac

orig = ac.whisper_prompt

def levels(f):
    w = wave.open(f)
    n, sr = w.getnframes(), w.getframerate()
    data = w.readframes(n)
    w.close()
    s = struct.unpack('<%dh' % (len(data) // 2), data)
    peak = max(abs(x) for x in s)
    rms = math.sqrt(sum(x * x for x in s) / len(s))
    return n / sr, rms, peak

def asr(f, use_prompt):
    if use_prompt:
        va.accent_correct = ac
        ac.whisper_prompt = orig
    else:
        va.accent_correct = None
    return va.transcribe(f)

files = sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cap_*.wav"))
for f in files:
    dur, rms, peak = levels(f)
    r_none = asr(f, False)
    r_full = asr(f, True)
    print("== %s  dur=%.2fs rms=%.0f peak=%d" % (os.path.basename(f), dur, rms, peak))
    print("   none: %r  wake=%s strip=%r" % (r_none, va.is_wake(r_none), va.strip_wake(r_none)))
    print("   full: %r" % r_full)
ac.whisper_prompt = orig
va.accent_correct = ac
print("DONE")
