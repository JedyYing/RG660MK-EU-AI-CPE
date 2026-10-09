# -*- coding: utf-8 -*-
"""在线决策状态机（设计 §9）。
NORMAL → EVALUATE → PRE_SWITCH → RECOMMEND/EXECUTE → (VERIFY/ROLLBACK) → COOLDOWN → NORMAL
Phase 1 = Shadow：执行阶段仅产出 recommend 并记录；VERIFY/ROLLBACK 留给 execute 阶段。"""
import uuid

from schemas import Decision
from ..models.baseline import MODEL_VERSION
from ..util import now_ms


class SelectionFSM:
    def __init__(self, engine, mode="shadow", bad_score=40, good_score=70):
        self.engine = engine
        self.mode = mode
        self.bad_score = bad_score
        self.good_score = good_score
        self.state = "NORMAL"
        self.cell_key = None
        self.cell_enter_ms = None
        self.switch_ts = []          # 观测到的(重选/执行)切换时刻
        self.last_switch_ms = 0
        self.recent_switch_reason = ""
        self.advantage = {"key": None, "since_ms": None, "gain": 0.0}
        self.cooldown_until_ms = 0
        self.rules_version = getattr(engine, "version", "rules-1.0")

    # ---- helpers ----
    def _prune(self, now):
        self.switch_ts = [t for t in self.switch_ts if now - t <= 600_000]

    def dwell_s(self, now=None):
        now = now or now_ms()
        return 0.0 if self.cell_enter_ms is None else max(0.0, (now - self.cell_enter_ms) / 1000.0)

    def switches_10min(self):
        return len(self.switch_ts)

    def switches_5min(self, now=None):
        now = now or now_ms()
        return len([t for t in self.switch_ts if now - t <= 300_000])

    # ---- main ----
    def tick(self, window, serving_score, candidates, traffic):
        now = now_ms()
        c = window.cell or {}
        key = c.get("cell_id") or c.get("nci") or c.get("tac") or "unknown"

        if self.cell_key is None:
            self.cell_key = key
            self.cell_enter_ms = now
        elif key != self.cell_key:
            # 观测到网络侧重选（非我们触发）
            self.switch_ts.append(now)
            self.last_switch_ms = now
            self.recent_switch_reason = "network_reselection"
            self.cell_key = key
            self.cell_enter_ms = now
            self.advantage = {"key": None, "since_ms": None, "gain": 0.0}
        self._prune(now)

        dwell = self.dwell_s(now)
        cooldown_left = max(0.0, (self.cooldown_until_ms - now) / 1000.0)

        # 候选优势跟踪（排除当前小区）
        best = None
        for cand in candidates or []:
            if cand.get("cell") != key:
                best = cand
                break
        gain = 0.0
        if best is not None and serving_score is not None:
            gain = best["score"] - serving_score
        eff = self.engine.effective(traffic.cls if traffic else "generic")
        if best is not None and gain >= eff.get("min_score_gain", 0):
            if self.advantage["key"] == best["cell"]:
                pass
            else:
                self.advantage = {"key": best["cell"], "since_ms": now, "gain": gain}
        else:
            self.advantage = {"key": None, "since_ms": None, "gain": 0.0}
        hold_s = 0.0 if self.advantage["since_ms"] is None else (now - self.advantage["since_ms"]) / 1000.0

        bad_qoe = serving_score is not None and serving_score < self.bad_score
        trigger = ""
        action = "none"
        blocked = []
        detail = {}

        if self.state == "NORMAL":
            if bad_qoe:
                trigger = "bad_qoe"
                self.state = "EVALUATE"
            elif best is not None and gain > 0:
                trigger = "score_gain"
                self.state = "EVALUATE"
        if self.state == "EVALUATE":
            ctx = {
                "dwell_s": dwell, "cooldown_left_s": cooldown_left,
                "switches_10min": self.switches_10min(),
                "gain": gain, "hold_s": hold_s,
                "candidate_rsrp": (best or {}).get("rsrp_est"),
                "traffic_class": traffic.cls if traffic else "generic",
            }
            res = self.engine.check(ctx)
            if res["allowed"]:
                self.state = "PRE_SWITCH"
                blocked = []
            else:
                blocked = res["blocked_by"]
                self.state = "NORMAL"
                trigger = trigger or "policy"
        if self.state == "PRE_SWITCH":
            # 通过全部安全规则 → 产出动作
            if self.mode in ("shadow", "recommend"):
                action = "recommend"
                detail = {
                    "target_cell": best["cell"] if best else None,
                    "target_score": best["score"] if best else None,
                    "gain": round(gain, 1),
                    "executed": False,
                    "why": "shadow/recommend 模式：仅建议，不控制 modem",
                }
            else:
                # execute 阶段（Phase1 未启用；保留接口路径）
                action = "recommend"
                detail = {"executed": False, "why": "executor capability not verified"}
            self.state = "COOLDOWN"
            cd = eff.get("switch_cooldown_s", 120)
            self.cooldown_until_ms = now + cd * 1000
        if self.state == "COOLDOWN" and cooldown_left <= 0 and now >= self.cooldown_until_ms:
            self.state = "NORMAL"

        top_features = self._top_features(window.features)
        dec = Decision(
            decision_id=uuid.uuid4().hex[:12],
            ts_ms=now,
            state=self.state,
            mode=self.mode,
            serving_cell={"key": key, **{k: c.get(k) for k in ("cell_id", "tac") if c.get(k)}},
            serving_score=serving_score,
            candidates=(candidates or [])[:5],
            trigger=trigger,
            traffic_class=(traffic.cls if traffic else "generic"),
            traffic_confidence=(traffic.confidence if traffic else 0.0),
            action=action,
            action_detail=detail,
            blocked_by=blocked,
            model_version=MODEL_VERSION,
            rules_version=self.rules_version,
            top_features=top_features,
        )
        return dec

    @staticmethod
    def _top_features(f):
        """可解释输出（设计 §13）：最多 3 条显著特征。"""
        out = []
        if f.get("rtt_p95") is not None and f["rtt_p95"] > 100:
            out.append("rtt_p95=%.0fms 偏高" % f["rtt_p95"])
        if f.get("rsrp_slope") is not None and f["rsrp_slope"] < -0.5:
            out.append("rsrp_slope=%.2f 下滑" % f["rsrp_slope"])
        if f.get("loss_rate") is not None and f["loss_rate"] > 0.01:
            out.append("loss_rate=%.1f%%" % (f["loss_rate"] * 100))
        if f.get("jitter_p95") is not None and f["jitter_p95"] > 30:
            out.append("jitter_p95=%.0fms" % f["jitter_p95"])
        if f.get("rsrp_mean") is not None and f["rsrp_mean"] < -100:
            out.append("rsrp_mean=%.0fdBm 偏弱" % f["rsrp_mean"])
        return out[:3]
