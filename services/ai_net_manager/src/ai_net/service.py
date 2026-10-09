# -*- coding: utf-8 -*-
"""ai-net-manager 常驻服务（Phase 1 Shadow）。
用法：
  python3 -m ai_net.service --config config/main.json             # shadow 常驻
  python3 -m ai_net.service --once                                # 单轮自检（测试）
  python3 -m ai_net.service --duration 60                         # 跑 60s 退出（验收）
"""
import argparse
import json
import logging
import os
import signal
import sys
import threading
import time

from ai_net.collectors.mtk_cell import MTKCellCollector
from ai_net.collectors.qoe_probe import QoEProbe
from ai_net.collectors.traffic_flow import FlowTracker, parse_conntrack
from ai_net.decision import scorer
from ai_net.decision.rules import RuleEngine
from ai_net.decision.state_machine import SelectionFSM
from ai_net.executor.shadow import ShadowExecutor
from ai_net.features.aggregator import FeatureAggregator
from ai_net.models.traffic_heuristic import TrafficHeuristic
from ai_net.storage.jsonl import JsonlWriter, StatusWriter
from ai_net.util import now_ms

log = logging.getLogger("ai_net")

DEFAULT_CONFIG = os.environ.get("AI_NET_CONFIG", "config/main.json")


class AINetService:
    def __init__(self, cfg_path, mode_override=None):
        with open(cfg_path, encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.base = os.path.dirname(os.path.dirname(os.path.abspath(cfg_path)))
        with open(os.path.join(self.base, "config", "rules.json"), encoding="utf-8") as f:
            rules_cfg = json.load(f)
        with open(os.path.join(self.base, "config", "traffic_profiles.json"), encoding="utf-8") as f:
            self.profiles_cfg = json.load(f)

        self.mode = mode_override or self.cfg.get("mode", "shadow")
        data_dir = self.cfg.get("storage", {}).get("dir") or os.path.join(self.base, "data")
        os.makedirs(data_dir, exist_ok=True)
        rot = self.cfg.get("storage", {}).get("rotate_mb", 20)
        self.w_radio = JsonlWriter(os.path.join(data_dir, "radio.jsonl"), rot)
        self.w_qoe = JsonlWriter(os.path.join(data_dir, "qoe.jsonl"), rot)
        self.w_flows = JsonlWriter(os.path.join(data_dir, "flows.jsonl"), rot)
        self.w_dec = JsonlWriter(os.path.join(data_dir, "decisions.jsonl"), rot)
        self.w_window = JsonlWriter(os.path.join(data_dir, "window.jsonl"), rot)
        self.status_w = StatusWriter(os.path.join(data_dir, "status.json"))
        self.profiles_path = os.path.join(data_dir, "cell_profiles.json")

        s = self.cfg.get("sampling_s", {})
        self.collector = MTKCellCollector(cell_ttl_s=s.get("cell", 5.0))
        qcfg = dict(self.cfg.get("qoe_probe", {}))
        qcfg["wan_iface"] = self.cfg.get("wan_iface", "ccmni2")
        self.probe = QoEProbe(qcfg)
        self.tracker = FlowTracker()
        self.agg = FeatureAggregator()
        self.classifier = TrafficHeuristic()
        self.engine = RuleEngine(rules_cfg)
        thr = self.cfg.get("thresholds", {})
        self.fsm = SelectionFSM(self.engine, mode=self.mode,
                                bad_score=thr.get("bad_qoe_score", 40),
                                good_score=thr.get("good_qoe_score", 70))
        self.executor = ShadowExecutor()

        self.cell_profiles = {}
        self._load_profiles()
        self._prof_dirty = False
        self._last_prof_save = 0.0

        self.latest = {"radio": None, "qoe": None, "flows": None,
                       "window": None, "decision": None, "candidates": []}
        self.serving = {"score": None, "breakdown": {}, "baselines": {}, "profile": "generic"}
        self.start_ts = time.time()
        self.stop_ev = threading.Event()
        self.threads = []
        self.rates = {"dl_mbps": None, "ul_mbps": None}
        self.counters = {"radio": 0, "qoe": 0, "flows": 0, "decisions": 0}

    # ---------- cell profiles (历史候选库) ----------
    def _load_profiles(self):
        try:
            with open(self.profiles_path, encoding="utf-8") as f:
                self.cell_profiles = json.load(f)
        except Exception:
            self.cell_profiles = {}

    def _save_profiles(self):
        tmp = self.profiles_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cell_profiles, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.profiles_path)
        except OSError:
            pass

    def _update_profiles(self, radio_d, decision):
        if not radio_d:
            return
        key = radio_d.get("cell_id") or radio_d.get("tac") or "unknown"
        row = self.cell_profiles.setdefault(key, {
            "obs": 0, "rsrp_sum": 0.0, "rsrp_n": 0, "score_sum": 0.0, "score_n": 0,
            "first_seen": time.time(), "last_seen": time.time()})
        if radio_d.get("rsrp_dbm") is not None:
            row["rsrp_sum"] += radio_d["rsrp_dbm"]
            row["rsrp_n"] += 1
        if decision is not None and decision.serving_score is not None:
            row["score_sum"] += decision.serving_score
            row["score_n"] += 1
        row["obs"] += 1
        row["last_seen"] = time.time()
        self._prof_dirty = True
        now = time.time()
        if self._prof_dirty and now - self._last_prof_save > 60:
            for r in self.cell_profiles.values():
                if r["rsrp_n"]:
                    r["rsrp_mean"] = round(r["rsrp_sum"] / r["rsrp_n"], 1)
                if r["score_n"]:
                    r["score_mean"] = round(r["score_sum"] / r["score_n"], 1)
            self._save_profiles()
            self._prof_dirty = False
            self._last_prof_save = now

    # ---------- loops ----------
    def radio_loop(self):
        s = self.cfg.get("sampling_s", {})
        iv = s.get("radio", 1.0)
        while not self.stop_ev.is_set():
            try:
                smp = self.collector.sample()
                d = smp.to_dict()
                self.w_radio.write(d)
                self.agg.add_radio(d)
                self.latest["radio"] = d
                self.counters["radio"] += 1
            except Exception as e:  # noqa
                log.warning("radio_loop err: %s", e)
            self.stop_ev.wait(iv)

    def qoe_loop(self):
        s = self.cfg.get("sampling_s", {})
        iv = s.get("qoe", 1.0)
        t0 = (self.cfg.get("qoe_probe", {}).get("icmp_targets") or [{"host": "223.5.5.5"}])[0]
        while not self.stop_ev.is_set():
            try:
                rtt = self.probe.ping(t0["host"])
                do_tcp = self.probe.next_tick()
                tcp_ms = self.probe.tcp_connect_ms("feishu") if do_tcp else None
                dl, ul = self.probe.rates()
                if dl is not None:
                    self.rates = {"dl_mbps": dl, "ul_mbps": ul}
                d = {"ts_ms": now_ms(), "rtt_ms": rtt, "rtt_ok": rtt is not None,
                     "probe_name": t0.get("name", t0["host"]),
                     "dl_mbps": dl, "ul_mbps": ul, "tcp_feishu_ms": tcp_ms}
                self.w_qoe.write(d)
                self.agg.add_qoe(d)
                self.latest["qoe"] = d
                self.counters["qoe"] += 1
            except Exception as e:  # noqa
                log.warning("qoe_loop err: %s", e)
            self.stop_ev.wait(iv)

    def flows_loop(self):
        s = self.cfg.get("sampling_s", {})
        iv = s.get("flows", 2.0)
        while not self.stop_ev.is_set():
            try:
                rows = parse_conntrack()
                res = self.tracker.sample(rows)
                agg = dict(res["agg"])
                agg["ts_ms"] = now_ms()
                self.w_flows.write({"ts_ms": now_ms(), "agg": res["agg"], "top": res["top"]})
                self.agg.add_flows(agg)
                self.latest["flows"] = {"agg": res["agg"], "top": res["top"][:5]}
                self.counters["flows"] += 1
            except Exception as e:  # noqa
                log.warning("flows_loop err: %s", e)
            self.stop_ev.wait(iv)

    def decision_tick(self):
        now = now_ms()
        cell = {}
        if self.latest["radio"]:
            cell = {"cell_id": self.latest["radio"].get("cell_id"),
                    "tac": self.latest["radio"].get("tac"),
                    "rat": self.latest["radio"].get("rat")}
        ctx = {"dwell_s": self.fsm.dwell_s(now), "switches_5min": self.fsm.switches_5min(now),
               "last_switch_reason": self.fsm.recent_switch_reason}
        window = self.agg.build(self.cfg.get("default_window_s", 10), cell, ctx)
        traffic = self.classifier.predict(window.features)
        window.traffic_class = traffic.cls
        window.traffic_confidence = traffic.confidence
        penalty = 5.0 if (self.fsm.last_switch_ms and now - self.fsm.last_switch_ms < 30_000) else 0.0
        score, bd, missing, baselines, prof = scorer.score_serving(
            window, self.profiles_cfg, traffic, penalty)
        self.serving = {"score": score, "breakdown": bd, "baselines": baselines,
                        "profile": prof.get("_cls", "generic"), "missing": missing}
        cur_key = cell.get("cell_id") or cell.get("tac") or "unknown"
        candidates = scorer.rank_candidates(self.cell_profiles, cur_key, self.profiles_cfg, traffic)
        dec = self.fsm.tick(window, score, candidates, traffic)
        dd = dec.to_dict()
        dd["baselines"] = baselines
        dd["serving_breakdown"] = bd
        self.w_dec.write(dd)
        self.latest["window"] = window.to_dict()
        self.w_window.write(self.latest["window"])
        self.latest["decision"] = dd
        self.latest["candidates"] = candidates
        self.counters["decisions"] += 1
        self._update_profiles(self.latest["radio"], dec)
        self.status_w.write(self.get_status())
        return dd

    def decision_loop(self):
        iv = self.cfg.get("sampling_s", {}).get("decision", 2.0)
        while not self.stop_ev.is_set():
            try:
                self.decision_tick()
            except Exception as e:  # noqa
                log.warning("decision_loop err: %s", e)
            self.stop_ev.wait(iv)

    # ---------- API accessors ----------
    def get_status(self):
        dec = self.latest.get("decision") or {}
        rad = self.latest.get("radio") or {}
        return {
            "service": "ai-net-manager", "version": "0.1.0",
            "mode": self.mode, "state": self.fsm.state,
            "uptime_s": int(time.time() - self.start_ts),
            "device": self.cfg.get("device", {}),
            "sampling_s": self.cfg.get("sampling_s", {}),
            "serving_cell": {"cell_id": rad.get("cell_id"), "tac": rad.get("tac"),
                             "rat": rad.get("rat"), "rsrp_dbm": rad.get("rsrp_dbm"),
                             "reg_state": rad.get("reg_state")},
            "qoe_score": self.serving.get("score"),
            "qoe_breakdown": self.serving.get("breakdown"),
            "baselines": self.serving.get("baselines"),
            "traffic": {"class": dec.get("traffic_class"), "confidence": dec.get("traffic_confidence")},
            "rates": self.rates,
            "fsm": {"state": self.fsm.state, "dwell_s": round(self.fsm.dwell_s(), 1),
                    "switches_10min": self.fsm.switches_10min(),
                    "cooldown_left_s": round(max(0.0, (self.fsm.cooldown_until_ms - now_ms()) / 1000.0), 1)},
            "candidates": (self.latest.get("candidates") or [])[:3],
            "last_decision": {"trigger": dec.get("trigger"), "action": dec.get("action"),
                              "blocked_by": dec.get("blocked_by"), "ts_ms": dec.get("ts_ms")},
            "executor": {"mode": self.mode, "enabled": bool(self.cfg.get("executor", {}).get("enabled")),
                         "capability": "see reports/modem_capability.json"},
            "counters": self.counters,
            "ts_ms": now_ms(),
        }

    def get_radio(self):
        return {"radio": self.latest.get("radio"), "window": self.latest.get("window")}

    def get_candidates(self):
        return {"candidates": self.latest.get("candidates") or [],
                "serving": self.serving, "note": "候选来自历史 cell_profile（本固件无邻区 API）"}

    def get_decision(self):
        return self.latest.get("decision") or {}

    def set_mode(self, mode):
        if mode not in ("shadow", "recommend", "execute"):
            return {"ok": False, "error": "mode must be shadow|recommend|execute"}
        if mode == "execute" and not self.cfg.get("executor", {}).get("enabled"):
            return {"ok": False, "error": "executor disabled in config (executor.enabled=false)"}
        self.mode = mode
        self.fsm.mode = mode
        return {"ok": True, "mode": mode}

    def reload_model(self):
        self.classifier = TrafficHeuristic()
        return {"ok": True, "source": "heuristic", "note": "phase1: heuristic classifier (no model file yet)"}

    # ---------- run ----------
    def run_once(self, quiet=False):
        smp = self.collector.sample()
        d = smp.to_dict()
        self.w_radio.write(d)
        self.agg.add_radio(d)
        self.latest["radio"] = d
        self.counters["radio"] += 1
        for i in range(2):
            rtt = self.probe.ping((self.cfg.get("qoe_probe", {}).get("icmp_targets") or [{}])[0].get("host", "223.5.5.5"))
            tcp_ms = self.probe.tcp_connect_ms("feishu")
            dl, ul = self.probe.rates()
            if dl is not None:
                self.rates = {"dl_mbps": dl, "ul_mbps": ul}
            dq = {"ts_ms": now_ms(), "rtt_ms": rtt, "rtt_ok": rtt is not None,
                  "probe_name": "alidns", "dl_mbps": dl, "ul_mbps": ul, "tcp_feishu_ms": tcp_ms}
            self.w_qoe.write(dq)
            self.agg.add_qoe(dq)
            self.latest["qoe"] = dq
            self.counters["qoe"] += 1
            if i == 0:
                self.stop_ev.wait(1.2)
        rows = parse_conntrack()
        res = self.tracker.sample(rows)
        agg = dict(res["agg"]); agg["ts_ms"] = now_ms()
        self.w_flows.write({"ts_ms": now_ms(), "agg": res["agg"], "top": res["top"]})
        self.agg.add_flows(agg)
        self.latest["flows"] = {"agg": res["agg"], "top": res["top"][:5]}
        self.counters["flows"] += 1
        dd = self.decision_tick()
        if not quiet:
            print(json.dumps(self.get_status(), ensure_ascii=False, indent=1))
        return dd

    def run(self, duration=None):
        names = [("radio", self.radio_loop), ("qoe", self.qoe_loop),
                 ("flows", self.flows_loop), ("decision", self.decision_loop)]
        for n, fn in names:
            t = threading.Thread(target=fn, name=n, daemon=True)
            t.start()
            self.threads.append(t)
        api_srv = None
        try:
            from ai_net.api import start_api
            a = self.cfg.get("api", {})
            api_srv = start_api(self, a.get("host", "0.0.0.0"), int(a.get("port", 8123)))
            log.info("api on %s:%s", a.get("host"), a.get("port"))
        except Exception as e:  # noqa
            log.warning("api start failed: %s", e)

        def _stop(signum=None, frame=None):
            self.stop_ev.set()
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        log.info("service started mode=%s duration=%s", self.mode, duration)
        t0 = time.time()
        while not self.stop_ev.is_set():
            if duration and time.time() - t0 >= duration:
                break
            self.stop_ev.wait(1.0)
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=3)
        if api_srv:
            try:
                api_srv.shutdown()
            except Exception:  # noqa
                pass
        self.status_w.write(self.get_status())
        log.info("service stopped; counters=%s", self.counters)
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--mode", default=None, choices=[None, "shadow", "recommend", "execute"])
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--duration", type=int, default=None)
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    handlers = [logging.StreamHandler(sys.stderr)]
    if args.log:
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)

    svc = AINetService(args.config, mode_override=args.mode)
    if args.once:
        svc.run_once()
        return 0
    return svc.run(duration=args.duration)


if __name__ == "__main__":
    sys.exit(main())
