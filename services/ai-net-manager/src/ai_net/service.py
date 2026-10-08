"""ai-net-manager 主服务（设计文档 15.6 编排 / 15.8 降级 / 12 本地接口）。

单进程主循环（全部只读轮询，禁流式上报）：
  radio   每 sample_interval_s   AT+ECELLMEAS?         （服务小区 + 邻区测量）
  reg     每 reg_interval_s      AT+C5GREG?            （注册态/NCI/TAC）
  ecsq    每 ecsq_interval_s     AT+ECSQ?              （标准 RSRP 校准）
  qoe     每 1s                  netdev 速率 + ICMP 探针（多目标轮询）
  flow    每 conntrack_interval_s  conntrack 差分 → FlowTracker → 业务分类
  decide  每 decision_interval_s  FeatureWindow → QoE 分 → 候选分 → 状态机 step
  state   每 5s                  state.json 落盘（供 API/诊断）

安全（设计 7/8/13 章）：
- 默认 shadow：状态机只"记录会怎么做"，executor_level=L0，绝不写 Modem；
- execute 需 rules.safety.executor_enabled=true 且 capability 审计通过（EMMCHLCK 可用），
  否则启动时自动降级 shadow 并记事件；
- 连续 AT 失败 >= 3 → modem_ok=False（状态机在 guard 拦截）；
- 执行器连续失败 >= safety.max_consecutive_action_failures → 运行期自动降级 shadow。
"""
from __future__ import annotations

import json
import os
import signal
import time
from collections import deque

from ai_net import config as cfgmod
from ai_net.collectors import modem
from ai_net.collectors import qoe_probe
from ai_net.collectors.at_transport import ATTransport
from ai_net.collectors.traffic_flow import FlowTracker
from ai_net.decision import scorer
from ai_net.decision.rules import RuleEngine
from ai_net.decision.state_machine import SelectionStateMachine
from ai_net.executor import capability
from ai_net.executor.mtk_lock import MtkLockExecutor
from ai_net.executor.shadow import ShadowExecutor
from ai_net.features.aggregator import FeatureAggregator
from ai_net.features.history import CellHistory
from ai_net.models.registry import ModelRegistry
from ai_net.models.traffic import HeuristicTrafficClassifier, make_classifier
from ai_net.storage.jsonl import JsonlWriter

AT_FAIL_DEGRADE = 3            # 连续 AT 失败 → modem_ok=False
STATE_EVERY_S = 5.0


