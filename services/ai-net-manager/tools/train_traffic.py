"""业务分类训练（设计 5.1/15.7；PC 侧运行，产物供端侧纯 Python 推理）。

数据来源：
  --data labels.jsonl   现场/回放标注数据（每行 {"features": {...}, "label": "video"}）
  缺省                 合成弱标注数据：按六类业务的原型分布采样，用于打通
                       「训练 → 校验导出 → 端侧推理 → 报告」链路。
                       ⚠ 合成数据上的精度不代表现场效果，报告会显式标注。

输出：
  models/traffic_clf.json        模型（body sha256 自校验，端侧 registry 校验后加载）
  reports/traffic_train_report.json  指标 + 数据来源 + 特征顺序 + 版本
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_net.models import gbdt                                  # noqa: E402
from ai_net.models.registry import body_sha256                  # noqa: E402
from ai_net.models.traffic import (CLASSES, FLOW_FEATURE_ORDER,  # noqa: E402
                                   HeuristicTrafficClassifier, flow_features)

# 各类业务的流量原型（均值/范围来自设计 5.3 描述的业务特征）
ARCHETYPES = {
    "gaming":     {"dl_mbps": (0.1, 1.5), "ul_mbps": (0.1, 1.0), "udp_ratio": (0.5, 1.0),
                   "pkt_size_p50": (60, 220), "small_pkt_ratio": (0.5, 1.0),
                   "new_flow_ratio": (0.0, 0.3), "rtc": (0.3, 0.8)},
    "video":      {"dl_mbps": (3.0, 30.0), "ul_mbps": (0.05, 0.8), "udp_ratio": (0.0, 0.3),
                   "pkt_size_p50": (400, 1400), "small_pkt_ratio": (0.0, 0.2),
                   "new_flow_ratio": (0.0, 0.3), "rtc": (0.0, 0.1)},
    "conference": {"dl_mbps": (0.5, 6.0), "ul_mbps": (0.3, 4.0), "udp_ratio": (0.4, 1.0),
                   "pkt_size_p50": (150, 700), "small_pkt_ratio": (0.1, 0.6),
                   "new_flow_ratio": (0.0, 0.2), "rtc": (0.7, 1.0)},
    "voip":       {"dl_mbps": (0.02, 0.4), "ul_mbps": (0.02, 0.3), "udp_ratio": (0.6, 1.0),
                   "pkt_size_p50": (80, 260), "small_pkt_ratio": (0.6, 1.0),
                   "new_flow_ratio": (0.0, 0.1), "rtc": (0.6, 1.0)},
    "camera":     {"dl_mbps": (0.1, 1.0), "ul_mbps": (1.0, 12.0), "udp_ratio": (0.3, 1.0),
                   "pkt_size_p50": (300, 1200), "small_pkt_ratio": (0.0, 0.4),
                   "new_flow_ratio": (0.0, 0.1), "rtc": (0.0, 0.2)},
    "backup":     {"dl_mbps": (10.0, 300.0), "ul_mbps": (0.5, 8.0), "udp_ratio": (0.0, 0.1),
                   "pkt_size_p50": (800, 1500), "small_pkt_ratio": (0.0, 0.1),
                   "new_flow_ratio": (0.0, 0.05), "rtc": (0.0, 0.05)},
}


def synth_rows(n_per_class: int, rng: random.Random) -> list:
    rows = []
    for cls in CLASSES:
        a = ARCHETYPES[cls]
        for _ in range(n_per_class):
            def r(k, lo=None, hi=None):
                lo = lo if lo is not None else a[k][0]
                hi = hi if hi is not None else a[k][1]
                return round(rng.uniform(lo, hi), 3)
            dl, ul = r("dl_mbps"), r("ul_mbps")
            udp = r("udp_ratio")
            ff = {
                "n_active": rng.randint(1, 40),
                "dl_mbps": dl, "ul_mbps": ul,
                "ul_ratio": round(ul / (ul + dl), 3) if (ul + dl) > 0 else None,
                "udp_ratio": udp, "tcp_ratio": round(1 - udp, 3),
                "pkt_size_p50": r("pkt_size_p50"),
                "small_pkt_ratio": r("small_pkt_ratio"),
                "new_flow_ratio": r("new_flow_ratio"),
                "top_ports": ([("udp/3478", 3)] if rng.random() < r("rtc") else [("tcp/443", 5)]),
            }
            feat, missing = flow_features(ff)
            if missing:
                continue
            rows.append({"features": feat, "label": cls, "source": "synthetic"})
    rng.shuffle(rows)
    return rows


def load_rows(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            d = json.loads(ln)
            feat = d.get("features") or {}
            missing = [k for k in FLOW_FEATURE_ORDER if feat.get(k) is None]
            if missing or d.get("label") not in CLASSES:
                continue                      # 缺特征/坏标签样本不进训练集
            rows.append({"features": {k: feat[k] for k in FLOW_FEATURE_ORDER},
                         "label": d["label"], "source": d.get("source", "collected")})
    return rows


def to_xy(rows: list) -> tuple:
    X = [[float(r["features"][k]) for k in FLOW_FEATURE_ORDER] for r in rows]
    y = [r["label"] for r in rows]
    return X, y


def main(argv=None):
    ap = argparse.ArgumentParser(description="train traffic classifier (PC side)")
    ap.add_argument("--data", default=None, help="labels.jsonl（缺省=合成弱标注）")
    ap.add_argument("--out", default=os.path.join(_ROOT, "models", "traffic_clf.json"))
    ap.add_argument("--report", default=os.path.join(_ROOT, "reports",
                                                     "traffic_train_report.json"))
    ap.add_argument("--rounds", type=int, default=40)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--lr", type=float, default=0.25)
    ap.add_argument("--n-per-class", type=int, default=250, help="合成模式每类样本数")
    ap.add_argument("--seed", type=int, default=20261008)
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    source = "synthetic_rule_archetypes" if not args.data else args.data
    rows = load_rows(args.data) if args.data else synth_rows(args.n_per_class, rng)
    if len(rows) < 50:
        print("dataset too small: %d rows" % len(rows))
        return 1
    rng.shuffle(rows)
    cut = int(len(rows) * 0.8)
    train_rows, val_rows = rows[:cut], rows[cut:]
    Xtr, ytr = to_xy(train_rows)
    Xva, yva = to_xy(val_rows)

    model = gbdt.train(Xtr, ytr, list(CLASSES), feature_order=list(FLOW_FEATURE_ORDER),
                       rounds=args.rounds, depth=args.depth, lr=args.lr)
    metrics = gbdt.evaluate(model, Xva, yva)

    # A/B：启发式基线在同一验证集上的表现（设计 15.7 对照）
    base = HeuristicTrafficClassifier()
    hit = tot = 0
    for r in val_rows:
        ff = dict(r["features"])
        pred = base.predict(ff, 0)
        tot += 1
        hit += 1 if pred.cls == r["label"] else 0
    baseline_acc = round(hit / max(1, tot), 4)

    model["version"] = "gbdt-r%d-d%d-lr%.2f-%s" % (args.rounds, args.depth, args.lr,
                                                   source.split("/")[-1][:24])
    model["sha256_scope"] = "body"
    model["sha256"] = body_sha256(model)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False)

    report = {
        "trained_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "data_source": source,
        "data_warning": ("合成/弱标注数据：仅验证「训练→导出→端侧推理」链路与指标口径，"
                         "精度不代表现场效果；现场标注数据到位后需重训"
                         if not args.data else None),
        "n_train": len(train_rows), "n_val": len(val_rows),
        "feature_order": list(FLOW_FEATURE_ORDER),
        "params": {"rounds": args.rounds, "depth": args.depth, "lr": args.lr,
                   "seed": args.seed},
        "model_version": model["version"], "model_sha256": model["sha256"],
        "model_file": os.path.relpath(args.out, _ROOT),
        "val_metrics": metrics,
        "baseline_heuristic_val_accuracy": baseline_acc,
        "ab_verdict": ("model_ge_baseline" if metrics["accuracy"] >= baseline_acc
                       else "baseline_better"),
    }
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    # 端侧加载自检（registry 校验 + 一次推理）
    from ai_net.models.registry import load_model
    from ai_net.models.traffic import ModelTrafficClassifier
    doc = load_model(args.out)
    clf = ModelTrafficClassifier(doc)
    pred = clf.predict({"dl_mbps": 20.0, "ul_mbps": 0.3, "ul_ratio": 0.015,
                        "udp_ratio": 0.1, "tcp_ratio": 0.9, "pkt_size_p50": 900,
                        "small_pkt_ratio": 0.05, "new_flow_ratio": 0.02,
                        "n_active": 12, "top_ports": []}, 0)
    print("val accuracy=%.3f (heuristic baseline=%.3f)  model=%s"
          % (metrics["accuracy"], baseline_acc, model["version"]))
    print("per-class:", json.dumps(metrics["per_class"], ensure_ascii=False))
    print("端侧加载自检: %s conf=%.2f version=%s" % (pred.cls, pred.confidence,
                                                     clf.model_version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
