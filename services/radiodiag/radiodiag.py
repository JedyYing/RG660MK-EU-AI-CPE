#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 5G 网络实时诊断与小区自愈（radiodiag）
=================================================
诊断面（全部为设备原生能力，实测可用 2026-09-29）：
  - RSRP / RAT          : mipc_wan_cli --nw_get_signal / --nw_get_rat     (0.01s)
  - 注册状态 / 射频状态 : --check_nw_status / --nw_radio_state_get
  - 服务小区 TAC+NCI    : AT+C5GREG?（小区变更可实锤对比）
  - 拥塞 / 链路质量     : ping RTT 均值·抖幅·丢包（对 223.5.5.5）
自愈面（vendor 官方动作，LuCI 同款）：
  - 射频软循环          : mipc_wan_cli --nw_radio_state_set 0 → 1  → 触发重新选网
  - 验证               : 重注册 + WAN 恢复 + 对比 RSRP/NCI（可判定"切到了更好的小区"）

用法：
  radiodiag.py status                      # 一次快照 JSON（快，~0.3s）
  radiodiag.py check [--net]               # 诊断结论（--net 含 ping 探测）
  radiodiag.py heal [--force] [--dry]      # 自愈：射频循环→重选→前后对比（--dry 只演练不动射频）
  radiodiag.py watch [--interval 60] [--apply] [--net]
                                           # 常驻监视：写状态文件；--apply 时在诊断异常后自动 heal
  radiodiag.py history [N]                 # 最近 N 条事件（默认 20）

