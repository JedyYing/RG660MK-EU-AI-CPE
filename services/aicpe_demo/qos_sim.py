#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qos_sim.py —— RG660MK 遥操作「三流 QoS」仿真（设备侧、隔离复现）

在设备上用 tc 在 **隔离路径(lo)** 上复现一条「拥塞链路」，并按端口把三条业务流分类：
    · 视频流  : TCP 大包（吞吐，Mbit/s）
    · 反馈流  : 音频 UDP 20ms 周期小包（抖动 P95）
    · 控制流  : 心跳 UDP 50ms 往返（本地排队时延: RTT P95 / 中位 / 最大）
对比 QoS 关闭（单队列 drop-tail）与启用（三级 htb：控制 prio0 · 音频 prio1 · 视频 prio2 + fq_codel 小队列）。

用法：
    python3 qos_sim.py                          # 默认 6Mbit、1路、关/开都测
    python3 qos_sim.py --bw 6mbit --streams 2
    python3 qos_sim.py --qos on --dur 15
    python3 qos_sim.py --json /data/ai_cpe/hermes/home/qos_sim.json --chart /data/.../qos.png
    python3 qos_sim.py --help

一拖多判据（可调）：
    两路视频各自 ≥ --min-video Mbit/s 且 控制流 RTT 中位数 ≤ --max-ctrl-median ms → 记为「支持 2 组」

安全设计：
    * 只在 lo 上整形（隔离路径），**不触碰 5G(ccmni*)/br-lan/eth** 等生产接口；
    * 运行前记录 lo 的 MTU，临时降为 1500（tbf/htb 在 65536 MTU 下无法正确整形），
      结束后无论成败都在 finally 中恢复 MTU 并清除 qdisc；
    * 退出前打印复原校验结果。
