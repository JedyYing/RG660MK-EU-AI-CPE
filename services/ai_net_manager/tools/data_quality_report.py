#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据质量报告（设计 §14.3：字段覆盖率可报告）。
用法: python3 tools/data_quality_report.py --data-dir data [--out reports/data_quality.md]"""
import argparse
import json
import os
import sys

RADIO_FIELDS = ["rat", "reg_state", "radio_on", "rsrp_dbm", "cell_id", "tac"]
QOE_FIELDS = ["rtt_ms", "dl_mbps", "ul_mbps"]


def load_jsonl(path, limit=200000):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for i, ln in enumerate(f):
                if i >= limit:
                    break
                ln = ln.strip()
                if ln:
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
    except OSError:
        pass
    return rows


def coverage(rows, fields):
    n = len(rows)
    out = {}
    for f in fields:
        have = sum(1 for r in rows if r.get(f) is not None)
        out[f] = (have / n * 100.0) if n else 0.0
    return out


def span(rows):
    if not rows:
        return None, None
    ts = [r.get("ts_ms") for r in rows if r.get("ts_ms")]
    if not ts:
        return None, None
    return min(ts), max(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    d = args.data_dir
    radio = load_jsonl(os.path.join(d, "radio.jsonl"))
    qoe = load_jsonl(os.path.join(d, "qoe.jsonl"))
    flows = load_jsonl(os.path.join(d, "flows.jsonl"))
    dec = load_jsonl(os.path.join(d, "decisions.jsonl"))
    win = load_jsonl(os.path.join(d, "window.jsonl"))

    L = ["# ai-net-manager 数据质量报告", ""]
    L.append("| 数据集 | 样本数 | 时间跨度 |")
    L.append("|---|---|---|")
    for name, rows in (("radio", radio), ("qoe", qoe), ("flows", flows),
                       ("window", win), ("decisions", dec)):
        a, b = span(rows)
        sp = "-" if a is None else "%.1f h" % ((b - a) / 3.6e6)
        L.append("| %s | %d | %s |" % (name, len(rows), sp))
    L += ["", "## 字段覆盖率（radio）", "", "| 字段 | 覆盖率 |", "|---|---|"]
    for k, v in coverage(radio, RADIO_FIELDS).items():
        L.append("| %s | %.1f%% |" % (k, v))
    L += ["", "## 字段覆盖率（qoe）", "", "| 字段 | 覆盖率 |", "|---|---|"]
    for k, v in coverage(qoe, QOE_FIELDS).items():
        L.append("| %s | %.1f%% |" % (k, v))
    if radio:
        dur = (span(radio)[1] - span(radio)[0]) / 1000.0 if span(radio)[0] else 0
        if dur > 10:
            rate = len(radio) / dur
            L += ["", "实测 radio 采样率: %.2f Hz（目标 1 Hz；设备负载高时允许下调）" % rate]
    if dec:
        acts = {}
        for r in dec:
            a = r.get("action") or "?"
            acts[a] = acts.get(a, 0) + 1
        L += ["", "## 决策动作分布", ""] + ["- %s: %d" % (k, v) for k, v in sorted(acts.items(), key=lambda kv: -kv[1])]
    md = "\n".join(L) + "\n"
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("data quality report -> %s" % args.out)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
