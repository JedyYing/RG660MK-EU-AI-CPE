#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""严谨版回环测试：播放 TTS（记录 rc/stderr/耗时）→ 检查 VAD 是否捕获。"""
import sys, os, time, subprocess, glob
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va

mp3s = sorted(glob.glob("/data/ai_cpe/tts_cache/*.mp3"), key=os.path.getmtime)
mp3 = mp3s[-1]
print("play file=%s" % os.path.basename(mp3), flush=True)

t0 = time.time()
p1 = subprocess.Popen(["/data/ai_cpe/bin/mpg123", "-q", "-s", "--rate", "48000", mp3],
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
p2 = subprocess.Popen(["aplay", "-D", "plughw:2,0", "-f", "S16_LE", "-r", "48000", "-c", "1", "-q"],
                      stdin=p1.stdout, stderr=subprocess.PIPE)
p1.stdout.close()
p2err = p2.communicate()[1]
p1err = p1.communicate()[1]
dt = time.time() - t0
print("aplay rc=%s stderr=%r" % (p2.returncode, p2err[-150:]), flush=True)
print("mpg123 rc=%s stderr=%r" % (p1.returncode, p1err[-150:]), flush=True)
print("播放耗时=%.2fs" % dt, flush=True)

print("等待 VAD 捕获+转写(14s)...", flush=True)
time.sleep(14)
print("DONE", flush=True)
