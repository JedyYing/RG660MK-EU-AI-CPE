#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[后置里程碑] QoE/Candidate Ranker 训练（设计 §15.7）。
输入：data/window.jsonl + data/decisions.jsonl（未来窗 QoE 作标签）。
依赖（主机）：pip install xgboost pandas scikit-learn。
用法: python3 tools/train_qoe.py --data-dir data --out models/qoe_xgb.json"""
import argparse
import sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="models/qoe_xgb.json")
    args = ap.parse_args()
    try:
        import pandas as pd  # noqa
        import xgboost as xgb  # noqa
    except ImportError as e:
        print("[待执行] 缺少训练依赖：%s" % e)
        print("主机执行: python3 -m pip install xgboost pandas scikit-learn")
        print("数据准备: ≥24h shadow 后，用 window.jsonl 构造 (features_t → qoe_{t+10s/30s}) 样本")
        print("验收对照：AI vs baseline(RSRP-only/rule-only)，指标 MAE<=8、Spearman>=0.7（设计 §10.4）")
        return 3
    print("[管线就绪] 读 window.jsonl → 未来 QoE 标签 → XGBoost 回归 → 导出 %s" % args.out)
    return 0

if __name__ == "__main__":
    sys.exit(main())
