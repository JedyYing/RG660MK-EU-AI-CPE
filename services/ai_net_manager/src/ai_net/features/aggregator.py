# -*- coding: utf-8 -*-
"""FeatureAggregator：5/10/30s 滚动窗（设计 §4/§15.5）。
输入：radio/qoe/flow 样本；输出：FeatureWindow（含 missing flags）。"""
import threading
import time
from collections import deque

from schemas import FeatureWindow
from ..util import now_ms, percentile
from .transforms import jitter_p95, loss_rate, summarize


class FeatureAggregator:
    def __init__(self, maxlen=180):
        self._radio = deque(maxlen=maxlen)
        self._qoe = deque(maxlen=maxlen)
        self._flows = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add_radio(self, d):
        with self._lock:
            self._radio.append(d)

    def add_qoe(self, d):
        with self._lock:
            self._qoe.append(d)

    def add_flows(self, d):
        with self._lock:
            self._flows.append(d)

    def _slice(self, buf, cut_ms):
        return [x for x in buf if x.get("ts_ms", 0) >= cut_ms]

    def build(self, window_s, current_cell, ctx=None):
        """ctx: {dwell_s, switches_5min, last_switch_reason}（来自状态机）"""
        cut = now_ms() - int(window_s * 1000)
        with self._lock:
            radio = self._slice(self._radio, cut)
            qoe = self._slice(self._qoe, cut)
            flows = self._slice(self._flows, cut)

        missing = []
        f = {}

        rsrp = [r.get("rsrp_dbm") for r in radio]
        st = summarize(rsrp)
        f["rsrp_mean"] = st["mean"]
        f["rsrp_std"] = st["std"]
        f["rsrp_min"] = st["min"]
        f["rsrp_p10"] = st["p10"]
        f["rsrp_p90"] = st["p90"]
        f["rsrp_slope"] = st["slope"]

        for key, name in (("sinr_db", "sinr"), ("rsrq_db", "rsrq")):
            vals = [r.get(key) for r in radio if r.get(key) is not None]
            if vals:
                st2 = summarize(vals)
                f[name + "_mean"] = st2["mean"]
                f[name + "_std"] = st2["std"]
                f[name + "_p10"] = st2["p10"]
            else:
                f[name + "_mean"] = None
                f[name + "_std"] = None
                f[name + "_p10"] = None
                missing.append(name + "_mean")

        # QoE
        rtts = [q.get("rtt_ms") for q in qoe]
        rtt_valid = [x for x in rtts if x is not None]
        f["rtt_p50"] = percentile(rtt_valid, 50)
        f["rtt_p95"] = percentile(rtt_valid, 95)
        f["jitter_p95"] = jitter_p95(rtt_valid)
        f["loss_rate"] = loss_rate([q.get("rtt_ok") for q in qoe])
        dls = [q.get("dl_mbps") for q in qoe]
        uls = [q.get("ul_mbps") for q in qoe]
        f["dl_mbps_p50"] = percentile([x for x in dls if x is not None], 50)
        f["dl_mbps_p95"] = percentile([x for x in dls if x is not None], 95)
        f["ul_mbps_p50"] = percentile([x for x in uls if x is not None], 50)
        f["ul_mbps_p95"] = percentile([x for x in uls if x is not None], 95)
        f["stall_ratio"] = None       # 无应用埋点（设计 §17：保留 missing）
        missing.append("stall_ratio")
        f["tcp_retrans_rate"] = None
        missing.append("tcp_retrans_rate")
        f["bler_dl"] = None
        f["bler_ul"] = None
        missing += ["bler_dl", "bler_ul"]

        # 流量/上下文
        last_flow = flows[-1] if flows else None
        f["active_flows"] = last_flow.get("agg", {}).get("n_flows") if last_flow else None
        f["flows_wan"] = last_flow.get("agg", {}).get("n_wan") if last_flow else None
        f["flow_dl_kbps"] = last_flow.get("agg", {}).get("dl_kbps") if last_flow else None
        f["flow_ul_kbps"] = last_flow.get("agg", {}).get("ul_kbps") if last_flow else None
        f["ca_cc_count"] = None
        missing.append("ca_cc_count")

        ctx = ctx or {}
        f["cell_dwell_s"] = ctx.get("dwell_s")
        f["switches_5min"] = ctx.get("switches_5min")

        return FeatureWindow(
            window_end_ms=now_ms(), window_s=int(window_s),
            cell=current_cell or {},
            features=f, missing_flags=missing,
        )
