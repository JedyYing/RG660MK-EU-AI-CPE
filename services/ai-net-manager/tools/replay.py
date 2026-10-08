"""离线回放器（设计 15.7 / 18）：把已录 JSONL 重放通过决策链，产出对比报告。

用途：
- 无设备时回归验证（fixed 输入 → 决策序列）；
- A/B 对比：规则评分 vs RSRP-only 基线（设计要求的对照实验）。

输入：radio.jsonl（必需）、qoe.jsonl（可选，按 ts 就近对齐）。
输出：reports/replay_report.json + 控制台摘要。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_net import config as cfgmod                          # noqa: E402
from ai_net.decision import scorer                           # noqa: E402
from ai_net.decision.rules import RuleEngine                 # noqa: E402
from ai_net.decision.state_machine import SelectionStateMachine  # noqa: E402
from ai_net.features.aggregator import FeatureAggregator     # noqa: E402
from ai_net.features.history import CellHistory              # noqa: E402
from ai_net.models.traffic import HeuristicTrafficClassifier  # noqa: E402


def read_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    yield json.loads(ln)
                except ValueError:
                    continue


def replay(radio_path, qoe_path, cfg, *, mode="shadow", speedup=float("inf")):
    from schemas import RadioSample, QoESample
    sel = cfg.get("rules", {}).get("selection", {})
    prof = cfg.get("rules", {}).get("profiles", {})
    weights = cfg.get("traffic_profiles", {}).get("weights", {})
    rules = RuleEngine(sel, prof, cfg.get("traffic_profiles", {}).get("unknown_threshold", 0.6))
    hist = CellHistory()
    agg = FeatureAggregator(history=hist)
    machine = SelectionStateMachine(rules, mode=mode)
    clf = HeuristicTrafficClassifier()

    qoe_by_ts = {}
    for q in read_jsonl(qoe_path):
        qoe_by_ts[q["ts_ms"]] = q
    qoe_list = sorted(qoe_by_ts.items())

    decisions = []
    t0 = None
    prev_cell = object()
    n_radio = 0
    for r in read_jsonl(radio_path):
        n_radio += 1
        s = RadioSample.from_dict(r)
        if t0 is None:
            t0 = s.ts_ms
        now_s = (s.ts_ms - t0) / 1000.0
        agg.add_radio(s)
        machine.note_serving_cell(now_s, s.cell_id or s.pci)
        q = _nearest(qoe_list, s.ts_ms)
        if q:
            agg.add_qoe(QoESample.from_dict(q))
        # 回放通常无 conntrack 录制 → 维持 generic（决策按 generic 画像）
        agg.set_context(machine.dwell_s(now_s) or 0.0, machine.switches_10min(now_s))

        fw = agg.build(s.ts_ms)
        qs = scorer.qoe_score(fw.features, fw.traffic_class, weights)
        cands = []
        for n in s.neighbours:
            if n.is_serving or n.rsrp_dbm is None:
                continue
            sc = scorer.candidate_score(n, fw.traffic_class, hist.probe(CellHistory.neighbour_key(n)))
            if sc.get("score") is None:
                continue
            cands.append({"key": n.key(), "cell_id": n.cid, "rat": n.rat, "arfcn": n.arfcn,
                          "pci": n.pci, "rsrp_dbm": n.rsrp_dbm, "score": sc["score"],
                          "source": sc["source"], "reasons": sc["reasons"],
                          "baseline_rsrp_only": scorer.rsrp_only_baseline(n)})
        cands.sort(key=lambda c: c["score"], reverse=True)
        serving_q, _, _ = scorer.radio_quality(fw.features.get("rsrp_mean"),
                                               fw.features.get("rsrq_mean"),
                                               fw.features.get("sinr_mean"))
        obs = {"qoe_score": qs.get("score"),
               "serving_score": None if serving_q is None else round(100 * serving_q, 1),
               "candidates": cands, "best_candidate": cands[0] if cands else None,
               "traffic_class": fw.traffic_class, "traffic_confidence": fw.traffic_confidence,
               "model_version": clf.model_version, "modem_ok": True,
               "serving_cell": dict(fw.cell)}
        for d in machine.step(now_s, obs):
            decisions.append({"t_s": round(now_s, 1), "decision": d.to_dict(),
                              "serving_vs_best": _gain(obs),
                              "best_baseline_gain": _gain(obs, baseline=True)})
    return {"n_radio": n_radio, "n_decisions": len(decisions), "decisions": decisions,
            "final_state": machine.state}


def _gain(obs, baseline=False):
    s = obs.get("serving_score")
    b = obs.get("best_candidate")
    if not b or s is None:
        return None
    v = b.get("baseline_rsrp_only") if baseline else b.get("score")
    return None if v is None else round(v - s, 1)


def _nearest(sorted_list, ts_ms, tol_ms=1500):
    if not sorted_list:
        return None
    lo, hi = 0, len(sorted_list) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        d = abs(sorted_list[mid][0] - ts_ms)
        if best is None or d < best[0]:
            best = (d, sorted_list[mid][1])
        if sorted_list[mid][0] < ts_ms:
            lo = mid + 1
        else:
            hi = mid - 1
    return best[1] if best and best[0] <= tol_ms else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="replay recorded JSONL through decision chain")
    ap.add_argument("--radio", required=True)
    ap.add_argument("--qoe", default=None)
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "..", "config"))
    ap.add_argument("--out", default="reports/replay_report.json")
    ap.add_argument("--mode", default="shadow")
    args = ap.parse_args(argv)
    cfg = cfgmod.load_config_dir(args.config)
    rep = replay(args.radio, args.qoe, cfg, mode=args.mode)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("radio samples: %d, decisions: %d, final state: %s"
          % (rep["n_radio"], rep["n_decisions"], rep["final_state"]))
    for d in rep["decisions"][:10]:
        dd = d["decision"]
        print("  t=%ss %s trigger=%s action=%s blocked=%s"
              % (d["t_s"], dd["state"], dd["trigger"], dd["action"]["type"],
                 ";".join(dd["blocked_by"] or [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
