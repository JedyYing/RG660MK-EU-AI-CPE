"""服务编排集成测试（离线，FakeTransport）：shadow 只读 + 决策链路 + 落盘。"""
import json
import os

import pytest

from ai_net.collectors import qoe_probe
from ai_net.collectors.at_transport import ATResponse
from ai_net.service import AiNetService

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "ecellmeas_20261008.txt")


def fixture_lines():
    with open(FIXTURE, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f
                if ln.startswith("+ECELLMEAS")]


class FakeTransport:
    """只应答读命令；记录所有下发的命令。"""

    def __init__(self, ecellmeas=None, c5greg=None, fail=False):
        self.cmds = []
        self.fail = fail
        self.backends = [type("B", (), {"name": "fake"})()]
        self.active = self.backends[0]
        self.ecellmeas = ecellmeas if ecellmeas is not None else fixture_lines()
        self.c5greg = c5greg or [
            '+C5GREG: 2,1,"594FCB484","46011",7,11,0,0,"00","00000001",7']

    def send(self, cmd, timeout=None):
        self.cmds.append(cmd)
        r = ATResponse(cmd=cmd)
        if self.fail:
            r.error = "timeout"
            return r
        r.ok = True
        r.backend = "fake"
        if cmd.startswith("AT+ECELLMEAS"):
            r.lines = self.ecellmeas
        elif cmd.startswith("AT+C5GREG"):
            r.lines = self.c5greg
        elif cmd.startswith("AT+ECSQ"):
            r.ok, r.error = False, "CME ERROR: 4"
        return r

    def probe_all(self):
        return []


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_service(tmp_path, transport=None, **cfg_over):
    cfg = {
        "service": {"mode": "shadow", "sample_interval_s": 1, "decision_interval_s": 2,
                    "feature_window_s": 10, "data_dir": str(tmp_path / "data"),
                    "log_dir": str(tmp_path / "log"),
                    "state_file": str(tmp_path / "state.json"),
                    "pid_file": str(tmp_path / "pid")},
        "modem": {"backends": ["fake"], "save_raw": True,
                  "raw_dir": str(tmp_path / "data" / "raw_at"),
                  "reg_interval_s": 10, "ecsq_interval_s": 30},
        "qoe": {"probe_targets": ["223.5.5.5"], "probe_interval_s": 1,
                "probe_window_s": 10, "probe_timeout_ms": 100,
                "conntrack_interval_s": 2,
                "conntrack_path": str(tmp_path / "no_conntrack")},
        "storage": {"jsonl_rotate_mb": 1},
        "traffic_profiles": {"weights": {"generic": {"radio": 0.2, "throughput": 0.3,
                                                     "latency": 0.3, "stability": 0.2}},
                             "unknown_threshold": 0.6},
        "rules": {"selection": {"min_dwell_time_s": 60, "switch_cooldown_s": 120,
                                "min_score_gain": 12, "min_gain_hold_s": 10,
                                "min_candidate_rsrp": -115, "max_switches_10min": 3,
                                "rollback_window_s": 30, "rollback_score_drop": 15,
                                "bad_qoe_hold_s": 5},
                  "profiles": {"generic": {"min_score_gain": 12, "min_gain_hold_s": 10}},
                  "safety": {"executor_enabled": False,
                             "max_consecutive_action_failures": 2}},
        "logging": {"jsonl": ["radio", "qoe", "features", "decision"]},
    }
    for k, v in cfg_over.items():
        cfg.setdefault(k, {}).update(v)
    clk = Clock()
    svc = AiNetService(cfg, transport=transport or FakeTransport(), clock=clk)
    return svc, clk


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_shadow_smoke_readonly_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(qoe_probe, "icmp_ping", lambda *a, **k: 25.0)
    tp = FakeTransport()
    svc, clk = make_service(tmp_path, tp)
    svc.setup()
    svc.sample_radio(clk())
    svc.sample_qoe(clk())
    svc.sample_flows(clk())
    svc.decide(clk())
    svc.write_state()

    # 只读保证：所有下发命令都是查询形态
    assert tp.cmds and all(c.endswith("?") for c in tp.cmds), tp.cmds

    radio = read_jsonl(tmp_path / "log" / "radio.jsonl")
    assert len(radio) == 1
    r0 = radio[0]
    assert r0["source"].startswith("ecellmeas|serve:")     # NCI 或 first_row 匹配
    assert r0["cell_id"] and r0["rsrp_dbm"] is not None
    assert "ca_cc_count" in r0["missing"]                  # 显式缺失不填 0
    n_plmn = [n for n in r0["neighbours"] if n["pci"] == 784 and
              len(n["plmn"] or []) == 2]
    assert n_plmn, "同一小区多 PLMN 行应合并"

    qoe = read_jsonl(tmp_path / "log" / "qoe.jsonl")
    assert qoe and qoe[0]["rtt_ms_p50"] == 25.0
    assert "stall_ratio" in qoe[0]["missing"]

    feats = read_jsonl(tmp_path / "log" / "features.jsonl")
    assert feats and feats[0]["features"]["rtt_p50"] == 25.0
    assert feats[0]["qoe"]["score"] is not None             # 有可用分量即可评分

    st = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert st["mode"] == "shadow" and st["executor_level"] == "L0"
    assert st["machine_state"] in ("NORMAL", "EVALUATE_CANDIDATES")
    svc.shutdown()


