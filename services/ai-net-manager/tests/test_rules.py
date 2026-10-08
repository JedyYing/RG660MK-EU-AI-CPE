"""设计 14.1：Rule Engine 边界测试（cooldown / hysteresis / max switch / rollback）。"""
from ai_net.decision.rules import RuleEngine

SEL = {"min_dwell_time_s": 60, "switch_cooldown_s": 120, "min_score_gain": 12,
       "min_gain_hold_s": 10, "min_candidate_rsrp": -115, "max_switches_10min": 3,
       "rollback_window_s": 30, "rollback_score_drop": 15, "bad_qoe_hold_s": 5}
PROF = {"conference": {"min_score_gain": 16, "min_gain_hold_s": 15},
        "backup": {"min_score_gain": 8, "min_gain_hold_s": 5},
        "generic": {"min_score_gain": 12, "min_gain_hold_s": 10}}


def eng():
    return RuleEngine(SEL, PROF, unknown_threshold=0.6)


def test_profile_generic_on_low_confidence():
    e = eng()
    assert e.profile_for("conference", 0.95)["min_score_gain"] == 16
    assert e.profile_for("conference", 0.40)["min_score_gain"] == 12   # 回落 generic
    assert e.profile_for("unknown_class", 0.9)["min_score_gain"] == 12


def test_hard_filter_rsrp_threshold():
    e = eng()
    kept, blocked = e.hard_filter([
        {"key": "a", "score": 80, "rsrp_dbm": -100},
        {"key": "b", "score": 90, "rsrp_dbm": -120},
        {"key": "c", "score": None, "rsrp_dbm": -90},
    ])
    assert [c["key"] for c in kept] == ["a"]
    reasons = " ".join(b["reason"] for b in blocked)
    assert "rsrp" in reasons and "no_score" in reasons


def test_switch_allowed_dwell_and_cooldown():
    e = eng()
    ok, why = e.switch_allowed(100, {"entered_cell_ts": 90, "last_switch_ts": None,
                                     "switch_ts_list": []})
    assert not ok and any("dwell" in w for w in why)
    ok, why = e.switch_allowed(300, {"entered_cell_ts": 100, "last_switch_ts": 250,
                                     "switch_ts_list": [250]})
    assert not ok and any("cooldown" in w for w in why)
    ok, _ = e.switch_allowed(400, {"entered_cell_ts": 100, "last_switch_ts": 250})
    assert ok


def test_max_switches_10min_blocks_ping_pong():
    e = eng()
    now = 1000
    ok, why = e.switch_allowed(now, {"entered_cell_ts": 0, "last_switch_ts": 0,
                                     "switch_ts_list": [now - 500, now - 300, now - 100]})
    assert not ok and any("max_switches_10min" in w for w in why)
    # 超出 10 分钟的旧切换不计入
    ok, _ = e.switch_allowed(now, {"entered_cell_ts": 0, "last_switch_ts": 0,
                                   "switch_ts_list": [now - 700, now - 650, now - 400]})
    assert ok


def test_rollback_window_and_drop():
    e = eng()
    do, why = e.rollback_check(70, 40, elapsed_s=10)
    assert not do and "verify_window" in why
    do, why = e.rollback_check(70, 40, elapsed_s=31)
    assert do and "qoe_drop" in why
    do, _ = e.rollback_check(70, 60, elapsed_s=31)
    assert not do
    do, why = e.rollback_check(None, 60, elapsed_s=31)
    assert not do and why == "insufficient_qoe"
