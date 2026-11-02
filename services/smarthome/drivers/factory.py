#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行器驱动工厂：按 config.json 选择 Matter / 涂鸦云 / 板载 LED / 虚拟。

优先级（driver=auto）：
  1. Matter：matter.node_id 已配网且 chip-tool 可用
  2. 涂鸦云：存在 TUYA_ACCESS_ID / TUYA_ACCESS_SECRET（Moes 灯用 App 配对后只有云通道可达）
  3. 板载 LED：演示代理（proxy=true），只能证明控制面通，不是真实灯具

2026-11-02：新增 TuyaDriver —— 实测 Moes 灯本地 6668/6667/80/443 全拒绝、
RG660MK 无 BLE 适配器且设备未开 Matter 配网窗口，云控是唯一可用真灯路径。
"""
import json
import os

from drivers.local_actuator import BoardLedDriver
from drivers.matter_driver import MatterDriver
from drivers.tuya_driver import TuyaDriver

CFG = "/data/ai_cpe/services/smarthome/config.json"


def load_cfg():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get_driver():
    cfg = load_cfg()
    m = cfg.get("matter") or {}
    t = cfg.get("tuya") or {}
    want = (cfg.get("driver") or "auto").lower()
    if want in ("matter", "auto") and m.get("node_id"):
        d = MatterDriver(node_id=m.get("node_id"), endpoint=m.get("endpoint", 1))
        if d.available():
            return d
    if want in ("tuya", "auto"):
        td = TuyaDriver(device_id=t.get("device_id"), timeout=t.get("timeout", 15))
        if td.available():
            return td
    return BoardLedDriver(led=(cfg.get("local") or {}).get("led"))
