"""Shadow 数据质量报告（设计 18 交付物之一）。

输入：一次 shadow run 的 log 目录（radio/qoe/features/decision/events JSONL）。
输出：reports/shadow_data_quality.json + 控制台摘要，关注：
- AT/解析成功率（radio.source 缺 ecellmeas 的比例、missing 字段频次）
- 采集密度（实际采样间隔 vs 配置）+ 缺口（>10s 空档次数）
- QoE 覆盖率（rtt/loss/active_flows 缺失比例）
- 决策统计（触发原因、拦截原因 top、动作类型）——shadow 期应无真实执行
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    bad = 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except ValueError:
                bad += 1
    return out


def _quantile_nr(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    import math
    k = max(1, min(len(s), int(math.ceil(q * len(s)))))     # 最近秩
    return s[k - 1]


def analyse(log_dir: str) -> dict:
    radio = read_jsonl(os.path.join(log_dir, "radio.jsonl"))
    qoe = read_jsonl(os.path.join(log_dir, "qoe.jsonl"))
    feats = read_jsonl(os.path.join(log_dir, "features.jsonl"))
    dec = read_jsonl(os.path.join(log_dir, "decision.jsonl"))
    ev = read_jsonl(os.path.join(log_dir, "events.jsonl"))

    ts = [r["ts_ms"] / 1000.0 for r in radio]
    gaps = [round(b - a, 1) for a, b in zip(ts, ts[1:]) if b - a > 10]
    missing_counter = Counter()
    for r in radio:
        for m in r.get("missing") or []:
            missing_counter[m] += 1
    no_serving = sum(1 for r in radio if not r.get("cell_id"))
    prov = sum(1 for r in radio if "ecellmeas" in (r.get("source") or ""))

    qmiss = Counter()
    for q in qoe:
        for m in q.get("missing") or []:
            qmiss[m] += 1

    triggers = Counter(d.get("trigger") for d in dec)
    blocked = Counter()
    for d in dec:
        for b in d.get("blocked_by") or []:
            blocked[str(b).split("(")[0]] += 1
    actions = Counter(d.get("action", {}).get("type") for d in dec)
    executed = sum(1 for d in dec
                   if (d.get("action", {}).get("result") or {}).get("ok") is True
                   and "not executed" not in str(
                       (d.get("action", {}).get("result") or {}).get("detail", "")))

    q_rtt = [q["rtt_ms_p50"] for q in qoe if q.get("rtt_ms_p50") is not None]
    dur = (ts[-1] - ts[0]) if len(ts) >= 2 else 0.0
    return {
        "log_dir": log_dir,
        "duration_s": round(dur, 1),
        "radio": {
            "n": len(radio), "with_ecellmeas": prov, "missing_serving": no_serving,
            "sample_interval_p50_s": _quantile_nr(
                [round(b - a, 2) for a, b in zip(ts, ts[1:])], 0.5),
            "gaps_gt_10s": len(gaps), "worst_gap_s": max(gaps) if gaps else None,
            "missing_field_freq": dict(missing_counter.most_common(12)),
        },
        "qoe": {
            "n": len(qoe), "rtt_ms_p50": _quantile_nr(q_rtt, 0.5),
            "rtt_ms_p95": _quantile_nr(q_rtt, 0.95),
            "missing_field_freq": dict(qmiss.most_common(12)),
        },
        "features": {"n": len(feats)},
        "decisions": {
            "n": len(dec), "triggers": dict(triggers),
            "action_types": dict(actions), "executed_real_actions": executed,
            "blocked_top": dict(blocked.most_common(8)),
            "executor_levels": dict(Counter(d.get("executor_level") for d in dec)),
        },
        "events": dict(Counter(e.get("kind") for e in ev)),
        "notes": [
            "shadow 期 executed_real_actions 必须为 0（L0 不写 Modem）",
            "radio.missing_field_freq 里 ca_* 恒缺属预期（本固件不可得）",
            "sample_interval_p50_s 应接近配置 sample_interval_s",
        ],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="shadow run data quality report")
    ap.add_argument("--log-dir", default="/data/ai_net/log")
    ap.add_argument("--out", default=os.path.join(_ROOT, "reports",
                                                  "shadow_data_quality.json"))
    args = ap.parse_args(argv)
    rep = analyse(args.log_dir)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: rep[k] for k in ("duration_s", "radio", "qoe", "decisions")},
                     ensure_ascii=False, indent=1)[:2500])
    print("→ %s" % args.out)
    if rep["decisions"]["executed_real_actions"]:
        print("⚠ shadow 期出现真实执行动作，请立即检查 executor 配置！")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
