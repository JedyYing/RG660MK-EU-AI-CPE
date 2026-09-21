#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""假阳性率统计：对全部存档录音跑 无prompt vs 唤醒词prompt，统计 wake 命中。"""
import subprocess, time, os, sys, glob
sys.path.insert(0, "/data/ai_cpe")
import voice_assistant as va

caps = sorted(glob.glob("/tmp/cap_*.wav"))
print("样本数: %d" % len(caps), flush=True)

def run(path, prompt):
    cmd = [va.WHISPER, "-m", va.MODEL, "-f", path, "-l", "zh", "--no-timestamps", "-np",
           "-ac", str(va.asr_context(path))]
    if prompt:
        cmd += ["--prompt", prompt]
    try:
        r = subprocess.run(cmd, capture_output=True,
                           env=dict(os.environ, LD_LIBRARY_PATH=va.WHISPER_LIB), timeout=15)
        return r.stdout.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>"

wake_hits = 0
for f in caps:
    base = os.path.basename(f)
    a = run(f, None)
    b = run(f, "你好小皮")
    wa = va.is_wake(a) if a and not a.startswith("<") else False
    wb = va.is_wake(b) if b and not b.startswith("<") else False
    if wb and not wa:
        wake_hits += 1
    flg = " <<< 偏置新增wake!" if (wb and not wa) else (" [wake]" if wa else "")
    print("%-24s none=%-18r wakeP=%-18r%s" % (base, a[:16], b[:16], flg), flush=True)

print("偏置导致的新增 wake 命中: %d / %d" % (wake_hits, len(caps)), flush=True)
print("DONE", flush=True)
