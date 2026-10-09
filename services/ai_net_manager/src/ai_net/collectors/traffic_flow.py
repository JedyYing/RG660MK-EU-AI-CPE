# -*- coding: utf-8 -*-
"""业务流采集（设计 §15.4）：/proc/net/nf_conntrack 解析 + 速率跟踪；不保存 payload。"""
import time

from ..util import fnum, now_ms

CONNTRACK = "/proc/net/nf_conntrack"


def parse_conntrack(path=CONNTRACK):
    """解析一行一条的 conntrack，返回 flow dict 列表（双向计数）。"""
    flows = []
    try:
        with open(path, "r") as f:
            lines = f.read().splitlines()
    except OSError:
        return flows
    for ln in lines:
        t = ln.split()
        if len(t) < 6:
            continue
        fam, proto = t[0], t[2]
        if fam not in ("ipv4", "ipv6"):
            continue
        # 状态字段（TIME_WAIT/ESTABLISHED/... 可能没有，比如 udp）
        state = ""
        for tok in t[4:7]:
            if tok and "=" not in tok and tok.replace("_", "").isalpha():
                state = tok
                break
        kvs = []
        for tok in t:
            if "=" in tok:
                k, v = tok.split("=", 1)
                kvs.append((k, v))
        # 第一组 = 原始方向；第二组 = 回复方向（同名覆盖前用列表收集）
        fwd, rev = {}, {}
        seen = set()
        cur = fwd
        for k, v in kvs:
            if k == "src" and "src" in seen:
                cur = rev
                seen = set()
            cur[k] = v
            seen.add(k)
        if "src" not in fwd or "src" not in rev:
            continue
        flows.append({
            "proto": proto,
            "state": state,
            "f_src": fwd.get("src"), "f_dst": fwd.get("dst"),
            "f_sport": fwd.get("sport"), "f_dport": fwd.get("dport"),
            "f_packets": int(fnum(fwd.get("packets")) or 0),
            "f_bytes": int(fnum(fwd.get("bytes")) or 0),
            "r_src": rev.get("src"), "r_dst": rev.get("dst"),
            "r_sport": rev.get("sport"), "r_dport": rev.get("dport"),
            "r_packets": int(fnum(rev.get("packets")) or 0),
            "r_bytes": int(fnum(rev.get("bytes")) or 0),
        })
    return flows


def _is_lan(ip):
    return (ip or "").startswith(("192.168.1.", "127.", "::1", "fe80:"))


class FlowTracker:
    """对 conntrack 快照做增量 → 每流速率 + 聚合（供特征与启发式分类）。"""

    def __init__(self, max_flows=400, stale_s=90):
        self.prev = {}
        self.max_flows = max_flows
        self.stale_s = stale_s
        self.last = {}

    def sample(self, flows, now=None):
        now = now or time.time()
        out = []
        seen = set()
        for fl in flows[: self.max_flows]:
            key = "%s|%s>%s|%s>%s" % (fl["proto"], fl["f_src"], fl["f_sport"], fl["f_dst"], fl["f_dport"])
            seen.add(key)
            prev = self.prev.get(key)
            entry = {
                "key": key, "proto": fl["proto"], "state": fl["state"],
                "src": fl["f_src"], "dst": fl["f_dst"],
                "sport": fl["f_sport"], "dport": fl["f_dport"],
                "bytes_fwd": fl["f_bytes"], "bytes_rev": fl["r_bytes"],
                "packets_fwd": fl["f_packets"], "packets_rev": fl["r_packets"],
                "rate_fwd_kbps": None, "rate_rev_kbps": None,
                "pkt_size_fwd": None, "pkt_size_rev": None,
            }
            tot_pkts = fl["f_packets"]
            if tot_pkts > 0:
                entry["pkt_size_fwd"] = round(fl["f_bytes"] / tot_pkts, 1)
            if fl["r_packets"] > 0:
                entry["pkt_size_rev"] = round(fl["r_bytes"] / fl["r_packets"], 1)
            if prev:
                dt = now - prev[0]
                if dt > 0.3:
                    entry["rate_fwd_kbps"] = round((fl["f_bytes"] - prev[1]) * 8.0 / dt / 1000.0, 2)
                    entry["rate_rev_kbps"] = round((fl["r_bytes"] - prev[2]) * 8.0 / dt / 1000.0, 2)
            self.prev[key] = (now, fl["f_bytes"], fl["r_bytes"])
            out.append(entry)
        # 清理过期
        for k in [k for k, v in self.prev.items() if now - v[0] > self.stale_s]:
            self.prev.pop(k, None)
        # 方向归并到「我们」视角：对非 LAN 对端，rev 方向 = 下行
        agg = {"n_flows": len(out), "n_wan": 0, "n_established": 0,
               "dl_kbps": 0.0, "ul_kbps": 0.0, "ts_ms": now_ms()}
        for e in out:
            is_wan = not (_is_lan(e["src"]) and _is_lan(e["dst"]))
            e["wan"] = is_wan
            if is_wan:
                agg["n_wan"] += 1
                # 若 f_dst 为本地(10.x CGNAT 我们自己)则 rev = 下行；否则取绝对值判断
                if _is_lan(e["src"]):
                    agg["ul_kbps"] += max(e["rate_fwd_kbps"] or 0.0, 0.0)
                    agg["dl_kbps"] += max(e["rate_rev_kbps"] or 0.0, 0.0)
                else:
                    agg["dl_kbps"] += max(e["rate_fwd_kbps"] or 0.0, 0.0)
                    agg["ul_kbps"] += max(e["rate_rev_kbps"] or 0.0, 0.0)
            if e["state"] == "ESTABLISHED":
                agg["n_established"] += 1
        self.last = agg
        top = sorted(out, key=lambda e: (e["rate_fwd_kbps"] or 0) + (e["rate_rev_kbps"] or 0),
                     reverse=True)[:15]
        return {"agg": agg, "top": top, "ts": now}
