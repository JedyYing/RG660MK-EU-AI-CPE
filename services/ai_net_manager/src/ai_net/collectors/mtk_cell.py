# -*- coding: utf-8 -*-
"""无线侧采集（MTK/mipc 适配层）→ RadioSample。
实测能力（2026-10-09）：nw_get_signal(RSRP+RAT) / check_nw_status / nw_radio_state_get /
AT+C5GREG(服务小区 TAC+NCI) / AT+CESQ(原始)；QENG/QCAINFO/QNWINFO 不支持(CME4)。
不可得字段一律进 missing（设计 §3.3：不要伪造为 0）。
[结构] parse_* 为纯函数（fixtures 可测）；read_* 执行设备调用；MTKCellCollector 做 TTL 缓存。"""
import re
import time

from schemas import RadioSample
from ..util import now_ms
from .at_transport import at_clean, mipc_clean

RE_SIGNAL = re.compile(r"RAT\s+([0-9A-Za-z+/.-]+)\s*,\s*RSRP=(-?\d+)")
RE_RAT = re.compile(r"Current:\s*(\S+)")
RE_REG = re.compile(r"MIPC_NW_REGISTER_STATE_([A-Z]+)")
RE_RADIO = re.compile(r"MIPC_NW_RADIO_STATE_([A-Z]+)")
RE_C5GREG_A = re.compile(r'\+C5GREG:\s*\d+,\d+,"([0-9A-Fa-f]{1,8})","([0-9A-Fa-f]+)"')
RE_C5GREG_B = re.compile(r'\+C5GREG:\s*\d+,"([0-9A-Fa-f]{1,8})","([0-9A-Fa-f]+)"')
RE_CESQ = re.compile(r"\+CESQ:\s*([0-9,\s]+)")

UNAVAILABLE = ["rsrq_db", "sinr_db", "rssi_dbm", "cqi", "tx_power_dbm",
               "ca_active", "ca_cc_count", "pci", "arfcn", "mcc", "mnc", "band"]


# ---------- 纯解析函数（fixtures 单测覆盖） ----------
def parse_signal(out):
    m = RE_SIGNAL.search(out or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def parse_rat(out):
    m = RE_RAT.search(out or "")
    v = m.group(1) if m else None
    if v and "5G" in v:
        return "5G"
    if v and ("4G" in v or "LTE" in v.upper()):
        return "LTE"
    return v


def parse_reg(out):
    m = RE_REG.search(out or "")
    return m.group(1) if m else None


def parse_radio(out):
    m = RE_RADIO.search(out or "")
    return m.group(1) if m else None


def parse_cell(out):
    m = RE_C5GREG_A.search(out or "") or RE_C5GREG_B.search(out or "")
    if m:
        return {"tac": m.group(1).upper(), "nci": m.group(2).upper()}
    return {}


def parse_cesq(out):
    m = RE_CESQ.search(out or "")
    if m:
        return {"raw_values": [v.strip() for v in m.group(1).split(",")]}
    return {"raw_values": []}


# ---------- 执行函数 ----------
def read_signal():
    rc, out = mipc_clean(["--nw_get_signal"], 8)
    rat, rsrp = parse_signal(out)
    return rat, rsrp, (out or "").strip()


def read_rat():
    rc, out = mipc_clean(["--nw_get_rat"], 8)
    return parse_rat(out)


def read_reg():
    rc, out = mipc_clean(["--check_nw_status"], 8)
    return parse_reg(out)


def read_radio_state():
    rc, out = mipc_clean(["--nw_radio_state_get"], 8)
    return parse_radio(out)


def read_cell():
    rc, o = at_clean("AT+C5GREG=2", 12)
    rc2, o2 = at_clean("AT+C5GREG?", 12)
    raw = o2 or o
    return parse_cell(raw), (raw or "")


def read_cesq():
    rc, out = at_clean("AT+CESQ", 12)
    d = parse_cesq(out)
    d["raw_line"] = (out or "").strip()
    return d


class MTKCellCollector:
    """带子采样 TTL 的无线采集器（signal 每次都取；其余按 TTL 缓存）。"""

    def __init__(self, cell_ttl_s=5.0, reg_ttl_s=3.0, radio_ttl_s=10.0, cesq_ttl_s=30.0):
        self.cell_ttl_s = cell_ttl_s
        self.reg_ttl_s = reg_ttl_s
        self.radio_ttl_s = radio_ttl_s
        self.cesq_ttl_s = cesq_ttl_s
        self._cell = {}
        self._cell_raw = ""
        self._cell_ts = 0.0
        self._reg = (None, 0.0)
        self._radio = (None, 0.0)
        self._rat = (None, 0.0)
        self._cesq = ({}, 0.0)

    def _refresh(self, now):
        if now - self._cell_ts >= self.cell_ttl_s:
            self._cell, self._cell_raw = read_cell()
            self._cell_ts = now
        if now - self._reg[1] >= self.reg_ttl_s:
            self._reg = (read_reg(), now)
        if now - self._radio[1] >= self.radio_ttl_s:
            self._radio = (read_radio_state(), now)
        if now - self._rat[1] >= self.radio_ttl_s:
            self._rat = (read_rat(), now)
        if now - self._cesq[1] >= self.cesq_ttl_s:
            self._cesq = (read_cesq(), now)

    def sample(self):
        now = time.time()
        rat0, rsrp, sig_raw = read_signal()
        self._refresh(now)
        rat = self._rat[0] or rat0
        cell = dict(self._cell)
        s = RadioSample(
            ts_ms=now_ms(),
            rat=rat if rat else rat0,
            reg_state=self._reg[0],
            radio_on=(self._radio[0] == "ON") if self._radio[0] else None,
            cell_id=cell.get("nci"),
            tac=cell.get("tac"),
            rsrp_dbm=float(rsrp) if rsrp is not None else None,
            missing=list(UNAVAILABLE),
            raw={"nw_get_signal": sig_raw,
                 "c5greg": self._cell_raw,
                 "cesq": self._cesq[0].get("raw_line", "")},
        )
        return s
