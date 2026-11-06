#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成日期报时片段：/data/ai_cpe/tts_date/000..050.wav
0=现在是, 1-12=一月..十二月, 13-43=一日..三十一日, 44-50=星期一..星期日
"""
import os, subprocess, glob, shutil, time

OUT = "/data/ai_cpe/tts_date"
TMP = "/tmp/dateclips"
L = "/data/ai_cpe/glibc/ld-linux-aarch64.so.1"
P = "/data/ai_cpe/piper"

CN = "零一二三四五六七八九"


def cn_num(n):
    if n < 10:
        return CN[n]
    if n < 20:
        return "十" + (CN[n % 10] if n % 10 else "")
    if n < 100:
        return CN[n // 10] + "十" + (CN[n % 10] if n % 10 else "")
    return str(n)


texts = ["现在是"]
texts += [cn_num(m) + "月" for m in range(1, 13)]          # 1-12
texts += [cn_num(d) + "日" for d in range(1, 32)]          # 13-43
texts += ["星期" + w for w in "一二三四五六日"]              # 44-50

os.makedirs(OUT, exist_ok=True)
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

env = dict(os.environ)
env["ESPEAK_DATA_PATH"] = P
env["LD_LIBRARY_PATH"] = "/data/ai_cpe/glibc:" + P

t0 = time.time()
r = subprocess.run([L, "--library-path", "/data/ai_cpe/glibc:" + P, P + "/piper-bin",
                    "--model", P + "/zh_CN-huayan-medium.onnx", "--length_scale", "0.92",
                    "--output_dir", TMP],
                   input="\n".join(texts).encode() + b"\n",
                   capture_output=True, env=env, timeout=1800)
print("piper rc=%d %.1fs" % (r.returncode, time.time() - t0), flush=True)
files = sorted(glob.glob(TMP + "/*.wav"))
print("generated", len(files), "expected", len(texts), flush=True)
if len(files) != len(texts):
    raise SystemExit("mismatch")
for i, f in enumerate(files):
    shutil.copyfile(f, os.path.join(OUT, "%03d.wav" % i))
with open(os.path.join(OUT, "clips.txt"), "w", encoding="utf-8") as fh:
    for i, t in enumerate(texts):
        fh.write("%03d %s\n" % (i, t))
print("installed to", OUT, flush=True)
