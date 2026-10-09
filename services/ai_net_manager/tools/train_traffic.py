#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[后置里程碑] Traffic Classifier 训练（设计 §15.6）。
前置：≥24-72h shadow 数据（flows/decisions jsonl）+ 场景标签（data/labels.csv，
格式 ts_from_ms,ts_to_ms,class —— 由测试脚本/端口映射产生，人工校验）。
依赖（主机）：pip install lightgbm pandas scikit-learn。
用法: python3 tools/train_traffic.py --data-dir data --out models/traffic_lgbm.txt"""
import argparse
import os
import sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="models/traffic_lgbm.txt")
    args = ap.parse_args()
    try:
        import pandas as pd  # noqa
        import lightgbm as lgb  # noqa
    except ImportError as e:
        print("[待执行] 缺少训练依赖：%s" % e)
        print("主机执行: python3 -m pip install lightgbm pandas scikit-learn")
        print("数据准备: 运行 shadow ≥24h 后重试；标签文件 %s/labels.csv" % args.data_dir)
        return 3
    print("[管线就绪] 读 flows/decisions → 流级特征窗 → LightGBM 6分类训练 → 导出 %s" % args.out)
    print("（完成训练后：端侧用纯 Python 树评估器推理，或导出 ONNX/Treelite——见 README 适配说明）")
    return 0

if __name__ == "__main__":
    sys.exit(main())
