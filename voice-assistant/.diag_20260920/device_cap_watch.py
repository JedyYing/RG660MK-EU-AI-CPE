#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""抓取 /tmp/voice_rec.wav 每次变化（唤醒失败现场取证）→ /tmp/cap_NN_HHMMSS.wav。"""
import os, time, shutil
src = "/tmp/voice_rec.wav"
end = time.time() + 2400
last = None
n = 0
print("watcher start 420s", flush=True)
try:
    st = os.stat(src)
    n += 1
    dst = "/tmp/cap_%02d_%s_init.wav" % (n, time.strftime("%H%M%S"))
    shutil.copy(src, dst)
    last = (st.st_mtime_ns, st.st_size)
    print("cap %s size=%d (init)" % (dst, st.st_size), flush=True)
except OSError as e:
    print("no initial file: %s" % e, flush=True)
while time.time() < end:
    try:
        st = os.stat(src)
        key = (st.st_mtime_ns, st.st_size)
        if key != last:
            n += 1
            dst = "/tmp/cap_%02d_%s.wav" % (n, time.strftime("%H%M%S"))
            shutil.copy(src, dst)
            last = key
            print("cap %s size=%d" % (dst, st.st_size), flush=True)
    except OSError:
        pass
    time.sleep(0.2)
print("watcher done n=%d" % n, flush=True)