"""
import argparse, json, os, socket, statistics, subprocess, sys, threading, time

TC = "/sbin/tc"; IP = "/sbin/ip"
H = "127.0.0.1"
PORTS = {"video": [5901, 5911], "audio": 5902, "ctrl": 5903}


def sh(cmd, t=30):
    try:
        r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=t)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return "<err %s>" % e


def lo_mtu():
    out = sh("%s -o link show lo" % IP)
    for tok in out.split():
        if tok.startswith("mtu"):
            return tok.split("mtu")[1] if "mtu" in tok else None
    return None


def clear_tc():
    sh("%s qdisc del dev lo root 2>/dev/null; true" % TC)


def setup_qos(on, bw, cfg):
    """on=False: 单队列 drop-tail（无 QoS）；on=True: 三级 htb"""
    clear_tc()
    if not on:
        sh("%s qdisc add dev lo root handle 1: tbf rate %s burst 15k latency %s" % (TC, bw, cfg["buf"]))
        return "单队列 drop-tail（无 QoS；%s 缓冲）" % cfg["buf"]
    sh("%s qdisc add dev lo root handle 1: htb default 30" % TC)
    sh("%s class add dev lo parent 1: classid 1:1 htb rate %s ceil %s" % (TC, bw, bw))
    sh("%s class add dev lo parent 1: classid 1:10 htb rate %s ceil %s prio 0 quantum 1500" % (TC, cfg["ctrl"], bw))
    sh("%s class add dev lo parent 1: classid 1:20 htb rate %s ceil %s prio 1 quantum 1500" % (TC, cfg["audio"], bw))
    sh("%s class add dev lo parent 1: classid 1:30 htb rate %s ceil %s prio 2 quantum 1500" % (TC, cfg["video"], bw))
    sh("%s qdisc add dev lo parent 1:10 handle 110: pfifo limit 20" % TC)
    sh("%s qdisc add dev lo parent 1:20 handle 120: fq_codel limit 40 target 3ms interval 20ms" % TC)
    sh("%s qdisc add dev lo parent 1:30 handle 130: fq_codel limit 100 target 5ms interval 20ms" % TC)
    for p in PORTS["video"]:
        sh("%s filter add dev lo parent 1: protocol ip prio 1 u32 match ip dport %d 0xffff flowid 1:30" % (TC, p))
    sh("%s filter add dev lo parent 1: protocol ip prio 1 u32 match ip dport %d 0xffff flowid 1:20" % (TC, PORTS["audio"]))
    sh("%s filter add dev lo parent 1: protocol ip prio 1 u32 match ip dport %d 0xffff flowid 1:10" % (TC, PORTS["ctrl"]))
    return "三级 htb（控制 %s/prio0 · 音频 %s/prio1 · 视频 %s/prio2）" % (cfg["ctrl"], cfg["audio"], cfg["video"])


def p95(v):
    if not v:
        return None
    v = sorted(v)
    return round(v[max(0, int(round(0.95 * (len(v) - 1))))], 2)


def measure(qos_on, streams, dur, cfg, aperiod=20.0):
    sched = setup_qos(qos_on, cfg["bw"], cfg)
    stop = threading.Event(); V = {}; vb = [0] * streams

    def srv(p, idx):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((H, p)); s.listen(4); s.settimeout(0.3)
        while not stop.is_set():
            try:
                c, _ = s.accept(); c.settimeout(0.3)
                while not stop.is_set():
                    try:
                        d = c.recv(32768)
                        if not d:
                            break
                        vb[idx] += len(d)
                    except socket.timeout:
                        pass
                    except Exception:
                        break
                c.close()
            except socket.timeout:
                pass
            except Exception:
                pass
        s.close()

    def cli(p):
        t0 = time.time(); s = None
        while time.time() - t0 < 4:
            try:
                s = socket.create_connection((H, p), timeout=1); break
            except Exception:
                time.sleep(0.2)
        if s is None:
            return
        s.settimeout(2); buf = b"V" * 1460
        while not stop.is_set():
            try:
                s.sendall(buf)
            except Exception:
                break
        try:
            s.close()
        except Exception:
            pass

    def rx_u(port, kind):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind((H, port)); s.settimeout(0.3); arr = []
        while not stop.is_set():
            try:
                d, a = s.recvfrom(2048)
                if kind == "a":
                    arr.append(time.time())
                else:
                    s.sendto(d, a)
            except socket.timeout:
                pass
        s.close()
        if kind == "a" and len(arr) > 3:
            iv = [(arr[i] - arr[i - 1]) * 1000 for i in range(1, len(arr))]
            V["jit"] = [abs(x - aperiod) for x in iv]

    def tx_a():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); pkt = b"A" * 200; nxt = time.time()
        while not stop.is_set():
            s.sendto(pkt, (H, PORTS["audio"])); nxt += aperiod / 1000.0
            d = nxt - time.time()
            if d > 0:
                time.sleep(d)
        s.close()

    def tx_c():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.5)
        ok = []; loss = 0; mx = 0.0; nxt = time.time(); i = 0
        while not stop.is_set():
            i += 1; t = time.time()
            try:
                s.sendto(b"C%05d" % i, (H, PORTS["ctrl"])); s.recvfrom(512)
                r = (time.time() - t) * 1000; ok.append(r); mx = max(mx, r)
            except socket.timeout:
                loss += 1
            nxt += 0.05
            d = nxt - time.time()
            if d > 0:
                time.sleep(d)
        s.close(); V["ctrl"] = ok; V["closs"] = round(100.0 * loss / max(1, i), 1); V["cmax"] = round(mx, 1)

    th = [threading.Thread(target=srv, args=(PORTS["video"][i], i)) for i in range(streams)]
    th += [threading.Thread(target=rx_u, args=(PORTS["audio"], "a")),
           threading.Thread(target=rx_u, args=(PORTS["ctrl"], "c")),
           threading.Thread(target=tx_a), threading.Thread(target=tx_c)]
    for t in th:
        t.start()
    time.sleep(0.8); t0 = time.time()
    cs = [threading.Thread(target=cli, args=(PORTS["video"][i],)) for i in range(streams)]
    for c in cs:
        c.start()
    time.sleep(max(0.5, dur - 0.8)); stop.set()
    for t in th + cs:
        t.join(timeout=4)
    el = max(0.1, time.time() - t0)
    vid = [round(b * 8 / 1e6 / el, 3) for b in vb]
    ctrl = V.get("ctrl", []); jit = V.get("jit", [])
    return {
        "qos": "启用" if qos_on else "未启用", "调度": sched, "视频路数": streams,
        "视频吞吐Mbit/s": vid, "视频合计Mbit/s": round(sum(vid), 3),
        "音频抖动P95_ms": p95(jit), "音频抖动中位_ms": round(statistics.median(jit), 2) if jit else None,
        "控制RTT_P95_ms": p95(ctrl), "控制RTT中位_ms": round(statistics.median(ctrl), 2) if ctrl else None,
        "控制RTT最大_ms": V.get("cmax"), "控制丢包率%": V.get("closs"),
        "控制样本数": len(ctrl),
    }


def verdict(runs, args):
    """按判据给出「一拖多」结论"""
    v = {}
    for r in runs:
        ok_vid = all(x >= args.min_video for x in r["视频吞吐Mbit/s"]) if r["视频吞吐Mbit/s"] else False
        med = r["控制RTT中位_ms"] if r["控制RTT中位_ms"] is not None else 9e9
        v[r["qos"]] = {"视频达标": ok_vid, "控制中位ms": med,
                       "可用组数": r["视频路数"] if (ok_vid and med <= args.max_ctrl_median) else 1}
    return v


def main():
    ap = argparse.ArgumentParser(description="RG660MK 遥操作三流 QoS 仿真（隔离复现，不碰生产接口）")
    ap.add_argument("--bw", default="6mbit", help="拥塞链路带宽（默认 6mbit，对齐现场口径）")
    ap.add_argument("--streams", type=int, default=1, help="视频并发路数（1 或 2）")
    ap.add_argument("--dur", type=float, default=12.0, help="每场景测量时长（秒）")
    ap.add_argument("--qos", choices=["off", "on", "both"], default="both", help="测哪些场景（默认 both）")
    ap.add_argument("--ctrl", default="1500kbit", help="QoS 启用时控制流保证带宽")
    ap.add_argument("--audio", default="800kbit", help="QoS 启用时音频流保证带宽")
    ap.add_argument("--video", default="3700kbit", help="QoS 启用时视频流保证带宽")
    ap.add_argument("--buf", default="250ms", help="无 QoS 场景的单队列缓冲（演示缓冲膨胀）")
    ap.add_argument("--audio-period", type=float, default=20.0, help="音频发包周期 ms")
    ap.add_argument("--min-video", type=float, default=1.5, help="一拖多判据：每路视频最低 Mbit/s")
    ap.add_argument("--max-ctrl-median", type=float, default=5.0, help="一拖多判据：控制 RTT 中位数上限 ms")
    ap.add_argument("--json", help="结果 JSON 输出路径")
    ap.add_argument("--ext-ping", default="", help="可选：外部控制链路探测目标（如 223.5.5.5），结果并入 JSON")
    a = ap.parse_args()

    cfg = {"bw": a.bw, "ctrl": a.ctrl, "audio": a.audio, "video": a.video, "buf": a.buf}
    m0 = lo_mtu()
    res = {"ts": sh("date +%Y-%m-%dT%H:%M:%S%z"), "bw": a.bw, "streams": a.streams, "dur": a.dur, "runs": [], "external": {}}
    print("== RG660MK 三流 QoS 仿真 ==")
    print("链路: %s · 视频 %d 路 · 单场景 %ss · lo MTU %s → 1500（结束恢复）" % (a.bw, a.streams, a.dur, m0))
    try:
        sh("%s link set dev lo mtu 1500" % IP)
        todo = [False, True] if a.qos == "both" else [a.qos == "on"]
        for on in todo:
            r = measure(on, a.streams, a.dur, cfg, a.audio_period)
            res["runs"].append(r)
            print("  QoS %-3s | 视频 %s Mbit/s | 抖动P95 %s ms | 控制RTT P95 %s / 中位 %s ms | 丢包 %s%%" %
                  (r["qos"], r["视频吞吐Mbit/s"], r["音频抖动P95_ms"], r["控制RTT_P95_ms"], r["控制RTT中位_ms"], r["控制丢包率%"]))
        if a.ext_ping:
            for on in [False, True] if a.qos == "both" else [a.qos == "on"]:
                setup_qos(on, a.bw, cfg)
                out = sh("ping -c 25 -i 0.2 -W 2 %s 2>&1 | tail -2" % a.ext_ping)
                res.setdefault("external", {})["启用" if on else "未启用"] = out.replace("\n", " | ")
                print("  外部链路 QoS %-3s : %s" % ("启用" if on else "未启用", out.replace("\n", " | ")))
        res["verdict"] = verdict(res["runs"], a)
        print("  判据结论:", json.dumps(res["verdict"], ensure_ascii=False))
    finally:
        clear_tc()
        if m0:
            sh("%s link set dev lo mtu %s" % (IP, m0))
    print("复原校验: lo MTU=%s | qdisc=%s" % (lo_mtu(), sh("%s qdisc show dev lo" % TC)))
    if a.json:
        json.dump(res, open(a.json, "w"), ensure_ascii=False, indent=1)
        print("结果已写入:", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
