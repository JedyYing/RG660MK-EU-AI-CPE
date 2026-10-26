#!/usr/bin/env python3
"""Sweep the remaining Genie/HTP knobs for the 7B bundle and report prefill/decode.

Each case rewrites the dialog JSON's QnnHtp section (or the ext config), runs one
prompt, reads the QNN profile, and repeats 3x for a median. Restores the original
dialog + ext config at the end.
"""
import json
import os
import shutil
import statistics
import subprocess

BASE = "/data/local/tmp/qnn/deepseek-7b"
CFG = os.path.join(BASE, "deepseek-7b-htp_sharing.json")
EXT = os.path.join(BASE, "htp_backend_ext_config_sharing.json")
PROMPT = "/tmp/long380.txt"
PROFILE = "/tmp/sweep_profile.json"
REPEATS = 3

shutil.copy(CFG, "/tmp/cfg.orig")
shutil.copy(EXT, "/tmp/ext.orig")
ORIG_CFG = json.load(open("/tmp/cfg.orig", encoding="utf-8"))
ORIG_EXT = json.load(open("/tmp/ext.orig", encoding="utf-8"))


def apply(htp_over=None, ext_over=None):
    cfg = json.loads(json.dumps(ORIG_CFG))
    if htp_over:
        cfg["dialog"]["engine"]["backend"]["QnnHtp"].update(htp_over)
    ext = json.loads(json.dumps(ORIG_EXT))
    if ext_over:
        ext["devices"][0]["cores"][0].update(ext_over)
    with open(CFG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    with open(EXT, "w", encoding="utf-8") as fh:
        json.dump(ext, fh, indent=2)


def run_once():
    if os.path.exists(PROFILE):
        os.unlink(PROFILE)
    p = subprocess.run(["genie-t2t-run", "-c", CFG, "--prompt_file", PROMPT,
                        "--profile", PROFILE],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0 or not os.path.exists(PROFILE):
        return None
    d = json.load(open(PROFILE, encoding="utf-8"))
    for e in d["components"][0]["events"]:
        if e.get("type") == "GenieDialog_query":
            return (e["prompt-processing-rate"]["value"],
                    e["token-generation-rate"]["value"])
    return None


CASES = {
    "baseline":                 dict(),
    "spill-fill 32":            dict(htp_over={"spill-fill-bufsize": 32}),
    "spill-fill 128":           dict(htp_over={"spill-fill-bufsize": 128}),
    "spill-fill 256":           dict(htp_over={"spill-fill-bufsize": 256}),
    "mmap-budget 256":          dict(htp_over={"mmap-budget": 256}),
    "no weight sharing":        dict(ext_over={"context": {"weight_sharing_enabled": False}}),
}

print("case                      prefill    decode   n", flush=True)
for name, kw in CASES.items():
    apply(**kw)
    pre, dec = [], []
    for _ in range(REPEATS):
        r = run_once()
        if r:
            pre.append(r[0]); dec.append(r[1])
    if pre:
        print("%-24s %7.1f  %7.2f  %d" % (name, statistics.median(pre),
                                          statistics.median(dec), len(pre)), flush=True)
    else:
        print("%-24s  FAILED" % name, flush=True)

apply()
shutil.copy("/tmp/cfg.orig", CFG)
shutil.copy("/tmp/ext.orig", EXT)
print("restored original dialog + ext config", flush=True)
