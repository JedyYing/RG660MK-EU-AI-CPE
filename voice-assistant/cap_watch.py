#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""录音存档守望：/tmp/voice_rec.wav 每次变化即拷贝到 /data/ai_cpe/capdiag/（保留最近 300 条）。
用于诊断 VAD/ASR 层问题（2026-09-21 起）。"""
import os, shutil, time, glob

SRC = "/tmp/voice_rec.wav"
DST_DIR = "/data/ai_cpe/capdiag"
KEEP = 300

os.makedirs(DST_DIR, exist_ok=True)
last = None
n = 0
while True:
    try:
        if os.path.exists(SRC):
            st = os.stat(SRC)
            sig = (st.st_mtime, st.st_size)
            if sig != last and st.st_size > 44:
                last = sig
                ts = time.strftime("%H%M%S")
                dst = os.path.join(DST_DIR, "cap_%06d_%s.wav" % (n, ts))
                shutil.copy(SRC, dst)
                n += 1
                caps = sorted(glob.glob(os.path.join(DST_DIR, "cap_*.wav")))
                for old in caps[:-KEEP]:
                    try:
                        os.unlink(old)
                    except OSError:
                        pass
    except Exception:
        pass
    time.sleep(0.4)