class AiNetService:
    def __init__(self, cfg: dict, *, transport=None, clock=time.monotonic,
                 wall=time.time, sleeper=time.sleep):
        self.cfg = cfg
        self.clock = clock
        self.wall = wall
        self.sleeper = sleeper
        svc = cfg.get("service", {})
        mc = cfg.get("modem", {})
        self.mode = svc.get("mode", "shadow")
        self.sample_s = float(svc.get("sample_interval_s", 1))
        self.neighbour_s = float(svc.get("neighbour_interval_s", 5))
        self.decision_s = float(svc.get("decision_interval_s", 2))
        self.feature_window_s = int(svc.get("feature_window_s", 10))
        self.reg_s = float(mc.get("reg_interval_s", 10))
        self.ecsq_s = float(mc.get("ecsq_interval_s", 30))
        self.data_dir = svc.get("data_dir", "/data/ai_net/data")
        self.log_dir = svc.get("log_dir", "/data/ai_net/log")
        self.state_file = svc.get("state_file", "/data/ai_net/state.json")
        self.pid_file = svc.get("pid_file", "/var/run/ai_net.pid")

        self.qc = cfg.get("qoe", {})
        self.sc = cfg.get("storage", {})
        self.tp = cfg.get("traffic_profiles", {})
        self.rules_cfg = cfg.get("rules", {})

        # 运行环境
        self._transport_injected = transport is not None
        self.transport = transport
        self.raw_at = None
        self.history = CellHistory()
        self.aggregator = FeatureAggregator(default_window_s=self.feature_window_s,
                                            history=self.history)
        self.rules = RuleEngine(self.rules_cfg.get("selection", {}),
                                self.rules_cfg.get("profiles", {}),
                                self.tp.get("unknown_threshold", 0.6))
        self.shadow = ShadowExecutor()
        self.executor = self.shadow
        self.machine = SelectionStateMachine(self.rules, executor=self.shadow,
                                             mode=self.mode if self.mode != "execute" else "execute",
                                             bad_qoe_threshold=scorer.BAD_QOE_SCORE)
        self.classifier = HeuristicTrafficClassifier()
        self.registry = ModelRegistry()
        self.flow_tracker = FlowTracker()
        self.rate_meter = qoe_probe.RateMeter()

        # 运行时状态
        self.writers: dict = {}
        self.decision_path = ""
        self.wan_if: str | None = None
        self._wan_checked_at = 0.0
        self._rtt_ring = deque(maxlen=120)          # (ts, rtt|None, target)
        self._probe_i = 0
        self._flow_win: dict | None = None
        self._radio = None                          # 最近 RadioSample
        self._c5g_lines: list = []
        self._ecsq_lines: list = []
        self._cand_cache: list = []
        self._cand_at = 0.0
        self._last_pred = None                      # 最近一次业务分类结果
        self._qoe_score: float | None = None
        self._qoe_bits: dict = {}
        self.at_fail_streak = 0
        self.decisions_total = 0
        self.started_at = None
        self._stop = False
        self._prev_machine_state = "NORMAL"
        self._pending_lock_expect: dict | None = None
        self.api = None
        self.probe_report: dict | None = None

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------
    def setup(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        rotate = int(self.sc.get("jsonl_rotate_mb", 200))
        for name in list((self.cfg.get("logging", {}) or {}).get("jsonl") or []) + ["events"]:
            if name not in self.writers:
                self.writers[name] = JsonlWriter(os.path.join(self.log_dir, name + ".jsonl"),
                                                 rotate_mb=rotate)
        self.decision_path = os.path.join(self.log_dir, "decision.jsonl")
        if (self.cfg.get("modem", {}) or {}).get("save_raw", True):
            raw_dir = (self.cfg.get("modem", {}) or {}).get("raw_dir", self.data_dir + "/raw_at")
            self.raw_at = JsonlWriter(os.path.join(raw_dir, "at.jsonl"), rotate_mb=rotate)
        if self.transport is None:
            mc = self.cfg.get("modem", {})
            self.transport = ATTransport(
                backends=mc.get("backends"),
                lock_file=mc.get("lock_file"),
                timeout_s=float(mc.get("command_timeout_s", 6)),
                allow_streaming_modes=bool(mc.get("allow_streaming_modes", False)))
        self.history.load(os.path.join(self.data_dir, "cell_history.json"))
        self.registry.load((self.cfg.get("models", {}) or {}).get("traffic"),
                           (self.cfg.get("models", {}) or {}).get("qoe"))
        self.classifier = make_classifier(self.registry.traffic,
                                          self.tp.get("unknown_threshold", 0.6))

        # 能力审计 → execute 门禁
        if self.mode == "execute":
            ok, why = self._executor_gate()
            if not ok:
                self.event("auto_degrade", mode_from="execute", mode_to="shadow", reason=why)
                self.mode = "shadow"
                self.machine.set_mode("shadow")
        if self.mode == "execute":
            self.executor = MtkLockExecutor(self.transport, raw_logger=self.raw_at)
            self.machine.executor = self.executor
        self.started_at = self.clock()
        self._write_pid()
        self.event("service_start", mode=self.mode, sample_s=self.sample_s,
                   decision_s=self.decision_s,
                   transport=[b.name for b in getattr(self.transport, "backends", [])])

    def _executor_gate(self) -> tuple:
        """execute 模式前置条件：配置开关 + 能力探测（只读）。"""
        safety = self.rules_cfg.get("safety", {})
        if not safety.get("executor_enabled", False):
            return False, "executor_enabled=false"
        rep = capability.build_report(self.transport)
        self.probe_report = rep
        rec = (rep.get("executor_recommendation") or {}).get("level")
        if rec != "L3_available":
            return False, "capability=%s" % rec
        return True, "ok"

    def _write_pid(self) -> None:
        try:
            with open(self.pid_file, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 采集
    # ------------------------------------------------------------------
    def at(self, cmd: str, timeout: float | None = None):
        r = self.transport.send(cmd, timeout)
        if self.raw_at is not None:
            self.raw_at.write({"ts": round(self.wall(), 3), "cmd": cmd, "ok": r.ok,
                               "backend": r.backend, "error": r.error,
                               "elapsed_ms": r.elapsed_ms, "raw": (r.raw or "")[:4000]})
        if r.ok:
            self.at_fail_streak = 0
        else:
            self.at_fail_streak += 1
        return r

    def sample_radio(self, now: float) -> None:
        """唯一高频 AT 命令：只读查询（ECELLMEAS? 禁写形态由 transport 硬护栏保证）。"""
        r = self.at(modem.CMD_ECELLMEAS_QUERY)
        if not r.ok:
            if self.at_fail_streak >= AT_FAIL_DEGRADE:
                self.event("modem_error", streak=self.at_fail_streak, error=r.error)
            return
        s = modem.build_radio_sample(r.lines, self._c5g_lines, self._ecsq_lines,
                                     ts_ms=int(self.wall() * 1000))
        self._radio = s
        self.aggregator.add_radio(s)
        self.machine.note_serving_cell(now, s.cell_id or s.pci)
        self.aggregator.set_context(self.machine.dwell_s(now) or 0.0,
                                    self.machine.switches_10min(now))
        if s.cell_id:
            self.history.observe_serving(self.history.key(s.mcc, s.mnc, s.rat, s.band,
                                                          s.arfcn, s.pci), s.rsrp_dbm,
                                         self._qoe_score)
        self._write("radio", s.to_dict())

    def sample_reg(self, now: float) -> None:
        r = self.at(modem.CMD_C5GREG_QUERY)
        if r.ok:
            self._c5g_lines = r.lines

    def sample_ecsq(self, now: float) -> None:
        r = self.at(modem.CMD_ECSQ_QUERY)
        if r.ok:
            self._ecsq_lines = r.lines

    def sample_qoe(self, now: float) -> None:
        if now - self._wan_checked_at > 30 or not self.wan_if:
            self.wan_if = qoe_probe.detect_wan_if()
            self._wan_checked_at = now
        counters = qoe_probe.read_netdev(self.wan_if) if self.wan_if else None
        dl, ul = self.rate_meter.update(counters, now)
        targets = self.qc.get("probe_targets") or ["223.5.5.5"]
        target = targets[self._probe_i % len(targets)]
        self._probe_i += 1
        rtt = qoe_probe.icmp_ping(target, int(self.qc.get("probe_timeout_ms", 800)),
                                  seq=self._probe_i & 0xFFFF)
        ts_ms = int(self.wall() * 1000)
        self._rtt_ring.append((now, rtt))
        win = float(self.qc.get("probe_window_s", 10))
        recent = [v for t, v in self._rtt_ring if now - t <= win]
        got = [v for v in recent if v is not None]
        loss = round(1 - len(got) / len(recent), 4) if recent else None
        rtts = sorted(got)
        from ai_net.features import transforms as T
        jit = T.jitter_absdiff(got)
        q = self._qoe_sample(ts_ms, dl, ul, rtts, loss, jit)
        self.aggregator.add_qoe(q)
        self._write("qoe", q.to_dict())

    def _qoe_sample(self, ts_ms, dl, ul, rtts, loss, jit):
        from schemas import QoESample
        missing = []
        if not rtts:
            missing.append("rtt")
        flows = self._flow_win.get("n_active") if self._flow_win else None
        if flows is None:
            missing.append("active_flows")
        return QoESample(
            ts_ms=ts_ms, wan_if=self.wan_if, dl_mbps=dl, ul_mbps=ul,
            rtt_ms_p50=_q(rtts, 0.50), rtt_ms_p95=_q(rtts, 0.95),
            jitter_ms_p95=_q(jit, 0.95), loss_rate=loss,
            active_flows=flows, tcp_retrans_rate=None, stall_ratio=None,
            queue_delay_ms=None, missing=missing + ["tcp_retrans_rate", "stall_ratio",
                                                    "queue_delay_ms"],
            source="netdev+icmp+conntrack")

    def sample_flows(self, now: float) -> None:
        path = self.qc.get("conntrack_path", "/proc/net/nf_conntrack")
        n, lines = qoe_probe.count_conntrack(path)
        if n is None:
            return
        self._flow_win = self.flow_tracker.update(lines, now)

    # ------------------------------------------------------------------
    # 决策
    # ------------------------------------------------------------------
    def decide(self, now: float) -> None:
        now_ms = int(self.wall() * 1000)
        if self._flow_win is not None:
            pred = self.classifier.predict(self._flow_win, now_ms)
            self.aggregator.set_prediction(pred.cls, pred.confidence)
            self._last_pred = pred
        fw = self.aggregator.build(now_ms, self.feature_window_s)
        cls = fw.traffic_class
        q = scorer.qoe_score(fw.features, cls, self.tp.get("weights", {}))
        self._qoe_score = q.get("score")
        self._qoe_bits = q
        self._write("features", dict(fw.to_dict(), qoe=q))

        cands = self._candidates(now)
        best = cands[0] if cands else None
        serving_q, _, _ = scorer.radio_quality(
            fw.features.get("rsrp_mean"), fw.features.get("rsrq_mean"),
            fw.features.get("sinr_mean"))
        obs = {
            "qoe_score": self._qoe_score,
            "serving_score": None if serving_q is None else round(100 * serving_q, 1),
            "candidates": cands,
            "best_candidate": best,
            "traffic_class": cls,
            "traffic_confidence": fw.traffic_confidence,
            "model_version": getattr(self.classifier, "model_version", "none"),
            "modem_ok": self.at_fail_streak < AT_FAIL_DEGRADE,
            "serving_cell": dict(fw.cell),
        }
        decisions = self.machine.step(now, obs)
        for d in decisions:
            self.decisions_total += 1
            self._write("decision", d.to_dict())
            self._after_decision(d, now)
        self._post_verify(now)

    def _candidates(self, now: float) -> list:
        """邻区候选评分（每 neighbour_interval_s 重算，其余复用缓存）。"""
        if now - self._cand_at < self.neighbour_s and self._cand_cache:
            return self._cand_cache
        self._cand_at = now
        s = self._radio
        if s is None:
            return self._cand_cache
        from ai_net.features.history import CellHistory
        out: list = []
        for n in s.neighbours:
            if n.is_serving or n.rsrp_dbm is None:
                continue
            probe = self.history.probe(CellHistory.neighbour_key(n))
            sc = scorer.candidate_score(n, self._last_class(), probe)
            if sc.get("score") is None:
                continue
            out.append({"key": n.key(), "cell_id": n.cid, "rat": n.rat, "arfcn": n.arfcn,
                        "pci": n.pci, "rsrp_dbm": n.rsrp_dbm, "score": sc["score"],
                        "source": sc["source"], "reasons": sc["reasons"],
                        "baseline_rsrp_only": scorer.rsrp_only_baseline(n)})
        out.sort(key=lambda c: c["score"], reverse=True)
        self._cand_cache = out
        return out

    def _last_class(self) -> str:
        return self._last_pred.cls if self._last_pred is not None else "generic"

    def _after_decision(self, d, now: float) -> None:
        if d.action.get("type") == "lock_cell" and self.mode == "execute":
            self._pending_lock_expect = dict(d.action.get("params") or {})
        if self.mode == "execute" and getattr(self.executor, "failures", 0) >= \
                int(self.rules_cfg.get("safety", {}).get(
                    "max_consecutive_action_failures", 2)):
            self.event("auto_degrade", mode_from="execute", mode_to="shadow",
                       reason="executor_failures=%d" % getattr(self.executor, "failures", 0))
            self.mode = "shadow"
            self.machine.set_mode("shadow")
            self.executor = self.shadow
            self.machine.executor = self.shadow

    def _post_verify(self, now: float) -> None:
        """VERIFY → COOLDOWN 转变时按 verify_delay_s 核验服务小区（execute 模式）。"""
        prev, self._prev_machine_state = self._prev_machine_state, self.machine.state
        if prev != "VERIFY" or self.machine.state != "COOLDOWN":
            return
        expect = self._pending_lock_expect
        self._pending_lock_expect = None
        if not expect or self.mode != "execute" or not hasattr(self.executor, "verify_lock"):
            return
        import threading

        def _run():
            self.sleeper(float(self.rules_cfg.get("safety", {})
                               .get("lock_verification", {}).get("verify_delay_s", 20)))
            res = self.executor.verify_lock(expect)
            self.event("lock_verify", expect=expect, result=res)

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(self) -> int:
        try:
            self.setup()
        except Exception as e:                       # 启动失败也要留证据
            self.event("fatal", where="setup", error=repr(e))
            return 2
        api_cfg = self.cfg.get("api", {}) or {}
        if api_cfg.get("enabled", True):
            from ai_net.api import StatusAPI
            self.api = StatusAPI(self, api_cfg.get("host", "127.0.0.1"),
                                 int(api_cfg.get("port", 8787)))
            if not self.api.start():
                self.event("api_unavailable")
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):            # 非主线程/无权限时跳过
                pass

        qc = self.qc
        flow_s = float(qc.get("conntrack_interval_s", 2))
        nexts = {"radio": 0.0, "reg": 0.0, "ecsq": 0.0, "qoe": 0.0, "flow": 0.0,
                 "decide": 0.0, "state": 0.0}
        self.event("run_loop_start")
        while not self._stop:
            now = self.clock()
            try:
                if now >= nexts["radio"]:
                    nexts["radio"] = now + self.sample_s
                    self.sample_radio(now)
                if now >= nexts["reg"]:
                    nexts["reg"] = now + self.reg_s
                    self.sample_reg(now)
                if now >= nexts["ecsq"]:
                    nexts["ecsq"] = now + self.ecsq_s
                    self.sample_ecsq(now)
                if now >= nexts["qoe"]:
                    nexts["qoe"] = now + float(qc.get("probe_interval_s", 1))
                    self.sample_qoe(now)
                if now >= nexts["flow"]:
                    nexts["flow"] = now + flow_s
                    self.sample_flows(now)
                if now >= nexts["decide"]:
                    nexts["decide"] = now + self.decision_s
                    self.decide(now)
                if now >= nexts["state"]:
                    nexts["state"] = now + STATE_EVERY_S
                    self.write_state()
            except Exception as e:                   # 采集异常不杀服务
                self.event("loop_error", error=repr(e))
            if self._stop:
                break
            # AT 密集时不留空转：睡到最近的下一个任务
            delta = min(nexts.values()) - self.clock()
            self.sleeper(max(0.05, min(1.0, delta)))
        self.shutdown()
        return 0

    def _on_signal(self, signum, frame):
        self._stop = True

    def shutdown(self) -> None:
        try:
            self.write_state()
            self.history.save(os.path.join(self.data_dir, "cell_history.json"))
            if self.api:
                self.api.stop()
            for w in self.writers.values():
                w.close()
            if self.raw_at:
                self.raw_at.close()
            self.event("service_stop", decisions=self.decisions_total)
            try:
                os.unlink(self.pid_file)
            except OSError:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _write(self, channel: str, obj: dict) -> None:
        w = self.writers.get(channel)
        if w is not None:
            w.write(obj)

    def event(self, kind: str, **kw) -> None:
        w = self.writers.get("events")
        rec = {"ts": round(self.wall(), 3), "kind": kind}
        rec.update(kw)
        if w is not None:
            w.write(rec)

    def uptime_s(self) -> float:
        return 0.0 if self.started_at is None else self.clock() - self.started_at

    def snapshot(self) -> dict:
        s = self._radio
        lw = self._flow_win or {}
        fw = self.aggregator.build(int(self.wall() * 1000), self.feature_window_s)
        return {
            "ts": round(self.wall(), 3),
            "mode": self.mode,
            "executor_level": self.machine.executor_level,
            "machine_state": self.machine.state,
            "uptime_s": round(self.uptime_s(), 1),
            "at": {"fail_streak": self.at_fail_streak,
                   "backends": [b.name for b in getattr(self.transport, "backends", [])],
                   "active": getattr(getattr(self.transport, "active", None), "name", None)},
            "radio": s.to_dict() if s else None,
            "qoe": {"score": self._qoe_score, "bits": self._qoe_bits,
                    "wan_if": self.wan_if},
            "flows": {k: lw.get(k) for k in ("n_active", "ul_mbps", "dl_mbps", "ul_ratio",
                                             "udp_ratio", "small_pkt_ratio")},
            "traffic": {"class": fw.traffic_class,
                        "confidence": fw.traffic_confidence,
                        "model": getattr(self.classifier, "model_version", "none")},
            "features_missing": fw.missing,
            "candidates": self._cand_cache[:5],
            "decisions_total": self.decisions_total,
            "model_registry_errors": self.registry.errors,
        }

    def write_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_file) or ".", exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.state_file)
        except OSError:
            pass


def _q(vals: list, q: float):
    from ai_net.features import transforms as T
    return T.quantile_nr(vals, q)


def load_service(config_dir: str, **kw) -> AiNetService:
    return AiNetService(cfgmod.load_config_dir(config_dir), **kw)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ai-net-manager service")
    ap.add_argument("--config", default="config")
    ap.add_argument("--mode", default=None, help="覆盖 service.mode（shadow/recommend/execute）")
    ap.add_argument("--once", action="store_true", help="仅启动 + 单次决策后退出（自检）")
    args = ap.parse_args(argv)
    cfg = cfgmod.load_config_dir(args.config)
    if args.mode:
        cfg.setdefault("service", {})["mode"] = args.mode
    svc = AiNetService(cfg)
    if args.once:
        svc.setup()
        now = svc.clock()
        svc.sample_radio(now)
        svc.sample_qoe(now)
        svc.sample_flows(now)
        svc.decide(now)
        svc.write_state()
        svc.shutdown()
        print(json.dumps(svc.snapshot(), ensure_ascii=False, indent=2))
        return 0
    return svc.run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
