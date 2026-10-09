# -*- coding: utf-8 -*-
"""Modem 能力探测（设计 §8.2/§15.10）：只读探测 + 变更类命令仅做存在性记录。
输出 reports/modem_capability.json。禁止执行任何锁频/锁小区/射频变更命令。"""
import argparse
import json
import os
import sys
import time

from ..collectors.at_transport import at_clean, mipc_clean
from ..util import now_ms

READ_CAPS = [
    ("nw_get_signal", ["--nw_get_signal"]),
    ("nw_get_rat", ["--nw_get_rat"]),
    ("check_nw_status", ["--check_nw_status"]),
    ("nw_radio_state_get", ["--nw_radio_state_get"]),
    ("show_register_status", ["--show_register_status"]),
    ("at_cesq", None),            # 走 at_clean
    ("at_c5greg", None),
    ("at_qeng_servingcell", None),
    ("at_qeng_neighbourcell", None),
    ("at_qcainfo", None),
    ("at_qnwinfo", None),
]
AT_CAPS = {
    "at_cesq": "AT+CESQ",
    "at_c5greg": "AT+C5GREG?",
    "at_qeng_servingcell": 'AT+QENG="servingcell"',
    "at_qeng_neighbourcell": 'AT+QENG="neighbourcell"',
    "at_qcainfo": "AT+QCAINFO",
    "at_qnwinfo": "AT+QNWINFO",
}
MUTATING_PRESENT = ["nw_radio_state_set", "nw_change_hw_radio", "nw_set_rat"]


def probe():
    result = {"ts_ms": now_ms(), "platform": "mtk-mipc", "read": {}, "mutating": {}}
    for name, args in READ_CAPS:
        if args is None:
            atname = AT_CAPS[name]
            rc, out = at_clean(atname, 12)
            first = (out or "").strip().splitlines()[:1]
        else:
            rc, out = mipc_clean(args, 10)
            first = (out or "").strip().splitlines()[:1]
        status = "ok"
        if "CME ERROR" in (out or ""):
            status = "unsupported"
        elif rc != 0:
            status = "error"
        result["read"][name] = {"status": status, "rc": rc, "sample": first[0][:120] if first else ""}
    for m in MUTATING_PRESENT:
        # 仅凭 help 表存在性记录，绝不执行
        result["mutating"][m] = {"status": "present-untested",
                                 "note": "存在性来自 mipc_wan_cli 帮助表；未执行（变更类）"}
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    r = probe()
    out = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "reports", "modem_capability.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    ok = sum(1 for v in r["read"].values() if v["status"] == "ok")
    uns = sum(1 for v in r["read"].values() if v["status"] == "unsupported")
    print("capability probe: ok=%d unsupported=%d -> %s" % (ok, uns, out))
    for k, v in r["read"].items():
        print("  %-24s %s | %s" % (k, v["status"], v["sample"][:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
