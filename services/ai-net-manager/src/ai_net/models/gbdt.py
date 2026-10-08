"""纯 Python GBDT：多分类梯度提升（训练 + 推理，标准库实现）。

存在理由（设计 5.1 / 13）：
- 端侧 aarch64 无 lightgbm/xgboost，推理必须纯 Python；
- Phase 1 首版模型可来自 PC 侧 LightGBM 或本模块的轻量训练器，
  统一导出为 ai_net.models.registry 校验的 JSON 模型格式。

模型 JSON：
  {schema_version, model_type:"gbdt_multiclass", feature_order:[..], classes:[..],
   base_scores:[..], learning_rate, trees:[[tree,...per class],...per round],
   sha256_scope:"body", sha256}
树节点：{"f": int, "t": float, "l": node, "r": node} 或叶子 {"v": float}
"""
from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# 推理
# ---------------------------------------------------------------------------
def _eval_tree(node: dict, x: list) -> float:
    while "v" not in node:
        node = node["l"] if x[node["f"]] <= node["t"] else node["r"]
    return node["v"]


def predict_scores(model: dict, features: dict) -> list:
    xs = [float(features.get(k)) if features.get(k) is not None else 0.0
          for k in model["feature_order"]]
    scores = list(model["base_scores"])
    lr = model.get("learning_rate", 0.1)
    for rd, per_class in enumerate(model["trees"]):
        for c, tree in enumerate(per_class):
            scores[c] += lr * _eval_tree(tree, xs)
    return scores


def predict_proba(model: dict, features: dict) -> list:
    s = predict_scores(model, features)
    m = max(s)
    e = [math.exp(v - m) for v in s]
    z = sum(e)
    return [v / z for v in e]


def predict(model: dict, features: dict) -> tuple:
    """返回 (类名, 置信度, 分布 dict)。"""
    p = predict_proba(model, features)
    classes = model["classes"]
    i = max(range(len(p)), key=lambda k: p[k])
    return classes[i], round(p[i], 4), {c: round(v, 4) for c, v in zip(classes, p)}


# ---------------------------------------------------------------------------
# 训练（轻量：分位数候选切分 + 方差缩减）
# ---------------------------------------------------------------------------
def _candidates(vals: list, max_splits: int = 16) -> list:
    s = sorted(set(vals))
    if len(s) <= 1:
        return []
    if len(s) <= max_splits:
        return [(s[i] + s[i + 1]) / 2 for i in range(len(s) - 1)]
    step = len(s) / (max_splits + 1)
    return [(s[int(step * (i + 1))] + s[min(len(s) - 1, int(step * (i + 1)) + 1)]) / 2
            for i in range(max_splits)]


def _sse(vals: list) -> float:
    if not vals:
        return 0.0
    m = sum(vals) / len(vals)
    return sum((v - m) ** 2 for v in vals)


def _fit_tree(X: list, g: list, depth: int, min_leaf: int = 5,
              max_splits: int = 12) -> dict:
    """方差缩减回归树；按特征预排序 + 前缀和，每特征 O(n log n)（排序）+ O(n)。"""
    if depth <= 0 or len(g) < 2 * min_leaf:
        return {"v": _mean(g)}
    n = len(g)
    n_feat = len(X[0]) if X else 0
    tot = sum(g)
    sq_tot = sum(v * v for v in g)
    base = sq_tot - tot * tot / n
    best = None
    for f in range(n_feat):
        order = sorted(range(n), key=lambda i: X[i][f])
        vals = [X[i][f] for i in order]
        gv = [g[i] for i in order]
        cand = set(_split_indices(vals, max_splits))
        if not cand:
            continue
        run = 0.0
        run_sq = 0.0
        for j in range(n - 1):
            v = gv[j]
            run += v
            run_sq += v * v
            nl = j + 1
            nr = n - nl
            if nl not in cand or nl < min_leaf or nr < min_leaf:
                continue
            sse_l = run_sq - run * run / nl
            sr = tot - run
            sse_r = (sq_tot - run_sq) - sr * sr / nr
            gain = base - (sse_l + sse_r)
            if best is None or gain > best[0]:
                best = (gain, f, (vals[j] + vals[j + 1]) / 2)
    if best is None or best[0] <= 1e-9:
        return {"v": _mean(g)}
    _, f, t = best
    li = [i for i in range(n) if X[i][f] <= t]
    ri = [i for i in range(n) if X[i][f] > t]
    return {"f": f, "t": t,
            "l": _fit_tree([X[i] for i in li], [g[i] for i in li], depth - 1,
                           min_leaf, max_splits),
            "r": _fit_tree([X[i] for i in ri], [g[i] for i in ri], depth - 1,
                           min_leaf, max_splits)}


def _split_indices(vals: list, max_splits: int) -> list:
    """值变化位置的均匀候选下标（返回左组大小 nl）。"""
    changes = [i + 1 for i in range(len(vals) - 1) if vals[i] != vals[i + 1]]
    if not changes:
        return []
    if len(changes) <= max_splits:
        return changes
    step = len(changes) / max_splits
    return [changes[min(len(changes) - 1, int(step * i))] for i in range(max_splits)]


def _mean(v):
    return 0.0 if not v else sum(v) / len(v)


def _softmax_rows(scores: list) -> list:
    out = []
    for row in scores:
        m = max(row)
        e = [math.exp(v - m) for v in row]
        z = sum(e)
        out.append([v / z for v in e])
    return out


def train(X: list, y: list, classes: list, *, feature_order: list,
          rounds: int = 40, depth: int = 3, lr: float = 0.2,
          min_leaf: int = 3) -> dict:
    """X: list[list[float]]（按 feature_order），y: list[类名]。"""
    k = len(classes)
    idx = {c: i for i, c in enumerate(classes)}
    yy = [idx[c] for c in y]
    base = [math.log(max(1e-6, yy.count(i) / len(yy))) for i in range(k)]
    scores = [[base[i] for i in range(k)] for _ in X]
    trees: list = []
    for _ in range(rounds):
        p = _softmax_rows(scores)
        round_trees = []
        for c in range(k):
            g = [(1.0 if yy[i] == c else 0.0) - p[i][c] for i in range(len(X))]
            tree = _fit_tree(X, g, depth, min_leaf)
            round_trees.append(tree)
            for i in range(len(X)):
                scores[i][c] += lr * _eval_tree(tree, X[i])
        trees.append(round_trees)
    return {"schema_version": 1, "model_type": "gbdt_multiclass",
            "feature_order": list(feature_order), "classes": list(classes),
            "base_scores": [round(b, 6) for b in base], "learning_rate": lr,
            "trees": trees}


def evaluate(model: dict, X: list, y: list) -> dict:
    """每类 precision/recall/F1 + 混淆矩阵（小数据集也够用的报告）。"""
    classes = model["classes"]
    cm = {a: {b: 0 for b in classes} for a in classes}
    for xi, yi in zip(X, y):
        pred, _, _ = predict(model, dict(zip(model["feature_order"], xi)))
        cm[yi][pred] += 1
    per = {}
    for c in classes:
        tp = cm[c][c]
        fp = sum(cm[a][c] for a in classes if a != c)
        fn = sum(cm[c][b] for b in classes if b != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per[c] = {"precision": round(prec, 3), "recall": round(rec, 3),
                  "f1": round(f1, 3), "support": tp + fn}
    acc = sum(cm[c][c] for c in classes) / max(1, len(y))
    return {"accuracy": round(acc, 4), "per_class": per, "confusion": cm}
