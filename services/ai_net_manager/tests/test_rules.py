# -*- coding: utf-8 -*-
"""规则引擎边界测试（设计 §14.1/§14.2 S3）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

from ai_net.decision.rules import RuleEngine

CFG = json.load(open(os.path.join(ROOT, "config", "rules.json"), encoding="utf-8"))


def eng():
    return RuleEngine(CFG)


def base_ctx(**kw):
    ctx = {"dwell_s": 120, "cooldown_left_s": 0, "switches_10min": 0,
           "gain": 20, "hold_s": 20, "candidate_rsrp": -90, "traffic_class": "generic"}
    ctx.update(kw)
    return ctx


def test_all_allowed():
    r = eng().check(base_ctx())
    assert r["allowed"] and not r["blocked_by"]


def test_dwell_blocks():
    r = eng().check(base_ctx(dwell_s=30))
    assert not r["allowed"] and any("min_dwell" in b for b in r["blocked_by"])


def test_cooldown_blocks():
    r = eng().check(base_ctx(cooldown_left_s=60))
    assert not r["allowed"] and any("cooldown" in b for b in r["blocked_by"])


def test_gain_threshold_generic_and_conference():
    e = eng()
    r = e.check(base_ctx(gain=13))
    assert r["allowed"]           # generic 门槛 12
    r2 = e.check(base_ctx(gain=13, traffic_class="conference"))
    assert not r2["allowed"]      # conference 门槛 16
    r3 = e.check(base_ctx(gain=17, traffic_class="conference", hold_s=16))
    assert r3["allowed"]


def test_hold_blocks():
    r = eng().check(base_ctx(hold_s=5))
    assert not r["allowed"] and any("hold" in b for b in r["blocked_by"])


def test_max_switches():
    r = eng().check(base_ctx(switches_10min=3))
    assert not r["allowed"] and any("max_switches" in b for b in r["blocked_by"])


def test_rsrp_floor():
    r = eng().check(base_ctx(candidate_rsrp=-120))
    assert not r["allowed"] and any("rsrp" in b for b in r["blocked_by"])


def test_backup_profile_more_aggressive():
    e = eng()
    r = e.check(base_ctx(gain=9, hold_s=6, traffic_class="backup"))
    assert r["allowed"]           # backup 门槛 8/5
