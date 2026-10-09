# -*- coding: utf-8 -*-
"""评分与候选排序（设计 §6）：serving QoE + 历史候选 + baseline 对照。"""
from ..models import baseline


def _profile_for(profiles_cfg, traffic_class, conf):
    if not traffic_class or traffic_class == "generic" or (conf or 0) < 0.6:
        prof = dict(profiles_cfg.get("generic", {}))
        prof["_cls"] = "generic"
        return prof
    prof = dict(profiles_cfg.get("profiles", {}).get(traffic_class) or profiles_cfg.get("generic", {}))
    prof["_cls"] = traffic_class
    if traffic_class == "camera":
        prof["_ul_priority"] = True
    return prof


def score_serving(window, profiles_cfg, traffic, recent_switch_penalty=0.0):
    prof = _profile_for(profiles_cfg, traffic.cls, traffic.confidence)
    score, bd, missing = baseline.qoe_score(window.features, prof, recent_switch_penalty)
    baselines = {
        "rsrp_only": baseline.score_rsrp_only(window.features),
        "rule_only": baseline.score_rule_only(window.features),
    }
    return score, bd, missing, baselines, prof


def rank_candidates(cell_profiles, current_key, profiles_cfg, traffic, top=5):
    prof = _profile_for(profiles_cfg, traffic.cls, traffic.confidence)
    out = []
    for key, row in (cell_profiles or {}).items():
        if key == current_key:
            continue
        c = baseline.build_candidate(key, row, prof)
        if c:
            out.append(c)
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:top]