def test_shadow_never_writes_modem_on_bad_qoe(tmp_path, monkeypatch):
    """持续坏 QoE → 触发评估 → 决策记录动作但绝不写 Modem（L0）。"""
    monkeypatch.setattr(qoe_probe, "icmp_ping", lambda *a, **k: 180.0)   # 高时延
    weak = ['+ECELLMEAS: 11,426030,784,-1130,-180,-50,"594FCB484",1,"46011","CHN-CT",1,38880',
            '+ECELLMEAS: 11,426030,493,-900,-120,50,"5959A0401",1,"46011","CHN-CT",1,38880']
    tp = FakeTransport(ecellmeas=weak)
    svc, clk = make_service(tmp_path, tp)
    svc.setup()
    for _ in range(12):                     # 覆盖 bad_qoe_hold_s=5 + 驻留/冷却判定
        svc.sample_radio(clk())
        svc.sample_qoe(clk())
        svc.sample_flows(clk())
        svc.decide(clk())
        clk.advance(1.0)

    dec = read_jsonl(tmp_path / "log" / "decision.jsonl")
    assert dec, "坏 QoE 持续后应产出决策记录"
    assert all(d["executor_level"] == "L0" for d in dec)
    assert not any(c.startswith("AT+EMMCHLCK=") for c in tp.cmds), tp.cmds
    kinds = {d["action"]["type"] for d in dec}
    assert kinds <= {"lock_cell", "unlock_cell", "noop"}
    # 决策里如有 lock_cell，则必须标注未执行（shadow）
    for d in dec:
        if d["action"]["type"] == "lock_cell":
            assert "not executed" in (d["action"].get("result") or {}).get("detail", "")
    events = read_jsonl(tmp_path / "log" / "events.jsonl")
    assert any(e["kind"] == "service_start" for e in events)
    svc.shutdown()


def test_at_fail_streak_degrades_modem_ok(tmp_path):
    tp = FakeTransport(fail=True)
    svc, clk = make_service(tmp_path, tp)
    svc.setup()
    for _ in range(3):
        svc.sample_radio(clk())
        clk.advance(1.0)
    assert svc.at_fail_streak >= 3
    snap = svc.snapshot()
    assert snap["at"]["fail_streak"] >= 3
    events = read_jsonl(tmp_path / "log" / "events.jsonl")
    assert any(e["kind"] == "modem_error" for e in events)
    # decide 不因 modem 故障抛异常，且在 guard 前被 modem_ok 拦截
    svc.decide(clk())
    svc.shutdown()


def test_execute_mode_gated_off_by_safety_switch(tmp_path):
    """mode=execute 但 executor_enabled=false → 启动即降级 shadow 并记事件。"""
    svc, clk = make_service(tmp_path, service={"mode": "execute"})
    svc.setup()
    assert svc.mode == "shadow"
    assert svc.machine.executor_level == "L0"
    events = read_jsonl(tmp_path / "log" / "events.jsonl")
    assert any(e["kind"] == "auto_degrade" and e["reason"] == "executor_enabled=false"
               for e in events)
    svc.shutdown()
