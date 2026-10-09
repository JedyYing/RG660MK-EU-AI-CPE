#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回放报告（设计 §15.8）：重放 decisions.jsonl，用当前规则复算裁决。
输出：建议次数/被拦截统计/触发分布/预计收益。
用法: python3 tools/replay.py --input data/decisions.jsonl --rules config/rules.json [--out reports/replay_report.md]"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

from ai_net.decision.rules import RuleEngine


def load_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        out.append(json.loads(ln))
                    except Exception:
                        pass
    except OSError:
        pass
    return out


def replay(lines, engine):
    cell = None
    cell_enter = None
    last_switch = None
    switches = []
    adv = {"key": None, "since": None}
    st = {"ticks": len(lines), "actual_recommend": 0, "would_recommend": 0, "gain_sum": 0.0,
          "gain_max": None, "blocked_counts": {}, "trigger_counts": {}, "state_counts": {}}
    events = []
    for d in lines:
        ts = d.get("ts_ms") or 0
        key = (d.get("serving_cell") or {}).get("key") or "unknown"
        if cell is None:
            cell, cell_enter = key, ts
        elif key != cell:
            switches.append(ts)
            last_switch = ts
            cell, cell_enter = key, ts
            adv = {"key": None, "since": None}
        switches = [t for t in switches if ts - t <= 600000]
        dwell = (ts - cell_enter) / 1000.0 if cell_enter is not None else 0.0
        cd = max(0.0, ((last_switch or 0) + 120000 - ts) / 1000.0) if last_switch else 0.0
        score = d.get("serving_score")
        cands = [c for c in (d.get("candidates") or []) if c.get("cell") != key]
        best = cands[0] if cands else None
        gain = (best["score"] - score) if (best and score is not None) else 0.0
        eff = engine.effective(d.get("traffic_class") or "generic")
        if best is not None and gain >= eff.get("min_score_gain", 0):
            if adv["key"] != best["cell"]:
                adv = {"key": best["cell"], "since": ts}
        else:
            adv = {"key": None, "since": None}
        hold = ((ts - adv["since"]) / 1000.0) if adv["since"] else 0.0
        ctx = {"dwell_s": dwell, "cooldown_left_s": cd, "switches_10min": len(switches),
               "gain": gain, "hold_s": hold, "candidate_rsrp": (best or {}).get("rsrp_est"),
               "traffic_class": d.get("traffic_class") or "generic"}
        res = engine.check(ctx)
        trig = d.get("trigger") or "none"
        st["trigger_counts"][trig] = st["trigger_counts"].get(trig, 0) + 1
        ststab = d.get("state") or "?"
        st["state_counts"][ststab] = st["state_counts"].get(ststab, 0) + 1
        if d.get("action") == "recommend":
            st["actual_recommend"] += 1
        if res["allowed"] and trig in ("bad_qoe", "score_gain") and best is not None:
            st["would_recommend"] += 1
            st["gain_sum"] += max(gain, 0.0)
            st["gain_max"] = gain if st["gain_max"] is None else max(st["gain_max"], gain)
            events.append({"ts_ms": ts, "cell": key, "target": best["cell"],
                           "gain": round(gain, 1), "trigger": trig})
        for b in res["blocked_by"]:
            k = b.split("(")[0]
            st["blocked_counts"][k] = st["blocked_counts"].get(k, 0) + 1
    st["would_gain_avg"] = (round(st["gain_sum"] / st["would_recommend"], 1)
                            if st["would_recommend"] else None)
    return st, events


def render_md(st, events, src):
    lines = ["# AI 选网 Replay 报告（Shadow 数据回放）", "",
             "- 输入: `%s`" % src,
             "- 说明: 用当前 rules.json 对每个 tick 重新裁决（简化重演驻留/冷却/保持状态）", "",
             "## 概览", "",
             "| 指标 | 值 |", "|---|---|",
             "| 总 tick 数 | %d |" % st["ticks"],
             "| 实际输出建议次数 | %d |" % st["actual_recommend"],
             "| 复算通过（would recommend） | %d |" % st["would_recommend"],
             "| 平均预计增益 | %s |" % st["would_gain_avg"],
             "| 最大预计增益 | %s |" % (round(st["gain_max"], 1) if st["gain_max"] is not None else None),
             "", "## 被安全规则拦截统计", "",
             "| 规则 | 次数 |", "|---|---|"]
    for k, v in sorted(st["blocked_counts"].items(), key=lambda kv: -kv[1]):
        lines.append("| %s | %d |" % (k, v))
    if not st["blocked_counts"]:
        lines.append("| （无） | 0 |")
    lines += ["", "## 触发分布", "", "| trigger | 次数 |", "|---|---|"]
    for k, v in sorted(st["trigger_counts"].items(), key=lambda kv: -kv[1]):
        lines.append("| %s | %d |" % (k, v))
    lines += ["", "## 建议时刻（前 50 条）", ""]
    if events:
        lines += ["| ts_ms | 当前小区 | 目标小区 | 预计增益 | 触发 |", "|---|---|---|---|---|"]
        for e in events[:50]:
            lines.append("| %d | %s | %s | %s | %s |" % (e["ts_ms"], e["cell"], e["target"], e["gain"], e["trigger"]))
    else:
        lines.append("（无——通常表示单小区环境或规则正确拦截了全部候选）")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--rules", default=os.path.join(ROOT, "config", "rules.json"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    lines = load_jsonl(args.input)
    engine = RuleEngine(json.load(open(args.rules, encoding="utf-8")))
    st, events = replay(lines, engine)
    md = render_md(st, events, args.input)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
        print("replay report -> %s" % args.out)
    print(json.dumps({k: st[k] for k in ("ticks", "actual_recommend", "would_recommend",
                                        "would_gain_avg", "blocked_counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
