# -*- coding: utf-8 -*-
"""回放工具冒烟测试（设计 §14.1）。"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_replay_smoke(tmp_path=None):
    lines = [
        {"ts_ms": 1000000, "state": "NORMAL", "serving_cell": {"key": "A"}, "serving_score": 30,
         "candidates": [{"cell": "B", "score": 60, "rsrp_est": -85}], "trigger": "bad_qoe",
         "traffic_class": "generic", "traffic_confidence": 0.0, "action": "none"},
        {"ts_ms": 1002000, "state": "NORMAL", "serving_cell": {"key": "A"}, "serving_score": 31,
         "candidates": [{"cell": "B", "score": 61, "rsrp_est": -85}], "trigger": "bad_qoe",
         "traffic_class": "generic", "traffic_confidence": 0.0, "action": "recommend"},
    ]
    d = tempfile.mkdtemp()
    inp = os.path.join(d, "decisions.jsonl")
    with open(inp, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(json.dumps(ln) + "\n")
    out = os.path.join(d, "replay.md")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "replay.py"),
                        "--input", inp, "--rules", os.path.join(ROOT, "config", "rules.json"),
                        "--out", out], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    txt = open(out, encoding="utf-8").read()
    assert "tick" in txt and "recommend" in txt.lower()
