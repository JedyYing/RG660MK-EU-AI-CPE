#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""决定性测试：停服务，同一音箱播放下，对比 CM564(2,0) 与 C270(1,0) 的捕获。"""
import sys, os, time, subprocess, glob, wave, struct, math
sys.path.insert(0, "/data/ai_cpe")

mp3s = sorted(glob.glob("/data/ai_cpe/tts_cache/*.mp3"), key=os.path.getmtime)
mp3 = mp3s[-1]
print("mp3=%s" % os.path.basename(mp3), flush=True)

# 捕获流状态快照（停服务前）
try:
    print("pcm0c status:", open("/proc/asound/card2/pcm0c/sub0/status").read().strip(), flush=True)
except Exception as e:
    print("pcm status err:", e, flush=True)

subprocess.run(["/etc/init.d/voice_assistant", "stop"])
time.sleep(1.2)

def rec(dev, out, dur=8, rate=48000):
    return subprocess.Popen(
        ["arecord", "-D", dev, "-f", "S16_LE", "-r", str(rate), "-c", "1", "-d", str(dur), "-t", "wav", out],
        stderr=subprocess.PIPE)

r2 = rec("plughw:2,0", "/tmp/captest_cm564.wav")
time.sleep(0.4)
r1 = rec("plughw:1,0", "/tmp/captest_c270.wav")
time.sleep(0.5)

p1 = subprocess.Popen(["/data/ai_cpe/bin/mpg123", "-q", "-s", "--rate", "48000", mp3],
                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
p2 = subprocess.Popen(["aplay", "-D", "plughw:2,0", "-f", "S16_LE", "-r", "48000", "-c", "1", "-q"],
                      stdin=p1.stdout, stderr=subprocess.DEVNULL)
p1.stdout.close()
p2.wait()
print("aplay rc=%s" % p2.returncode, flush=True)
r2.wait()
r1.wait()
e2 = r2.stderr.read(); e1 = r1.stderr.read()
print("rec cm564 rc=%s stderr=%r" % (r2.returncode, e2[-100:]), flush=True)
print("rec c270  rc=%s stderr=%r" % (r1.returncode, e1[-100:]), flush=True)

import voice_assistant as va
va.accent_correct = None
for f in ("/tmp/captest_cm564.wav", "/tmp/captest_c270.wav"):
    try:
        w = wave.open(f)
        sr = w.getframerate()
        d = w.readframes(w.getnframes())
        w.close()
        s = struct.unpack('<%dh' % (len(d) // 2), d)
        rms = math.sqrt(sum(x * x for x in s) / len(s))
        peak = max(abs(x) for x in s)
        txt = va.transcribe(f)
        print("%s rms=%.0f peak=%d dur=%.2fs asr=%r" % (f, rms, peak, len(s) / sr, txt), flush=True)
    except Exception as e:
        print(f, "ERR", e, flush=True)

subprocess.run(["/etc/init.d/voice_assistant", "start"])
time.sleep(1)
print("service restarted", flush=True)
