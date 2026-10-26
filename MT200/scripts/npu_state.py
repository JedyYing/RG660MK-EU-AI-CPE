#!/usr/bin/env python3
"""Does Genie's dialog save/restore skip the ~7 s model init? (single process only)"""
import json
import os
import shutil
import subprocess
import time

BASE = "/data/local/tmp/qnn/deepseek-7b"
CFG = os.path.join(BASE, "deepseek-7b-htp_sharing.json")
PROMPT = "/tmp/long380.txt"
STATE = "/tmp/genie_state"


def prof(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    out = {}
    for e in d["components"][0]["events"]:
        if e.get("type") == "GenieDialog_create":
            out["init_s"] = round(e["duration"] / 1e6, 2)
        elif e.get("type") == "GenieDialog_query":
            out["prefill_tps"] = round(e["prompt-processing-rate"]["value"], 1)
            out["decode_tps"] = round(e["token-generation-rate"]["value"], 2)
    return out


def run(extra, profile):
    if os.path.exists(profile):
        os.unlink(profile)
    t0 = time.time()
    p = subprocess.run(["genie-t2t-run", "-c", CFG, "--prompt_file", PROMPT,
                        "--profile", profile] + extra,
                       capture_output=True, text=True, timeout=300)
    tail = [l for l in (p.stderr or p.stdout or "").splitlines() if l.strip()][-1:] or [""]
    return round(time.time() - t0, 2), p.returncode, prof(profile), tail[0][:100]


def purge(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.unlink(path)

purge(STATE)

w1, rc1, p1, e1 = run(["--save", STATE], "/tmp/p_save.json")
print("--save     wall=%5.2fs rc=%s %s" % (w1, rc1, p1), flush=True)
print("           state=%s %s" % (("dir %d entries" % len(os.listdir(STATE))) if os.path.isdir(STATE) else ("%s bytes" % os.path.getsize(STATE) if os.path.exists(STATE) else "none"), e1), flush=True)

w2, rc2, p2, e2 = run(["--restore", STATE], "/tmp/p_restore.json")
print("--restore  wall=%5.2fs rc=%s %s" % (w2, rc2, p2), flush=True)
if e2:
    print("           note: %s" % e2, flush=True)
