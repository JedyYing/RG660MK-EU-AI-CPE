"""Cell Profile 历史库（设计文档 8.1）：按 (mcc,mnc,rat,band,arfcn,pci) 聚合历史体验。

Phase 1 轻量实现：内存字典 + 可选 JSON 快照；长期数据由 JSONL 落盘、replay 可重建。
"""
from __future__ import annotations

import json
import os


class CellHistory:
    def __init__(self):
        # key -> {rsrp:[...], qoe:[...], serve_n, seen_n}
        self._d: dict = {}

    @staticmethod
    def key(mcc, mnc, rat, band, arfcn, pci) -> str:
        return "%s-%s|%s|%s|%s|%s" % (mcc or "?", mnc or "?", rat or "?",
                                      band or "?", arfcn, pci)

    @staticmethod
    def neighbour_key(n) -> str:
        return "?-?|%s|%s|%s|%s" % (n.rat, None, n.arfcn, n.pci)

    def observe_neighbour(self, n) -> None:
        k = self.neighbour_key(n)
        e = self._d.setdefault(k, {"rsrp": [], "qoe": [], "serve_n": 0, "seen_n": 0})
        if n.rsrp_dbm is not None:
            e["rsrp"].append(n.rsrp_dbm)
            del e["rsrp"][:-240]                     # 保留最近 240 个
        e["seen_n"] += 1

    def observe_serving(self, key: str, rsrp, qoe_score) -> None:
        e = self._d.setdefault(key, {"rsrp": [], "qoe": [], "serve_n": 0, "seen_n": 0})
        if rsrp is not None:
            e["rsrp"].append(rsrp)
            del e["rsrp"][:-240]
        if qoe_score is not None:
            e["qoe"].append(qoe_score)
            del e["qoe"][:-240]
        e["serve_n"] += 1

    def probe(self, key: str) -> dict:
        e = self._d.get(key)
        if not e:
            return {}
        r, q = e["rsrp"], e["qoe"]
        return {
            "n": len(r),
            "rsrp_mean": round(sum(r) / len(r), 1) if r else None,
            "rsrp_p90": sorted(r)[min(len(r) - 1, max(0, int(0.9 * len(r)) - 1))] if r else None,
            "qoe_mean": round(sum(q) / len(q), 1) if q else None,
            "serve_n": e["serve_n"], "seen_n": e["seen_n"],
        }

    def save(self, path: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._d, f)
            os.replace(tmp, path)
            return True
        except OSError:
            return False

    def load(self, path: str) -> bool:
        try:
            with open(path, encoding="utf-8") as f:
                self._d = json.load(f)
            return True
        except (OSError, json.JSONDecodeError):
            return False
