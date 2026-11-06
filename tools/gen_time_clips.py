#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性生成「报时」用的短语音片段（piper，单进程批处理）。
输出：/data/ai_cpe/tts_time/000.wav ... 076.wav + clips.txt（序号=文本）
"""
import os, subprocess, glob, shutil, time

OUT = "/data/ai_cpe/tts_time"
TMP = "/tmp/timeclips"
L = "/data/ai_cpe/glibc/ld-linux-aarch64.so.1"
P = "/data/ai_cpe/piper"

texts = ["现在是"]
texts += ["上午", "下午", "晚上", "凌晨"]                    # 1-4
texts += ["%s点" % h for h in ["一","二","三","四","五","六","七","八","九","十","十一","十二"]]  # 5-16
texts += ["整"]                                              # 17
texts += ["零%s分" % d for d in ["一","二","三","四","五","六","七","八","九"]]  # 18-26
texts += ["%d分" % m for m in range(10, 60)]                 # 27-76

os.makedirs(OUT, exist_ok=True)
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

env = dict(os.environ)
env["ESPEAK_DATA_PATH"] = P
env["LD_LIBRARY_PATH"] = "/data/ai_cpe/glibc:" + P

t0 = time.time()
r = subprocess.run([L, "--library-path", "/data/ai_cpe/glibc:" + P, P + "/piper-bin",
                    "--model", P + "/zh_CN-huayan-medium.onnx", "--output_dir", TMP],
                   input="\n".join(texts).encode() + b"\n",
                   capture_output=True, env=env, timeout=1800)
print("piper rc=%d %.1fs" % (r.returncode, time.time() - t0), flush=True)
if r.returncode != 0:
    print((r.stderr or b"").decode("utf-8", "replace")[-400:], flush=True)

files = sorted(glob.glob(TMP + "/*.wav"))
print("generated", len(files), "expected", len(texts), flush=True)
if len(files) != len(texts):
    raise SystemExit("clip count mismatch")

for i, f in enumerate(files):
    shutil.copyfile(f, os.path.join(OUT, "%03d.wav" % i))
with open(os.path.join(OUT, "clips.txt"), "w", encoding="utf-8") as fh:
    for i, t in enumerate(texts):
        fh.write("%03d %s\n" % (i, t))
print("installed to", OUT, flush=True)