阈值（可用环境变量覆盖）：RSRP_SEVERE=-105, RSRP_WEAK=-95, LOSS_PCT=10, RTT_MS=200, JITTER_MS=100
"""
import json, os, re, subprocess, sys, time

MIPC = "mipc_wan_cli"
QL = "ql_datacall"
STATE_FILE = os.environ.get("RADIODIAG_STATE", "/data/ai_cpe/radiodiag_state.json")
LOG_FILE = os.environ.get("RADIODIAG_LOG", "/data/ai_cpe/radiodiag_log.jsonl")
PING_TARGET = os.environ.get("RADIODIAG_PING", "223.5.5.5")

RSRP_SEVERE = int(os.environ.get("RSRP_SEVERE", "-105"))
RSRP_WEAK = int(os.environ.get("RSRP_WEAK", "-95"))
LOSS_PCT = int(os.environ.get("LOSS_PCT", "10"))
RTT_MS = int(os.environ.get("RTT_MS", "200"))
JITTER_MS = int(os.environ.get("JITTER_MS", "100"))
HEAL_COOLDOWN = int(os.environ.get("HEAL_COOLDOWN", "180"))


def sh(cmd, t=10):
    try:
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=t)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:
        return 127, str(e)


def clean(out):
    return "\n".join(ln for ln in out.splitlines() if "libtrm" not in ln).strip()


def read_signal():
    rc, o = sh("%s --nw_get_signal" % MIPC, 8)
    m = re.search(r"RAT\s+([0-9A-Za-z+/]+)\s*,\s*RSRP=(-?\d+)", o)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def read_rat():
    rc, o = sh("%s --nw_get_rat" % MIPC, 8)
    m = re.search(r"Current:\s*(\S+)", o)
    return m.group(1) if m else None


def read_reg():
    rc, o = sh("%s --check_nw_status" % MIPC, 8)
    m = re.search(r"MIPC_NW_REGISTER_STATE_([A-Z]+)", o)   # [A-Z]+ 防尾部噪声(如 libtrm_init)粘连
    return m.group(1) if m else ("RAW:" + clean(o)[:40] if o else None)


def read_radio():
    rc, o = sh("%s --nw_radio_state_get" % MIPC, 8)
    m = re.search(r"MIPC_NW_RADIO_STATE_([A-Z]+)", o)
    return m.group(1) if m else None


def read_cell():
    # C5GREG 需先设为模式 2（启用位置信息）；=2 与 ? 的响应都可能携带 TAC/NCI（TAC 为 1~8 位十六进制）
    rc, o1 = sh("%s --at_cmd 'AT+C5GREG=2'" % MIPC, 12)
    rc, o2 = sh("%s --at_cmd 'AT+C5GREG?'" % MIPC, 12)
    for o in (o2, o1):
        m = re.search(r'\+C5GREG:\s*\d+,\d+,"([0-9A-Fa-f]{1,8})","([0-9A-Fa-f]+)"', o)
        if not m:
            m = re.search(r'\+C5GREG:\s*\d+,"([0-9A-Fa-f]{1,8})","([0-9A-Fa-f]+)"', o)
        if m:
            return {"tac": m.group(1).upper(), "nci": m.group(2).upper()}
    return {}


def read_wan():
    rc, o = sh("ifstatus wan 2>/dev/null", 8)
    up = '"up": true' in o
    m = re.search(r'"address":\s*"([0-9.]+)"', o)
    return up, (m.group(1) if m else None)


def read_temp():
    try:
        v = int(open("/sys/class/thermal/thermal_zone0/temp").read().strip())
        return round(v / 1000.0, 1)
    except Exception:
        return None


def probe_net(count=5):
    rc, o = sh("ping -c %d -W 2 %s 2>&1" % (count, PING_TARGET), count * 3 + 5)
    loss = None; avg = mx = mdev = None
    m = re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", o)
    if m:
        loss = float(m.group(1))
    m = re.search(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)", o)
    if m:
        avg, mx, mdev = float(m.group(2)), float(m.group(3)), float(m.group(4))
    return {"loss_pct": loss, "rtt_avg_ms": avg, "rtt_max_ms": mx, "jitter_ms": mdev}


def snapshot():
    rat, rsrp = read_signal()
    s = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "ts_epoch": round(time.time(), 1),
         "rat": rat, "rsrp": rsrp, "rat_mode": read_rat(), "reg": read_reg(),
         "radio": read_radio(), "cell": read_cell(), "temp_c": read_temp()}
    up, ip = read_wan()
    s["wan_up"] = up; s["wan_ip"] = ip
    return s


def diagnose(snap, net=None):
    """返回 verdict: ok / weak / severe / congested / down + 说明列表"""
    notes = []
    v = "ok"
    if snap.get("radio") and snap["radio"] != "ON":
        return "down", ["射频未开启（radio=%s）" % snap["radio"]]
    if not snap.get("reg") or "HOME" not in str(snap.get("reg")) and "ROAMING" not in str(snap.get("reg")):
        v = "down"; notes.append("未注册：%s" % snap.get("reg"))
    rsrp = snap.get("rsrp")
    if rsrp is not None:
        if rsrp < RSRP_SEVERE:
            v = "severe"; notes.append("RSRP %d dBm 严重弱信号（<%d）" % (rsrp, RSRP_SEVERE))
        elif rsrp < RSRP_WEAK:
            v = "weak" if v == "ok" else v; notes.append("RSRP %d dBm 弱信号（<%d）" % (rsrp, RSRP_WEAK))
        else:
            notes.append("RSRP %d dBm 正常" % rsrp)
    if net:
        if net.get("loss_pct") is not None and net["loss_pct"] >= LOSS_PCT:
            if v == "ok": v = "congested"
            notes.append("丢包 %.0f%%（≥%d%%）" % (net["loss_pct"], LOSS_PCT))
        if net.get("rtt_avg_ms") is not None and net["rtt_avg_ms"] >= RTT_MS:
            if v == "ok": v = "congested"
            notes.append("RTT %.0f ms 偏高（≥%d）" % (net["rtt_avg_ms"], RTT_MS))
        if net.get("jitter_ms") is not None and net["jitter_ms"] >= JITTER_MS:
            if v == "ok": v = "congested"
            notes.append("抖动 %.0f ms 偏高（≥%d）" % (net["jitter_ms"], JITTER_MS))
        if net.get("rtt_avg_ms") is not None and v == "ok":
            notes.append("RTT %.0f ms、抖动 %.0f ms、丢包 %s%% 正常" % (
                net["rtt_avg_ms"], net.get("jitter_ms") or 0, net.get("loss_pct")))
    return v, notes


def wait_ready(timeout=90, quiet=True):
    """等待注册 + WAN 恢复"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        reg = read_reg() or ""
        up, ip = read_wan()
        if ("HOME" in reg or "ROAMING" in reg) and up and ip:
            return True, int(time.time() - t0)
        time.sleep(3)
    return False, int(time.time() - t0)


def radio_cycle():
    """射频软循环：off → 5s → on（vendor LuCI 同款动作）"""
    rc1, _ = sh("%s --nw_radio_state_set 0" % MIPC, 15)
    time.sleep(5)
    rc2, _ = sh("%s --nw_radio_state_set 1" % MIPC, 15)
    return rc1, rc2


