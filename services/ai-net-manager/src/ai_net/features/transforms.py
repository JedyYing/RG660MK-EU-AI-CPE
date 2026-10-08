"""窗口统计原语（设计文档 15.5）。

⚠ 分位口径统一为 **nearest-rank（最近秩）**，不做线性插值。
   两套口径（最近秩 / 中位插值）混用会让报告数值不可复现 —— 本项目只用最近秩，
   函数名显式带 _nr 后缀，避免误用。
"""
from __future__ import annotations

import math


def mean(vals: list) -> float | None:
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 4) if v else None


def pstdev(vals: list) -> float | None:
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None
    m = sum(v) / len(v)
    return round(math.sqrt(sum((x - m) ** 2 for x in v) / len(v)), 4)


def quantile_nr(vals: list, q: float) -> float | None:
    """最近秩分位：ceil(q*n) 号（1-based）。q∈(0,1]。"""
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = max(1, math.ceil(q * len(v)))
    return round(v[k - 1], 4)


def vmin(vals: list):
    v = [x for x in vals if x is not None]
    return min(v) if v else None


def vmax(vals: list):
    v = [x for x in vals if x is not None]
    return max(v) if v else None


def slope(vals: list, dt_s: float = 1.0) -> float | None:
    """最小二乘斜率（单位/秒）。点数 <2 或 dt<=0 返回 None。"""
    v = [x for x in vals if x is not None]
    n = len(v)
    if n < 2 or dt_s <= 0:
        return None
    xs = [i * dt_s for i in range(n)]
    mx, my = sum(xs) / n, sum(v) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    num = sum((xs[i] - mx) * (v[i] - my) for i in range(n))
    return round(num / den, 6)


def jitter_absdiff(vals: list) -> list:
    """相邻差绝对值序列（jitter 定义，设计文档 3.2）。"""
    v = [x for x in vals if x is not None]
    return [abs(v[i] - v[i - 1]) for i in range(1, len(v))]


def clamp01(x: float | None) -> float | None:
    if x is None:
        return None
    return max(0.0, min(1.0, x))


def piecewise(x: float | None, points: list) -> float | None:
    """分段线性插值：points=[(x1,y1),...] 单调递增 x；域外取端点值。"""
    if x is None or not points:
        return None
    if x <= points[0][0]:
        return float(points[0][1])
    if x >= points[-1][0]:
        return float(points[-1][1])
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        if x0 <= x <= x1:
            if x1 == x0:
                return float(y1)
            return round(y0 + (y1 - y0) * (x - x0) / (x1 - x0), 4)
    return None


def window_stats(vals: list, dt_s: float = 1.0) -> dict:
    """一组值的完整统计（缺值自动跳过）。"""
    return {
        "mean": mean(vals), "std": pstdev(vals),
        "min": vmin(vals), "max": vmax(vals),
        "p10": quantile_nr(vals, 0.10), "p50": quantile_nr(vals, 0.50),
        "p90": quantile_nr(vals, 0.90), "p95": quantile_nr(vals, 0.95),
        "slope": slope(vals, dt_s),
        "n": len([x for x in vals if x is not None]),
    }
