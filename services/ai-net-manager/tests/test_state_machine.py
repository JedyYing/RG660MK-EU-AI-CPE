"""状态机集成测试（设计 14.2 场景 S3/S6 的离线等价物）。

语义：一次 step() 会把瞬态状态推进到等待态，决策只在
「被拦截 / 执行动作 / 回滚」节点产生，decision.state 为产生决策时所处状态。
"""
from ai_net.decision.rules import RuleEngine
from ai_net.decision.state_machine import SelectionStateMachine

SEL = {"min_dwell_time_s": 60, "switch_cooldown_s": 120, "min_score_gain": 12,
       "min_gain_hold_s": 10, "min_candidate_rsrp": -115, "max_switches_10min": 3,
       "rollback_window_s": 30, "rollback_score_drop": 15, "bad_qoe_hold_s": 5}
PROF = {"generic": {"min_score_gain": 12, "min_gain_hold_s": 10}}


class FakeExec:
    def __init__(self):
        self.locks = []
        self.unlocks = 0

    def lock_cell(self, rat, arfcn, pci):
        self.locks.append((rat, arfcn, pci))
        return {"ok": True, "detail": "fake"}

    def unlock(self):
        self.unlocks += 1
        return {"ok": True, "detail": "fake"}


def sm(mode="shadow", exec_=None):
    m = SelectionStateMachine(RuleEngine(SEL, PROF), executor=exec_, mode=mode)
    m.note_serving_cell(0.0, "CELL-A")
    return m


def obs(qoe=80.0, cand=None, modem_ok=True):
    cands = [cand] if cand else []
    bc = max(cands, key=lambda c: c["score"]) if cands else None
    return {"qoe_score": qoe, "serving_score": qoe, "candidates": cands,
            "best_candidate": bc, "traffic_class": "generic",
            "traffic_confidence": 0.9, "modem_ok": modem_ok,
            "serving_cell": {"cell_id": "CELL-A"}}


CAND = {"key": "n1", "score": 95, "rsrp_dbm": -95, "rat": 11, "arfcn": 426030, "pci": 784}


def test_hysteresis_hold_required_before_evaluate():
    m = sm()
    assert m.step(100, obs(qoe=80, cand=CAND)) == []        # gain 够但未保持
    assert m.step(105, obs(qoe=80, cand=CAND)) == []        # 保持 5s < 10s
    recs = m.step(112, obs(qoe=80, cand=CAND))              # 保持 12s → 评估→guard→执行
    assert len(recs) == 1
    d = recs[0]
    assert d.state == "EXECUTE" and d.trigger == "score_gain"
    assert d.action["type"] == "lock_cell"
    assert d.action["params"] == {"rat": 11, "arfcn": 426030, "pci": 784}
    assert d.action["result"]["detail"].startswith("shadow")
    assert d.executor_level == "L0"
    assert m.state == "VERIFY"


def test_gain_reset_when_candidate_changes():
    m = sm()
    c2 = dict(CAND, key="n2", pci=785)
    m.step(100, obs(qoe=80, cand=CAND))
    m.step(108, obs(qoe=80, cand=c2))       # 换候选 → 计时重置
    assert m.step(110, obs(qoe=80, cand=c2)) == []
    recs = m.step(119, obs(qoe=80, cand=c2))
    assert recs and recs[0].state == "EXECUTE"


def test_dwell_blocks_and_logs_reason():
    m = sm()
    m.step(10, obs(qoe=80, cand=CAND))      # hold 开始
    recs = m.step(21, obs(qoe=80, cand=CAND))   # 保持 11s → 评估；驻留 21s < 60 → 拦截
    assert m.state == "NORMAL"
    assert len(recs) == 1 and any("dwell" in b for b in recs[0].blocked_by)
    assert recs[0].state == "EVALUATE_CANDIDATES"


def test_bad_qoe_trigger_and_rollback_in_execute_mode():
    ex = FakeExec()
    m = sm(mode="execute", exec_=ex)
    cand = {"key": "n1", "score": 60, "rsrp_dbm": -100, "rat": 11, "arfcn": 1, "pci": 2}
    assert m.step(100, obs(qoe=30, cand=cand)) == []        # 坏 QoE 起点
    recs = m.step(106, obs(qoe=30, cand=cand))              # 持续 6s ≥ 5s → 触发
    assert recs and recs[0].trigger == "bad_qoe" and recs[0].state == "EXECUTE"
    assert ex.locks == [(11, 1, 2)]
    assert m.state == "VERIFY"
    recs = m.step(140, obs(qoe=10, cand=cand))              # 观察窗内大跌 → 回滚
    assert recs and recs[0].rollback is True
    assert "qoe_drop" in recs[0].rollback_reason
    assert recs[0].action["type"] == "unlock_cell"
    assert ex.unlocks == 1
    assert m.state == "COOLDOWN"
    m.step(300, obs(qoe=80, cand=cand))
    assert m.state in ("NORMAL", "EVALUATE_CANDIDATES")


def test_verify_stable_keeps_lock_and_enters_cooldown():
    ex = FakeExec()
    m = sm(mode="execute", exec_=ex)
    cand = {"key": "n1", "score": 60, "rsrp_dbm": -100, "rat": 11, "arfcn": 1, "pci": 2}
    m.step(100, obs(qoe=30, cand=cand))
    m.step(106, obs(qoe=30, cand=cand))
    recs = m.step(140, obs(qoe=45, cand=cand))              # 无下跌 → 不回滚
    assert recs and recs[0].rollback is False
    assert recs[0].action["type"] == "noop"
    assert ex.unlocks == 0 and m.state == "COOLDOWN"
    # 冷却期内（含坏 QoE）不产生任何动作/决策
    assert m.step(150, obs(qoe=30, cand=cand)) == []
    assert m.step(156, obs(qoe=30, cand=cand)) == []
    assert m.state == "COOLDOWN"


def test_modem_error_blocks_switch():
    m = sm(mode="execute", exec_=FakeExec())
    cand = {"key": "n1", "score": 95, "rsrp_dbm": -95, "rat": 11, "arfcn": 1, "pci": 2}
    m.step(100, obs(qoe=30, cand=cand))
    recs = m.step(106, obs(qoe=30, cand=cand, modem_ok=False))
    assert m.state == "NORMAL"
    assert any("modem_error" in b for b in recs[0].blocked_by)
