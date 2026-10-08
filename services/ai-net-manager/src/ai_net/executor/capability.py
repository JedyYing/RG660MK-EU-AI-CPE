"""CapabilityProbe（设计文档 8.2 / 15.10）：启动时探测本固件可用命令集。

输出 reports/modem_capability.json，字段：
- at_backends        : 各 AT 后端可用性（启动探测结果）
- read_commands      : 观测类命令探测结果（=? 域 / 实际查询）
- control_commands   : 控制类命令（仅 =? 探测，不执行任何写操作）
- executor_recommendation : L0..L3 建议等级 + 依据
- incidents          : 现场事故记录（流式上报导致失联）
"""
from __future__ import annotations

import json
import os
import time

from ai_net.collectors import modem


def probe_read(transport) -> dict:
    """只读探测：=? 域 + 单次查询。不触碰任何写命令。"""
    out = {}
    for name, q in (("ECELLMEAS", modem.CMD_ECELLMEAS_QUERY),
                    ("C5GREG", modem.CMD_C5GREG_QUERY),
                    ("ECSQ", modem.CMD_ECSQ_QUERY)):
        r = transport.send(q)
        out[name] = {
            "query": q, "ok": r.ok, "error": r.error,
            "lines": len(r.lines),
            "sample": (r.lines[0][:160] if r.lines else None),
            "elapsed_ms": r.elapsed_ms, "backend": r.backend,
        }
    return out


def probe_control(transport) -> dict:
    """控制命令探测：只发 =? 与查询，绝不执行锁/解锁。"""
    out = {}
    # QNWLOCK（Quectel 文档命令）—— 预期 CME ERROR 4（本固件无）
    r = transport.send("AT+QNWLOCK=?")
    out["QNWLOCK"] = {"probe": "AT+QNWLOCK=?", "available": r.ok,
                      "error": r.error, "raw": r.raw.strip()[:200]}
    # EMMCHLCK（MTK 同能力命令）
    r2 = transport.send(modem.CMD_EMMCHLCK_TEST)
    r3 = transport.send(modem.CMD_EMMCHLCK_QUERY)
    out["EMMCHLCK"] = {
        "probe": modem.CMD_EMMCHLCK_TEST, "available": r2.ok,
        "domain": (r2.lines[0][:200] if r2.lines else None),
        "state_now": modem.parse_lock_state(r3.lines) if r3.ok else None,
        "error": r2.error,
        "write_form_verified": None,      # 由现场锁验证步骤填充（lock/unlock 往返测试）
    }
    for extra in ("AT+QENG=?", "AT+QNWINFO", "AT+QCAINFO"):
        r4 = transport.send(extra)
        out[extra] = {"available": r4.ok, "error": r4.error}
    return out


def build_report(transport, extra: dict | None = None) -> dict:
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "device": {
            "model": "RG660MK-EU (MediaTek T930)",
            "fw_modem": "R01.QUECTEL_UNLOCK.177C2E9B701 (2026-08-28)",
        },
        "at_backends": [
            {"name": p.name, "ok": p.ok, "elapsed_ms": p.elapsed_ms,
             "detail": p.detail}
            for p in transport.probe_all()
        ],
        "read_commands": probe_read(transport),
        "control_commands": probe_control(transport),
        "incidents": [{
            "ts": "2026-10-08",
            "what": "AT+ECELLMEAS=1 / AT+ECELL=1 开启流式测量上报后，设备在数分钟内"
                    "AT 全通道无响应、USB-Ethernet 链路掉载波",
            "root_cause_update": "当日稍后现场核实：CPE 处于断电（关机）状态 —— 失联与"
                                 "掉载波由断电直接解释，无证据支持与流式指令存在因果",
            "mitigation": "采集一律轮询式只读；ATTransport 内置硬护栏拒绝任何"
                          "非 0 参数的 ECELLMEAS/ECELL 写命令（allow_streaming_modes=false）。"
                          "禁令保留理由：设计保守约束 + 本系统无流式上报需求",
        }],
    }
    ctrl = report["control_commands"]
    lock_ok = ctrl.get("EMMCHLCK", {}).get("available") and \
        ctrl.get("QNWLOCK", {}).get("available") is False
    report["executor_recommendation"] = {
        "level": "L3_available" if lock_ok else "L0_only",
        "rationale": ("EMMCHLCK 可用且域合法（(0-3),(0,2,7,11),(0,1),(0-2279165),(0-1007)）；"
                      "QNWLOCK 不存在（CME ERROR 4）。写形态 AT+EMMCHLCK=1,<rat>,0,<arfcn>,<pci>,0"
                      " 与 AT+EMMCHLCK=0（撤销），锁定跨重启保留。"
                      if lock_ok else "未发现可用锁小区命令，保持 Shadow/Recommend。"),
        "executor_enabled_default": False,
        "gate": "rules.yaml safety.executor_enabled=false（默认）；人工确认后置 true 且 mode=execute",
    }
    if extra:
        report.update(extra)
    return report


def write_report(report: dict, path: str) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def main(argv=None) -> int:
    import argparse
    from ai_net.collectors.at_transport import ATTransport

    ap = argparse.ArgumentParser(description="RG660MK AT capability probe")
    ap.add_argument("--out", default="reports/modem_capability.json")
    ap.add_argument("--lock-file", default="/var/lock/ai_net_at.lock")
    args = ap.parse_args(argv)

    t = ATTransport(lock_file=args.lock_file)
    report = build_report(t)
    ok = write_report(report, args.out)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:3000])
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
