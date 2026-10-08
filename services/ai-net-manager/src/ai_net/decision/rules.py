"""Rule Engine：硬约束 / 迟滞 / 冷却 / 回滚（设计文档第 7 章、14.1 边界测试对象）。"""
from __future__ import annotations


class RuleEngine:
    def __init__(self, selection: dict, profiles: dict | None = None,
                 unknown_threshold: float = 0.6):
        self.sel = dict(selection or {})
        self.profiles = dict(profiles or {})
        self.unknown_threshold = unknown_threshold

    # -- 业务画像 -------------------------------------------------------
    def profile_for(self, traffic_class: str, confidence: float) -> dict:
        """低置信度回落 generic（设计 7.2）。"""
        if not traffic_class or confidence < self.unknown_threshold:
            traffic_class = "generic"
        p = dict(self.profiles.get(traffic_class) or self.profiles.get("generic") or {})
        return {
            "traffic_class": traffic_class,
            "min_score_gain": p.get("min_score_gain", self.sel.get("min_score_gain", 12)),
            "min_gain_hold_s": p.get("min_gain_hold_s", self.sel.get("min_gain_hold_s", 10)),
        }

    # -- 硬约束 ---------------------------------------------------------
    def hard_filter(self, candidates: list, min_rsrp: float | None = None) -> tuple:
        """返回 (kept, blocked[ {key, reason} ])。min_candidate_rsrp 来自配置。"""
        thr = self.sel.get("min_candidate_rsrp") if min_rsrp is None else min_rsrp
        kept, blocked = [], []
        for c in candidates:
            if thr is not None and c.get("rsrp_dbm") is not None and c["rsrp_dbm"] < thr:
                blocked.append({"key": c.get("key"), "reason": "rsrp<%.0f(%s)"
                                % (thr, c.get("rsrp_dbm"))})
                continue
            if c.get("score") is None:
                blocked.append({"key": c.get("key"), "reason": "no_score"})
                continue
            kept.append(c)
        kept.sort(key=lambda c: c["score"], reverse=True)
        return kept, blocked

    def switch_allowed(self, now_s: float, ctx: dict) -> tuple:
        """驻留 / 冷却 / 10 分钟切换次数上限。ctx:
        {entered_cell_ts, last_switch_ts, switch_ts_list}
        """
        reasons = []
        dwell = self.sel.get("min_dwell_time_s", 60)
        entered = ctx.get("entered_cell_ts")
        if entered is not None and now_s - entered < dwell:
            reasons.append("dwell(%ds<%.0fs)" % (dwell, now_s - entered))
        cd = self.sel.get("switch_cooldown_s", 120)
        last = ctx.get("last_switch_ts")
        if last is not None and now_s - last < cd:
            reasons.append("cooldown(%ds<%.0fs)" % (cd, now_s - last))
        mx = self.sel.get("max_switches_10min", 3)
        recent = [t for t in (ctx.get("switch_ts_list") or []) if now_s - t <= 600]
        if len(recent) >= mx:
            reasons.append("max_switches_10min(%d>=%d)" % (len(recent), mx))
        return (not reasons), reasons

    def rollback_check(self, qoe_before, qoe_after, elapsed_s: float) -> tuple:
        """观察窗内 QoE 跌幅 >= rollback_score_drop → 回滚（设计 7.1）。"""
        win = self.sel.get("rollback_window_s", 30)
        if elapsed_s < win:
            return False, "verify_window(%ds<%.0fs)" % (elapsed_s, win)
        drop = self.sel.get("rollback_score_drop", 15)
        if qoe_before is None or qoe_after is None:
            return False, "insufficient_qoe"
        if qoe_before - qoe_after >= drop:
            return True, "qoe_drop(%.1f>=%.1f)" % (qoe_before - qoe_after, drop)
        return False, "stable(%.1f)" % (qoe_before - qoe_after)

    def bad_qoe(self, qoe_now, threshold: float = 40.0) -> bool:
        return qoe_now is not None and qoe_now < threshold
