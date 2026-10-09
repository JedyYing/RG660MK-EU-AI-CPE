#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析器验证（无需 pytest）：对 tests/fixtures 全量断言。返回码 0=全过。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]

from ai_net.collectors import mtk_cell as mc
from ai_net.collectors.qoe_probe import parse_ping_rtt
from ai_net.collectors.traffic_flow import parse_conntrack

FX = os.path.join(ROOT, "tests", "fixtures")
checks = []


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def ck(name, cond):
    checks.append((name, bool(cond)))


ck("signal 5G/-67", mc.parse_signal(fx("nw_get_signal.txt")) == ("5G", -67))
ck("reg HOME", mc.parse_reg(fx("check_nw_status.txt")) == "HOME")
ck("radio ON", mc.parse_radio(fx("nw_radio_state.txt")) == "ON")
ck("cell (mode2)", mc.parse_cell(fx("c5greg_reply.txt")) == {"tac": "590D0A", "nci": "0594FCB484"})
ck("cell (short)", mc.parse_cell(fx("c5greg_reply_short.txt")) == {"tac": "590D0A", "nci": "0594FCB484"})
ck("cesq 9 fields", len(mc.parse_cesq(fx("cesq_reply.txt"))["raw_values"]) == 9)
ck("qeng unsupported marked", "CME ERROR" in fx("qeng_unsupported.txt"))
ck("ping rtt", parse_ping_rtt(fx("ping_ok.txt")) == 13.494)
ck("ping loss -> None", parse_ping_rtt(fx("ping_loss100.txt")) is None)
fl = parse_conntrack(os.path.join(FX, "conntrack_sample.txt"))
ck("conntrack 3 flows", len(fl) == 3)
ck("conntrack established bytes", any(f["f_bytes"] == 34327 and f["r_bytes"] == 21805 for f in fl))

bad = [n for n, ok in checks if not ok]
for n, ok in checks:
    print(("PASS " if ok else "FAIL ") + n)
print("---- %d/%d passed ----" % (len(checks) - len(bad), len(checks)))
sys.exit(0 if not bad else 1)
