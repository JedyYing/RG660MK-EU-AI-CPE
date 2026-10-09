# -*- coding: utf-8 -*-
"""特征变换（复用 util 统计）。差异序列/滚动帮手。"""
from ..util import mean, percentile, slope, std, summarize


def diff_series(xs):
    """相邻差（忽略 None 对）。"""
    out = []
    prev = None
    for x in xs:
        if x is None:
            continue
        if prev is not None:
            out.append(x - prev)
        prev = x
    return out


def jitter_p95(rtts):
    """jitter = |rtt_t - rtt_t-1| 的 P95（设计 §3.2 默认口径）。"""
    ds = [abs(d) for d in diff_series(rtts)]
    return percentile(ds, 95)


def loss_rate(ok_flags):
    """None/False 记为丢。"""
    vals = [1.0 if f else 0.0 for f in ok_flags if f is not None]
    if not vals:
        return None
    return round(1.0 - (sum(vals) / len(vals)), 4)


__all__ = ["diff_series", "jitter_p95", "loss_rate", "mean", "std", "percentile", "slope", "summarize"]
