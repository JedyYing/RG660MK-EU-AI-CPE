# -*- coding: utf-8 -*-
"""Rule Engine：安全控制核心（设计 §7）。
硬约束：驻留/冷却/增益/保持/RSRP 门槛/10min 切换上限。YAML→JSON 配置。"""


class RuleEngine:
    def __init__(self, cfg):
        self.selection = dict(cfg.get("selection", {}))
        self.profiles = dict(cfg.get("profiles", {}))
        self.safety = dict(cfg.get("safety", {}))
        self.version = "rules-%s" % cfg.get("schema_version", "1.0")

    def effective(self, traffic_class):
        eff = dict(self.selection)
        p = self.profiles.get(traffic_class)
        if p:
            for k in ("min_score_gain", "min_gain_hold_s"):
                if k in p:
                    eff[k] = p[k]
        return eff

    def check(self, ctx):
        """ctx: dwell_s, cooldown_left_s, switches_10min, gain, hold_s,
                candidate_rsrp, traffic_class
        返回 {allowed, blocked_by[], thresholds}（纯函数，可重放）。"""
        eff = self.effective(ctx.get("traffic_class", "generic"))
        blocked = []

        dwell = ctx.get("dwell_s")
        if dwell is not None and dwell < eff.get("min_dwell_time_s", 0):
            blocked.append("min_dwell_time_s(%.0f<%.0f)" % (dwell, eff["min_dwell_time_s"]))

        cd = ctx.get("cooldown_left_s") or 0
        if cd > 0:
            blocked.append("switch_cooldown_s(remaining %.0fs)" % cd)

        if (ctx.get("switches_10min") or 0) >= eff.get("max_switches_10min", 99):
            blocked.append("max_switches_10min(%d>=%d)" % (ctx.get("switches_10min"),
                                                           eff.get("max_switches_10min")))

        cr = ctx.get("candidate_rsrp")
        if cr is not None and cr < eff.get("min_candidate_rsrp", -999):
            blocked.append("min_candidate_rsrp(%s<%s)" % (cr, eff.get("min_candidate_rsrp")))

        gain = ctx.get("gain") or 0.0
        if gain < eff.get("min_score_gain", 0):
            blocked.append("min_score_gain(%.1f<%.1f)" % (gain, eff.get("min_score_gain")))

        hold = ctx.get("hold_s") or 0.0
        if hold < eff.get("min_gain_hold_s", 0):
            blocked.append("min_gain_hold_s(%.1f<%.1f)" % (hold, eff.get("min_gain_hold_s")))

        return {"allowed": not blocked, "blocked_by": blocked, "thresholds": eff}
