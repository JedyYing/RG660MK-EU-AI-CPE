#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""增益实验：对 21:32 失败录音再乘 x2/x4，看 whisper 能否解码出唤醒词。"""
import subprocess, time, os, sys, wave, struct
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va
import accent_correct as ac
va.accent_correct = None

def mul_wav(src, dst, factor):
    w = wave.open(src)
    sr, n, sw, ch = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
    d = w.readframes(n)
    w.close()
    s = struct.unpack('<%dh' % (len(d) // 2), d)
    out = struct.pack('<%dh' % len(s),
                      *[max(-32768, min(32767, int(x * factor))) for x in s])
    o = wave.open(dst, 'w')
    o.setnchannels(ch); o.setsampwidth(sw); o.setframerate(sr)
    o.writeframes(out); o.close()

files = ["/tmp/cap_13_213341.wav", "/tmp/cap_09_213313.wav", "/tmp/cap_05_213259.wav",
         "/tmp/cap_07_213303.wav", "/tmp/cap_11_213334.wav", "/tmp/cap_01_213235.wav",
         "/tmp/keep_last.wav"]
for f in files:
    for factor in (1.0, 2.0, 4.0):
        v = "/tmp/bt_x%g.wav" % factor
        mul_wav(f, v, factor)
        t0 = time.time()
        txt = va.transcribe(v)
        print("%-22s x%-3g %.1fs %r" % (os.path.basename(f)[:20], factor, time.time() - t0, txt), flush=True)
print("BOOST TEST DONE", flush=True)
