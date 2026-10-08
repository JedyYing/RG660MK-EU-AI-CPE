"""在线决策状态机（设计文档第 9 章 / 9.1 决策日志字段）。

NORMAL ──bad_qoe / score_gain──> EVALUATE_CANDIDATES ──通过硬约束──>
PRE_SWITCH_GUARD ──无安全拦截──> EXECUTE ──> VERIFY(30s)
  ├─ improved/stable ─> COOLDOWN ─> NORMAL
  └─ degraded ────────> ROLLBACK ─> COOLDOWN ─> NORMAL

约定：一次 step() 内会把"瞬态"状态（EVALUATE_CANDIDATES / PRE_SWITCH_GUARD /
EXECUTE）推进到等待态（NORMAL / VERIFY / COOLDOWN），决策只在
「被安全规则拦截」「执行动作」「回滚」三类节点产生。
本模块只做状态与决策，不直接触碰 Modem（执行经 executor 接口）。
"""
from __future__ import annotations

import time
import uuid

from schemas import Candidate, Decision
from .rules import RuleEngine


class SelectionStateMachine:
    def __init__(self, rules: RuleEngine, executor=None, mode: str = "shadow",
                 bad_qoe_threshold: float = 40.0, rules_version: str = "rules.yaml"):
        self.rules = rules
        self.executor = executor
        self.mode = mode
        self.bad_qoe_threshold = bad_qoe_threshold
        self.rules_version = rules_version
        self.state = "NORMAL"
        self.t = {
            "bad_qoe_since": None,
            "gain_key": None, "gain_hold_since": None,
            "verify_until": None, "cooldown_until": None,
            "entered_cell_ts": None, "last_switch_ts": None,
            "switch_ts": [],
        }
        self.pending: dict | None = None
        self.qoe_before: float | None = None
        self._trigger = "initial"
        self._last_cell = object()
        self.executor_level = {"shadow": "L0", "recommend": "L1"}.get(mode, "L3")

    # ------------------------------------------------------------------
    def note_serving_cell(self, now_s: float, cell_id) -> None:
        if cell_id != self._last_cell:
            self._last_cell = cell_id
            self.t["entered_cell_ts"] = now_s

    def dwell_s(self, now_s: float) -> float | None:
        e = self.t["entered_cell_ts"]
        return None if e is None else max(0.0, now_s - e)

    def switches_10min(self, now_s: float) -> int:
        return len([x for x in self.t["switch_ts"] if now_s - x <= 600])

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.executor_level = {"shadow": "L0", "recommend": "L1"}.get(mode, "L3")

    # ------------------------------------------------------------------
    def _decision(self, obs: dict, *, action=None, blocked=None,
                  rollback=False, rollback_reason=None) -> Decision:
        cands = []
        blocked_keys = {b.get("key") for b in (blocked or [])}
        for c in (obs.get("candidates") or [])[:5]:
            cands.append(Candidate(
                key=c.get("key", ""), cell_id=c.get("cell_id"),
                score=c.get("score"), score_source=c.get("source", "rule"),
                rsrp_dbm=c.get("rsrp_dbm"),
                passed_hard_filter=c.get("key") not in blocked_keys,
                reasons=list(c.get("reasons") or [])))
        return Decision(
            decision_id=uuid.uuid4().hex[:12],
            ts_ms=int(time.time() * 1000),
            state=self.state, trigger=self._trigger,
            serving_cell=dict(obs.get("serving_cell") or {}),
            candidates=cands,
            qoe_before={"score": self.qoe_before if self.qoe_before is not None
                        else obs.get("qoe_score")},
            qoe_after=None,
            traffic_class=obs.get("traffic_class", "generic"),
            traffic_confidence=obs.get("traffic_confidence", 0.0),
            model_version=obs.get("model_version", "none"),
            rules_version=self.rules_version,
            executor_level=self.executor_level,
            action=action or {"type": "noop", "params": {}},
            blocked_by=[b.get("reason") for b in (blocked or [])],
            rollback=rollback, rollback_reason=rollback_reason,
        )

    # ------------------------------------------------------------------
    def step(self, now_s: float, obs: dict) -> list:
        """推进状态机（一次调用内推进到等待态）。obs 需含: qoe_score,
        candidates[], best_candidate, traffic_class/confidence, modem_ok,
        serving_cell。返回本 tick 产生的 Decision 列表。"""
        out = []
        for _ in range(4):
            st = self.state
            if st == "NORMAL":
                self._normal(now_s, obs)
            elif st == "EVALUATE_CANDIDATES":
                out += self._evaluate(now_s, obs)
            elif st == "PRE_SWITCH_GUARD":
                out += self._guard(now_s, obs)
            elif st == "VERIFY":
                out += self._verify(now_s, obs)
            elif st == "COOLDOWN":
                self._cooldown(now_s)
            if self.state == st:
                break
        return out

    # ------------------------------------------------------------------
    def _normal(self, now_s: float, obs: dict) -> None:
        qoe = obs.get("qoe_score")
        if self.rules.bad_qoe(qoe, self.bad_qoe_threshold):
            if self.t["bad_qoe_since"] is None:
                self.t["bad_qoe_since"] = now_s
            elif now_s - self.t["bad_qoe_since"] >= self.rules.sel.get("bad_qoe_hold_s", 5):
                self.state = "EVALUATE_CANDIDATES"
                self._trigger = "bad_qoe"
                self.t["bad_qoe_since"] = None
                return
        else:
            self.t["bad_qoe_since"] = None

        bc = obs.get("best_candidate")
        serving = obs.get("serving_score")
        if bc and bc.get("score") is not None and serving is not None:
            prof = self.rules.profile_for(obs.get("traffic_class"),
                                          obs.get("traffic_confidence", 1.0))
            if bc["score"] - serving >= prof["min_score_gain"]:
                if self.t["gain_key"] != bc.get("key"):
                    self.t["gain_key"] = bc.get("key")
                    self.t["gain_hold_since"] = now_s
                elif now_s - (self.t["gain_hold_since"] or now_s) >= prof["min_gain_hold_s"]:
                    self.state = "EVALUATE_CANDIDATES"
                    self._trigger = "score_gain"
                    self.t["gain_key"] = self.t["gain_hold_since"] = None
            else:
                self.t["gain_key"] = self.t["gain_hold_since"] = None

    def _evaluate(self, now_s: float, obs: dict) -> list:
        kept, blocked = self.rules.hard_filter(obs.get("candidates") or [])
        ctx = {"entered_cell_ts": self.t["entered_cell_ts"],
               "last_switch_ts": self.t["last_switch_ts"],
               "switch_ts_list": self.t["switch_ts"]}
        ok, reasons = self.rules.switch_allowed(now_s, ctx)
        if kept and ok:
            self.pending = kept[0]
            self.state = "PRE_SWITCH_GUARD"
            return []
        blocked = list(blocked) + [{"key": None, "reason": r} for r in reasons]
        if not blocked:
            blocked = [{"key": None, "reason": "no_candidate"}]
        d = self._decision(obs, blocked=blocked)      # 记录在 EVALUATE 状态
        self.state = "NORMAL"
        return [d]

    def _guard(self, now_s: float, obs: dict) -> list:
        if not obs.get("modem_ok", True):
            d = self._decision(obs, blocked=[{"key": None, "reason": "modem_error"}])
            self.state = "NORMAL"
            return [d]
        c = self.pending or {}
        action = {"type": "lock_cell",
                  "params": {"rat": c.get("rat"), "arfcn": c.get("arfcn"),
                             "pci": c.get("pci")}}
        self.qoe_before = obs.get("qoe_score")
        if self.mode == "execute" and self.executor is not None and hasattr(self.executor, "lock_cell"):
            res = self.executor.lock_cell(c.get("rat"), c.get("arfcn"), c.get("pci"))
            action["result"] = res
            if not res.get("ok"):
                d = self._decision(obs, action=action,
                                   blocked=[{"key": None,
                                             "reason": "exec_failed:%s" % res.get("detail")}])
                self.state = "NORMAL"
                return [d]
        else:
            action["result"] = {"ok": True,
                                "detail": "%s: not executed (no modem write)" % self.mode}
        self.state = "EXECUTE"
        d = self._decision(obs, action=action)
        self.state = "VERIFY"
        self.t["verify_until"] = now_s + self.rules.sel.get("rollback_window_s", 30)
        self.t["last_switch_ts"] = now_s
        self.t["switch_ts"].append(now_s)
        self.t["entered_cell_ts"] = now_s          # 新小区驻留计时
        return [d]

    def _verify(self, now_s: float, obs: dict) -> list:
        if now_s < (self.t["verify_until"] or 0):
            return []
        elapsed = now_s - (self.t["verify_until"] - self.rules.sel.get("rollback_window_s", 30))
        do_rb, reason = self.rules.rollback_check(self.qoe_before, obs.get("qoe_score"), elapsed)
        if do_rb:
            if self.mode == "execute" and self.executor is not None and hasattr(self.executor, "unlock"):
                action = {"type": "unlock_cell", "result": self.executor.unlock()}
            else:
                action = {"type": "unlock_cell",
                          "result": {"ok": True,
                                     "detail": "%s: not executed (no modem write)" % self.mode}}
        else:
            action = {"type": "noop", "params": {}}
        self.state = "ROLLBACK" if do_rb else "COOLDOWN"
        d = self._decision(obs, action=action, rollback=do_rb,
                           rollback_reason=reason if do_rb else None)
        self.state = "COOLDOWN"
        self.t["cooldown_until"] = now_s + self.rules.sel.get("switch_cooldown_s", 120)
        self.qoe_before = None
        return [d]

    def _cooldown(self, now_s: float) -> None:
        if now_s >= (self.t["cooldown_until"] or 0):
            self.state = "NORMAL"
