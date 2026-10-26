#!/usr/bin/env python3
"""Can the NPU run two different workloads at once?

Test A: two LLM dialogs concurrently  -> does aggregate decode exceed 12.25 tok/s?
Test B: one LLM dialog + one CNN stream -> do both keep their solo rates?

Everything is bounded by timeouts so the box cannot wedge; load average is printed
after each case.
"""
import json
import os
import subprocess
import time

BASE = "/data/local/tmp/qnn/deepseek-7b"
CFG = os.path.join(BASE, "deepseek-7b-htp_sharing.json")
LLM_PROMPT = "/tmp/long380.txt"
QNN = "/opt/qnn/sdk/bin/qnn-net-run"
BE = "/opt/qnn/sdk/lib/libQnnHtp.so"
CTX = "/opt/qnn/model/qnn/yolov8n/yolov8n_w8a8_9075.bin"
INLIST = "yolov8n/input_list/input_list_npu.txt"
CNN_CWD = "/opt/qnn/model/qnn"


def rate(profile):
    if not os.path.exists(profile):
        return None
    d = json.load(open(profile, encoding="utf-8"))
    for e in d["components"][0]["events"]:
        if e.get("type") == "GenieDialog_query":
            return (round(e["prompt-processing-rate"]["value"], 1),
                    round(e["token-generation-rate"]["value"], 2))
    return None


def llm(tag):
    prof = "/tmp/prof_%s.json" % tag
    if os.path.exists(prof):
        os.unlink(prof)
    return subprocess.Popen(["genie-t2t-run", "-c", CFG, "--prompt_file", LLM_PROMPT,
                             "--profile", prof],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), prof


def cnn(n):
    return subprocess.Popen([QNN, "--backend", BE, "--retrieve_context", CTX,
                             "--input_list", INLIST, "--num_inferences", str(n),
                             "--output_dir", "/tmp/yolo_out", "--perf_profile", "burst"],
                            cwd=CNN_CWD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load():
    return open("/proc/loadavg").read().split()[:3]


print("solo LLM", flush=True)
p, prof = llm("solo")
t0 = time.time(); p.wait(timeout=200); solo_wall = time.time() - t0
print("  solo: %s  wall=%.1fs  load=%s" % (rate(prof), solo_wall, load()), flush=True)

print("A) two LLM dialogs concurrently", flush=True)
procs = [llm("a1"), llm("a2")]
t0 = time.time()
for pr, _ in procs:
    pr.wait(timeout=240)
wall = time.time() - t0
r1, r2 = rate(procs[0][1]), rate(procs[1][1])
print("  a1=%s a2=%s wall=%.1fs load=%s" % (r1, r2, wall, load()), flush=True)
if r1 and r2:
    print("  aggregate decode = %.2f tok/s (solo %.2f)" %
          (r1[1] + r2[1], rate(prof)[1] if rate(prof) else 0), flush=True)

print("B) LLM + CNN concurrently", flush=True)
p_llm, prof_b = llm("b1")
p_cnn = cnn(20)
t0 = time.time(); p_cnn.wait(timeout=240); cnn_wall = time.time() - t0
p_llm.wait(timeout=240); tot = time.time() - t0
print("  llm=%s cnn_wall=%.1fs total=%.1fs load=%s" %
      (rate(prof_b), cnn_wall, tot, load()), flush=True)
if cnn_wall > 0:
    print("  cnn through 20 inf in %.2fs -> %.2f inf/s" % (cnn_wall, 20 / cnn_wall), flush=True)