def log_event(ev):
    ev.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(ev, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def cmd_status(args):
    s = snapshot()
    v, notes = diagnose(s)
    s["verdict"] = v; s["notes"] = notes
    print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


def cmd_check(args):
    s = snapshot()
    net = probe_net(5)
    v, notes = diagnose(s, net)
    out = dict(s); out["net"] = net; out["verdict"] = v; out["notes"] = notes
    log_event({"type": "check", **out})
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print()
    print("诊断结论: [%s] %s" % (v, "；".join(notes)))
    return 0


def cmd_heal(args):
    force = "--force" in args
    dry = "--dry" in args
    s0 = snapshot()
    net0 = probe_net(5)
    v0, n0 = diagnose(s0, net0)
    print("== 自愈前 ==")
    print(json.dumps({"rsrp": s0["rsrp"], "rat": s0["rat"], "cell": s0["cell"],
                      "reg": s0["reg"], "wan": s0["wan_ip"], "net": net0, "verdict": v0},
                     ensure_ascii=False))
    if v0 == "ok" and not force:
        print("\n[跳过] 当前健康（verdict=ok）；如需强制演练换网请加 --force")
        return 0
    ev = {"type": "heal", "trigger": ("force" if (v0 == "ok" and force) else v0),
          "before": s0, "net_before": net0}
    if dry:
        print("\n[dry-run] 将执行：射频 0 → 5s → 1 → 等待重注册/恢复 → 前后对比（本次未动作）")
        ev["result"] = "dry-run"; log_event(ev); return 0
    print("\n→ 执行射频软循环（off → 5s → on），WAN 将短暂中断…")
    rc1, rc2 = radio_cycle()
    ok, waited = wait_ready(90)
    time.sleep(6)                                  # 稳定期
    s1 = snapshot()
    net1 = probe_net(5)
    v1, n1 = diagnose(s1, net1)
    nci0 = (s0.get("cell") or {}).get("nci"); nci1 = (s1.get("cell") or {}).get("nci")
    changed = bool(nci0 and nci1 and nci0 != nci1)
    improved = (s0.get("rsrp") is not None and s1.get("rsrp") is not None
                and s1["rsrp"] >= s0["rsrp"] + 3)
    success = ok and (changed or improved or v1 == "ok")
    ev.update({"after": s1, "net_after": net1, "verdict_after": v1,
               "rc_off": rc1, "rc_on": rc2, "ready": ok, "waited_s": waited,
               "cell_changed": changed, "rsrp_delta": (s1.get("rsrp") - s0.get("rsrp")
                                                       if (s1.get("rsrp") is not None and s0.get("rsrp") is not None) else None),
               "success": success})
    log_event(ev)
    print("\n== 自愈后 ==")
    print(json.dumps({"rsrp": s1["rsrp"], "rat": s1["rat"], "cell": s1["cell"],
                      "reg": s1["reg"], "wan": s1["wan_ip"], "net": net1, "verdict": v1},
                     ensure_ascii=False))
    print()
    print("结果: %s｜小区 %s｜RSRP %s→%s (Δ%s)｜重注册耗时 %ss" % (
        "✅ 已切换到更优小区/恢复" if success else "⚠️ 未达成恢复",
        ("%s → %s" % (nci0, nci1)) if changed else ("未变化(%s)" % (nci1 or "?")),
        s0.get("rsrp"), s1.get("rsrp"), ev["rsrp_delta"], waited))
    return 0 if success else 1


def cmd_watch(args):
    interval = 60
    if "--interval" in args:
        interval = int(args[args.index("--interval") + 1])
    apply_ = "--apply" in args
    net_each = "--net" in args
    last_heal = 0.0
    print("[watch] 启动：间隔 %ds，自愈=%s（Ctrl+C 退出）" % (interval, "开" if apply_ else "仅观察"))
    while True:
        s = snapshot()
        net = probe_net(3) if net_each else None
        v, notes = diagnose(s, net)
        rec = {"type": "watch", "verdict": v, "rsrp": s.get("rsrp"), "cell": s.get("cell"),
               "reg": s.get("reg"), "notes": notes, "net": net}
        if v != "ok" and apply_ and (time.time() - last_heal) > HEAL_COOLDOWN:
            print("[watch] 诊断异常(%s) → 触发自愈" % v)
            cmd_heal(["--force"])
            last_heal = time.time()
            rec["action"] = "heal"
        log_event(rec)
        print("[%s] verdict=%s rsrp=%s cell=%s reg=%s" % (
            time.strftime("%H:%M:%S"), v, s.get("rsrp"),
            (s.get("cell") or {}).get("nci", "?"), s.get("reg")))
        time.sleep(interval)


def cmd_history(args):
    n = int(args[0]) if args and args[0].isdigit() else 20
    try:
        lines = open(LOG_FILE, encoding="utf-8").read().strip().splitlines()[-n:]
    except OSError:
        print("(暂无日志)"); return 0
    for ln in lines:
        print(ln)
    return 0


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    rest = args[1:]
    if cmd == "status":
        return cmd_status(rest)
    if cmd == "check":
        return cmd_check(rest)
    if cmd == "heal":
        return cmd_heal(rest)
    if cmd == "watch":
        return cmd_watch(rest)
    if cmd == "history":
        return cmd_history(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
