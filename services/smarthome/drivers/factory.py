#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行器驱动工厂：按 config.json 选择 Matter / 板载 LED / 虚拟。"""
import json, os
from drivers.local_actuator import BoardLedDriver
from drivers.matter_driver import MatterDriver

CFG = "/data/ai_cpe/services/smarthome/config.json"


def load_cfg():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_driver():
    cfg = load_cfg()
    m = cfg.get("matter") or {}
    want = (cfg.get("driver") or "auto").lower()
    if want in ("matter", "auto") and m.get("node_id"):
        d = MatterDriver(node_id=m.get("node_id"), endpoint=m.get("endpoint", 1))
        if d.available():
            return d
    return BoardLedDriver(led=(cfg.get("local") or {}).get("led"))
