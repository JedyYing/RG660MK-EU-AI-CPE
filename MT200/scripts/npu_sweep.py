#!/usr/bin/env python3
"""Sweep QNN HTP backend-extension knobs and report prefill / decode rates.

Each case rewrites htp_backend_ext_config_sharing.json from the pristine .orig,
runs genie-t2t-run with the same prompt, and reads the QNN profile. Runs N times
per case and reports the median, because single runs vary by ~5%.
"""
import json
import os
import statistics
import subprocess
import sys

BASE = "/data/local/tmp/qnn/deepseek-7b"
CFG = os.path.join(BASE, "deepseek-7b-htp_sharing.json")
EXT = os.path.join(BASE, "htp_backend_ext_config_sharing.json")
PROMPT = "/tmp/long380.txt"
PROFILE = "/tmp/sweep_profile.json"
REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

with open(EXT + ".orig", encoding="utf-8") as fh:
    ORIG = json.load(fh)


def apply(core_over):
    d = json.loads(json.dumps(ORIG))
    d["devices"][0]["cores"][0].update(core_over)
    with open(EXT, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)


def run_once():
    if os.path.exists(PROFILE):
        os.unlink(PROFILE)
    proc = subprocess.run(["genie-t2t-run", "-c", CFG, "--prompt_file", PROMPT,
                           "--profile", PROFILE],
                          capture_output=True, text=True, timeout=300)
    if not os.path.exists(PROFILE) or proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, (err[-1][:120] if err else "rc=%d" % proc.returncode)
    with open(PROFILE, encoding="utf-8") as fh:
        d = json.load(fh)
    for e in d["components"][0]["events"]:
        if e.get("type") == "GenieDialog_query":
            return (e["prompt-processing-rate"]["value"],
                    e["token-generation-rate"]["value"]), "ok"
    return None, "no query event"


CASES = {
    "A burst (orig)": {},
    "B burst + vtcm8": {"vtcm_mb": 8},
    "C burst + hvx4": {"num_hvx_threads": 4},
    "D sustained_hp": {"perf_profile": "sustained_high_performance"},
    "E burst + vtcm8 + hvx4": {"vtcm_mb": 8, "num_hvx_threads": 4},
}

print("repeats per case:", REPEATS, flush=True)
for name, over in CASES.items():
    apply(over)
    pre, dec, notes = [], [], ""
    for _ in range(REPEATS):
        res, note = run_once()
        if res is None:
            notes = note
            continue
        pre.append(res[0])
        dec.append(res[1])
    if pre:
        print("%-24s prefill=%6.1f tok/s  decode=%5.2f tok/s  n=%d %s"
              % (name, statistics.median(pre), statistics.median(dec), len(pre), notes), flush=True)
    else:
        print("%-24s FAILED: %s" % (name, notes), flush=True)

apply({})
print("restored original ext config", flush=True)
