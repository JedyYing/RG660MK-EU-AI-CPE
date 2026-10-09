# -*- coding: utf-8 -*-
"""Baseline 评分与候选排序（设计 §6.3/§15.7）。
子分归一化 + 业务权重合成；缺失子分权重重归一（并回传 missing）。"""
from ..util import clamp

MODEL_VERSION = "baseline-1.0"


def radio_quality(rsrp):
    if rsrp is None:
        return None
    return clamp((rsrp + 120.0) / 50.0, 0.0, 1.0)   # -120 → 0, -70 → 1


def throughput_quality(profile, dl, ul):
    """方向加权：camera 类 UL 权重 ×2（设计 §5.3/§7.2）。"""
    t = profile.get("targets", {})
    w_dl = 1.0
    w_ul = 2.0 if profile.get("_ul_priority") else 1.0
    num = 0.0
    den = 0.0
    if dl is not None and t.get("dl_mbps"):
        num += w_dl * clamp(dl / t["dl_mbps"], 0, 1.2) / 1.2
        den += w_dl
    if ul is not None and t.get("ul_mbps"):
        num += w_ul * clamp(ul / t["ul_mbps"], 0, 1.2) / 1.2
        den += w_ul
    return (num / den) if den else None


def latency_quality(rtt, jitter):
    if rtt is None and jitter is None:
        return None
    q = 1.0
    if rtt is not None:
        q -= clamp(rtt / 300.0, 0, 1.0) * 0.7
    if jitter is not None:
        q -= clamp(jitter / 100.0, 0, 1.0) * 0.3
    return clamp(q, 0.0, 1.0)


def stability_quality(loss, jitter):
    if loss is None:
        return None
    q = 1.0 - clamp(loss / 0.1, 0, 1.0) * 0.8
    if jitter is not None:
        q -= clamp(jitter / 150.0, 0, 1.0) * 0.2
    return clamp(q, 0.0, 1.0)


def qoe_score(feats, profile, recent_switch_penalty=0.0):
    """返回 (score 0..100, breakdown dict, missing list)。"""
    prof = dict(profile or {})
    w = dict(prof.get("weights", {}))
    u = clamp(prof.get("_radio_weight", 0.0) or
              (1.0 - sum(w.values())), 0.0, 1.0)
    rq = radio_quality(feats.get("rsrp_mean"))
    tq = throughput_quality(prof, feats.get("dl_mbps_p50"), feats.get("ul_mbps_p50"))
    lq = latency_quality(feats.get("rtt_p50"), feats.get("jitter_p95"))
    sq = stability_quality(feats.get("loss_rate"), feats.get("jitter_p95"))
    parts = {"radio": (u, rq), "throughput": (w.get("throughput", 0.0), tq),
             "latency": (w.get("latency", 0.0), lq), "stability": (w.get("stability", 0.0), sq)}
    num = 0.0
    den = 0.0
    missing = []
    bd = {}
    for k, (ww, q) in parts.items():
        if q is None:
            missing.append(k)
            bd[k] = None
            continue
        num += ww * q
        den += ww
        bd[k] = round(q, 3)
    if den <= 0:
        return None, bd, missing
    score = 100.0 * num / den - recent_switch_penalty
    return round(clamp(score, 0, 100), 1), bd, missing


def score_rsrp_only(feats):
    """对照 baseline①：仅 RSRP 线性映射。"""
    rq = radio_quality(feats.get("rsrp_mean"))
    return None if rq is None else round(100.0 * rq, 1)


def score_rule_only(feats):
    """对照 baseline②：规则打点（RSRP/丢包/RTT 三项阈值）。"""
    s = 0.0
    r = feats.get("rsrp_mean")
    loss = feats.get("loss_rate")
    rtt = feats.get("rtt_p50")
    if r is not None:
        s += 40 if r >= -90 else (25 if r >= -105 else 8)
    if loss is not None:
        s += 30 if loss < 0.01 else (15 if loss < 0.05 else 0)
    if rtt is not None:
        s += 30 if rtt < 60 else (15 if rtt < 150 else 0)
    return round(s, 1)


def build_candidate(cell_key, profile_row, traffic_profile):
    """从历史 cell_profile 行构造候选评分（只含无线侧特征）。"""
    rsrp = profile_row.get("rsrp_mean")
    if rsrp is None and profile_row.get("rsrp_n"):
        rsrp = profile_row["rsrp_sum"] / profile_row["rsrp_n"]
    rq = radio_quality(rsrp)
    if rq is None:
        return None
    hist = profile_row.get("score_mean")
    if hist is None and profile_row.get("score_n"):
        hist = profile_row["score_sum"] / profile_row["score_n"]
    score = 100.0 * (0.75 * rq + 0.25 * ((hist or 50.0) / 100.0))
    return {
        "cell": cell_key,
        "score": round(score, 1),
        "rsrp_est": rsrp,
        "obs": profile_row.get("obs", 0),
        "last_seen_s": profile_row.get("last_seen_s"),
        "source": "history_profile",
    }
