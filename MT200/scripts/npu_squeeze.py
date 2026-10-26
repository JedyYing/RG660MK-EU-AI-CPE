#!/usr/bin/env python3
"""Two squeezes on the MT200 NPU path:

1) dialog save/restore: does --restore skip the ~7 s model init?
2) concurrency: two genie processes at once — does aggregate throughput add up,
   or does the DSP serialise them?

Prints wall times and the QNN profile rates for each run.
"""
import json
import os
import subprocess
import time

BASE = "/data/local/tmp/qnn/deepseek-7b"
CFG = os.path.join(BASE, "deepseek-7b-htp_sharing.json")
PROMPT = "/tmp/long380.txt"
STATE = "/tmp/dialog_state.bin"


def prof(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    out = {}
    for e in d["components"][0]["events"]:
        if e.get("type") == "GenieDialog_create":
            out["init_s"] = round(e["duration"] / 1e6, 2)
        if e.get("type") == "GenieDialog_query":
            out["prefill_tps"] = round(e["prompt-processing-rate"]["value"], 1)
            out["decode_tps"] = round(e["token-generation-rate"]["value"], 2)
            out["gen_tokens"] = e["num-generated-tokens"]["value"]
    return out


def run(extra, profile):
    if os.path.exists(profile):
        os.unlink(profile)
    t0 = time.time()
    p = subprocess.run(["genie-t2t-run", "-c", CFG, "--prompt_file", PROMPT,
                        "--profile", profile] + extra,
                       capture_output=True, text=True, timeout=300)
    return round(time.time() - t0, 2), p.returncode, prof(profile)


print("=== 1) save / restore ===", flush=True)
if os.path.exists(STATE):
    os.unlink(STATE)
w1, rc1, p1 = run(["--save", STATE], "/tmp/p_save.json")
print("run with --save   wall=%ss rc=%s %s state=%sB"
      % (w1, rc1, p1, os.path.getsize(STATE) if os.path.exists(STATE) else "none"), flush=True)
w2, rc2, p2 = run(["--restore", STATE], "/tmp/p_restore.json")
print("run with --restore wall=%ss rc=%s %s" % (w2, rc2, p2), flush=True)

print("=== 2) concurrency (2 processes) ===", flush=True)
procs = []
for i in range(2):
    prof_i = "/tmp/p_conc_%d.json" % i
    if os.path.exists(prof_i):
        os.unlink(prof_i)
    procs.append((subprocess.Popen(["genie-t2t-run", "-c", CFG, "--prompt_file", PROMPT,
                                    "--profile", prof_i],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), prof_i))
t0 = time.time()
for p, _ in procs:
    p.wait(timeout=600)
wall = round(time.time() - t0, 2)
for i, (_, prof_i) in enumerate(procs):
    print("proc%d %s" % (i, prof(prof_i)), flush=True)
print("both finished in %ss" % wall, flush=True)
