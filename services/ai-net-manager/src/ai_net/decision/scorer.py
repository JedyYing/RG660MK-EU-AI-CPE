"""QoE 归一化评分与候选排序（设计文档 6.2 / 6.3）。

设计原则：模型与安全策略分离 —— 本模块只产出分数与分解（可解释），
是否切换由 RuleEngine/状态机决定。缺特征时按可用分量重归一化权重，
并把缺失项显式记录（不是填 0）。
"""
from __future__ import annotations

import math

from ai_net.features import transforms as T

# 分段线性质量曲线（0..1）
RADIO_CURVE_RSRP = [(-140, 0.0), (-125, 0.10), (-115, 0.30), (-105, 0.55),
                    (-95, 0.75), (-85, 0.90), (-75, 1.0)]
RADIO_CURVE_RSRQ = [(-20, 0.0), (-17, 0.25), (-14, 0.50), (-11, 0.75), (-8, 0.90), (-5, 1.0)]
RADIO_CURVE_SINR = [(-10, 0.0), (-5, 0.10), (0, 0.30), (5, 0.55), (10, 0.75),
                    (15, 0.88), (20, 0.95), (25, 1.0)]

# 业务期望吞吐（Mbps，camera 取上行）与延迟参考（ms）
EXPECTED_MBPS = {"gaming": 3.0, "video": 25.0, "conference": 8.0, "voip": 0.2,
                 "camera": 8.0, "backup": 150.0, "generic": 15.0}
LATENCY_REF_MS = {"gaming": 40.0, "video": 120.0, "conference": 80.0, "voip": 60.0,
                  "camera": 120.0, "backup": 300.0, "generic": 100.0}

BAD_QOE_SCORE = 40.0


def radio_quality(rsrp, rsrq=None, sinr=None):
    """无线电质量 0..1；返回 (q, parts, missing)。缺分量按可用项重归一化。"""
    parts, missing = {}, []
    if rsrp is not None:
        parts["rsrp"] = T.piecewise(rsrp, RADIO_CURVE_RSRP)
    else:
        missing.append("rsrp")
    if rsrq is not None:
        parts["rsrq"] = T.piecewise(rsrq, RADIO_CURVE_RSRQ)
    else:
        missing.append("rsrq")
    if sinr is not None:
        parts["sinr"] = T.piecewise(sinr, RADIO_CURVE_SINR)
    else:
        missing.append("sinr")
    if not parts:
        return None, {}, missing
    w = {"rsrp": 0.5, "rsrq": 0.25, "sinr": 0.25}
    tw = sum(w[k] for k in parts)
    q = sum(w[k] * parts[k] for k in parts) / tw
    return round(q, 4), parts, missing


def throughput_quality(dl_mbps, ul_mbps, cls: str):
    exp = EXPECTED_MBPS.get(cls, EXPECTED_MBPS["generic"])
    v = ul_mbps if cls == "camera" else dl_mbps
    if v is None:
        return None
    return round(min(1.0, math.sqrt(max(0.0, v) / exp)), 4)


def latency_quality(rtt_ms, cls: str):
    if rtt_ms is None or rtt_ms <= 0:
        return None
    return round(min(1.0, LATENCY_REF_MS.get(cls, 100.0) / rtt_ms), 4)


def stability_quality(jitter_p95, loss_rate):
    parts = []
    if jitter_p95 is not None:
        parts.append((0.6, T.clamp01(1.0 - jitter_p95 / 50.0)))
    if loss_rate is not None:
        parts.append((0.4, T.clamp01(1.0 - loss_rate / 0.02)))
    if not parts:
        return None
    tw = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / tw, 4)


def qoe_score(features: dict, traffic_class: str, weights: dict) -> dict:
    """0..100 越高越好：w·quality 加权，缺分量重归一化。"""
    w = dict(weights.get(traffic_class) or weights.get("generic"))
    rq, rparts, rmiss = radio_quality(features.get("rsrp_p50", features.get("rsrp_mean")),
                                      features.get("rsrq_mean"), features.get("sinr_mean"))
    tq = throughput_quality(features.get("dl_mbps_p50"), features.get("ul_mbps_p50"), traffic_class)
    lq = latency_quality(features.get("rtt_p95"), traffic_class)
    sq = stability_quality(features.get("jitter_p95"), features.get("loss_rate"))
    comp = {"radio": rq, "throughput": tq, "latency": lq, "stability": sq}
    missing = []
    num = den = 0.0
    for k, v in comp.items():
        if v is None:
            missing.append(k)
            continue
        num += w.get(k, 0.0) * v
        den += w.get(k, 0.0)
    if den == 0:
        return {"score": None, "parts": comp, "missing": missing + rmiss}
    return {"score": round(100.0 * num / den, 1), "parts": comp,
            "missing": missing + rmiss, "weights": w}


def candidate_score(n, traffic_class: str, history_probe: dict | None = None) -> dict:
    """邻区候选评分：只依赖无线侧 + 历史画像（邻区无 QoE，设计 6.1）。"""
    q, parts, missing = radio_quality(n.rsrp_dbm, n.rsrq_db, n.sinr_db)
    if q is None:
        return {"score": None, "source": "rule", "parts": {},
                "reasons": ["no_rsrp"], "missing": missing}
    score = 100.0 * q
    src = "rule"
    reasons = ["radio=%.2f" % q]
    for k, v in parts.items():
        reasons.append("%s=%.2f" % (k, v))
    hp = history_probe or {}
    if hp.get("n", 0) >= 5 and hp.get("rsrp_mean") is not None:
        hq, _, _ = radio_quality(hp["rsrp_mean"])
        if hq is not None:
            score = 0.7 * score + 0.3 * 100.0 * hq
            src = "rule+history"
            reasons.append("hist_rsrp_mean=%.1f(n=%d)" % (hp["rsrp_mean"], hp["n"]))
    return {"score": round(score, 1), "source": src, "parts": parts,
            "reasons": reasons, "missing": missing}


def rsrp_only_baseline(n) -> float | None:
    """A/B 对照组：只看 RSRP 的朴素基线（设计 15.7）。"""
    q, _, _ = radio_quality(n.rsrp_dbm)
    return None if q is None else round(100.0 * q, 1)
