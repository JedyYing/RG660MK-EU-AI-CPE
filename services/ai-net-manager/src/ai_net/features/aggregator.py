"""Feature Aggregator（设计文档 15.5）：5/10/30s rolling 窗 + 缺失标记。

输入：RadioSample / QoESample / TrafficPrediction 的持续流。
输出：schemas.FeatureWindow（决策模型的输入单元）。
"""
from __future__ import annotations

from collections import deque

from . import transforms as T
from .history import CellHistory


class _Ring:
    __slots__ = ("ts", "v")

    def __init__(self, maxlen: int = 600):
        self.ts = deque(maxlen=maxlen)
        self.v = deque(maxlen=maxlen)

    def add(self, ts_ms: int, val) -> None:
        self.ts.append(ts_ms)
        self.v.append(val)

    def since(self, ts_ms: int, step_s: float = 1.0) -> list:
        out = []
        for t, v in zip(self.ts, self.v):
            if t >= ts_ms:
                out.append(v)
        return out


class FeatureAggregator:
    def __init__(self, windows_s=(5, 10, 30), default_window_s: int = 10,
                 history: CellHistory | None = None):
        self.windows_s = tuple(windows_s)
        self.default_window_s = default_window_s
        self.history = history or CellHistory()
        self.r_rsrp, self.r_rsrq, self.r_sinr = _Ring(), _Ring(), _Ring()
        self.r_dl, self.r_ul, self.r_rtt = _Ring(), _Ring(), _Ring()
        self.r_loss, self.r_flows = _Ring(), _Ring()
        self._pred = {"class": "generic", "confidence": 0.0}
        self._cell = {}
        self._dwell_s = 0.0
        self._switches_10min = 0

    # -- 输入 ----------------------------------------------------------
    def add_radio(self, s) -> None:
        self.r_rsrp.add(s.ts_ms, s.rsrp_dbm)
        self.r_rsrq.add(s.ts_ms, s.rsrq_db)
        self.r_sinr.add(s.ts_ms, s.sinr_db)
        for n in s.neighbours:
            self.history.observe_neighbour(n)
        self._cell = {"cell_id": s.cell_id, "band": s.band, "pci": s.pci,
                      "arfcn": s.arfcn, "rat": s.rat, "tac": s.tac}

    def add_qoe(self, q) -> None:
        self.r_dl.add(q.ts_ms, q.dl_mbps)
        self.r_ul.add(q.ts_ms, q.ul_mbps)
        self.r_rtt.add(q.ts_ms, q.rtt_ms_p50)
        self.r_loss.add(q.ts_ms, q.loss_rate)
        self.r_flows.add(q.ts_ms, q.active_flows)

    def set_prediction(self, cls: str, confidence: float) -> None:
        self._pred = {"class": cls or "generic", "confidence": float(confidence or 0.0)}

    def set_context(self, dwell_s: float, switches_10min: int) -> None:
        self._dwell_s = float(dwell_s)
        self._switches_10min = int(switches_10min)

    def serving_key(self) -> str:
        return self.history.key(None, None, self._cell.get("rat"),
                                self._cell.get("band"), self._cell.get("arfcn"),
                                self._cell.get("pci"))

    # -- 输出 ----------------------------------------------------------
    def build(self, now_ms: int, window_s: int | None = None):
        from schemas import FeatureWindow

        w = window_s or self.default_window_s
        t0 = now_ms - w * 1000
        step = 1.0
        rsrp = self.r_rsrp.since(t0, step)
        rsrq = self.r_rsrq.since(t0, step)
        sinr = self.r_sinr.since(t0, step)
        dl = self.r_dl.since(t0, step)
        ul = self.r_ul.since(t0, step)
        rtt = self.r_rtt.since(t0, step)
        loss = self.r_loss.since(t0, step)
        flows = self.r_flows.since(t0, step)

        f = {
            "rsrp_mean": T.mean(rsrp), "rsrp_std": T.pstdev(rsrp),
            "rsrp_min": T.vmin(rsrp), "rsrp_p10": T.quantile_nr(rsrp, 0.10),
            "rsrp_p90": T.quantile_nr(rsrp, 0.90), "rsrp_slope": T.slope(rsrp, step),
            "rsrq_mean": T.mean(rsrq), "rsrq_p10": T.quantile_nr(rsrq, 0.10),
            "sinr_mean": T.mean(sinr), "sinr_std": T.pstdev(sinr),
            "sinr_slope": T.slope(sinr, step),
            "dl_mbps_p50": T.quantile_nr(dl, 0.50), "dl_mbps_p95": T.quantile_nr(dl, 0.95),
            "ul_mbps_p50": T.quantile_nr(ul, 0.50),
            "dl_busy_ratio": _busy_ratio(dl, 1.0),
            "rtt_p50": T.quantile_nr(rtt, 0.50), "rtt_p95": T.quantile_nr(rtt, 0.95),
            "rtt_slope": T.slope(rtt, step),
            "jitter_p95": T.quantile_nr(T.jitter_absdiff(rtt), 0.95),
            "loss_rate": _loss_rate(loss),
            "active_flows_mean": T.mean(flows),
            "traffic_class": self._pred["class"],
            "traffic_confidence": self._pred["confidence"],
            "cell_dwell_s": round(self._dwell_s, 1),
            "switches_10min": self._switches_10min,
        }
        missing = [k for k, v in f.items() if v is None]
        missing.append("ca_cc_count")           # 本固件不可得，显式缺失
        f["ca_cc_count"] = None

        return FeatureWindow(
            window_end_ms=now_ms, window_s=int(w),
            traffic_class=self._pred["class"], traffic_confidence=self._pred["confidence"],
            cell=dict(self._cell), features=f, missing=sorted(set(missing)),
        )


def _busy_ratio(vals: list, thresh: float) -> float | None:
    v = [x for x in vals if x is not None]
    if not v:
        return None
    return round(sum(1 for x in v if x > thresh) / len(v), 3)


def _loss_rate(vals: list) -> float | None:
    v = [x for x in vals if x is not None]
    if not v:
        return None
    return round(sum(v) / len(v), 4)
