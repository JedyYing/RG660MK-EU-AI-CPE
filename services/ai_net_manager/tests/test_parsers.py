# -*- coding: utf-8 -*-
"""解析层单测（设计 §14.1）：真实设备回包 fixtures。用 pytest 或 python3 直接跑。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

from ai_net.collectors import mtk_cell as mc
from ai_net.collectors.qoe_probe import parse_ping_rtt
from ai_net.collectors.traffic_flow import parse_conntrack

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def test_parse_signal():
    rat, rsrp = mc.parse_signal(fx("nw_get_signal.txt"))
    assert rat == "5G" and rsrp == -67


def test_parse_reg():
    assert mc.parse_reg(fx("check_nw_status.txt")) == "HOME"
    assert mc.parse_reg("libtrm_init.\nMIPC_NW_REGISTER_STATE_SEARCHING\nlibtrm_deinit") == "SEARCHING"


def test_parse_radio():
    assert mc.parse_radio(fx("nw_radio_state.txt")) == "ON"


def test_parse_cell_forms():
    a = mc.parse_cell(fx("c5greg_reply.txt"))
    b = mc.parse_cell(fx("c5greg_reply_short.txt"))
    assert a == {"tac": "590D0A", "nci": "0594FCB484"}
    assert b == a


def test_parse_cesq():
    d = mc.parse_cesq(fx("cesq_reply.txt"))
    assert len(d["raw_values"]) == 9


def test_qeng_unsupported_detect():
    out = fx("qeng_unsupported.txt")
    assert "CME ERROR" in out


def test_ping_ok_and_loss():
    assert parse_ping_rtt(fx("ping_ok.txt")) == 13.494
    assert parse_ping_rtt(fx("ping_loss100.txt")) is None


def test_conntrack_parse():
    flows = parse_conntrack(os.path.join(FX, "conntrack_sample.txt"))
    assert len(flows) == 3
    est = [f for f in flows if f["proto"] == "tcp" and f["state"] == "ESTABLISHED"]
    assert len(est) == 1
    assert est[0]["f_bytes"] == 34327 and est[0]["r_bytes"] == 21805
    udp = [f for f in flows if f["proto"] == "udp"][0]
    assert udp["f_dport"] == "53"


def test_rat_normalize():
    assert mc.parse_rat("SIM16 RAT Mode Value: 19\nCurrent: 4/5G") == "5G"
