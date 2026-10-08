"""流量流窗采集（设计 15.4）：conntrack metadata，不保存 payload。

/proc/net/nf_conntrack 每行含双向 packets/bytes；相邻快照做差得到流级速率特征。
分类器只吃统计特征（不解析报文内容），满足"仅 metadata"约束。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

_PRIVATE_PREFIXES = ("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.2", "172.30.", "172.31.", "100.64.", "100.65", "100.66",
                     "100.67", "100.68", "100.69", "100.70", "100.71", "100.72",
                     "100.73", "100.74", "100.75", "100.76", "100.77", "100.78",
                     "100.79", "127.")


def _is_private(ip: str) -> bool:
    return ip.startswith(_PRIVATE_PREFIXES)


@dataclass
class Flow:
    proto: str
    src: str
    dst: str
    sport: int
    dport: int
    o_pkts: int = 0
    o_bytes: int = 0
    r_pkts: int = 0
    r_bytes: int = 0

    def key(self) -> tuple:
        return (self.proto, self.src, self.sport, self.dst, self.dport)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_conntrack_line(ln: str) -> Flow | None:
    tok = ln.split()
    if len(tok) < 8 or tok[0] not in ("ipv4", "ipv6"):
        return None
    proto = tok[2]
    src = dst = None
    sport = dport = 0
    o_pkts = o_bytes = r_pkts = r_bytes = 0
    side = 0
    for t in tok:
        if t.startswith("src="):
            if side == 0:
                src = t[4:]
                side = 1
            elif side == 1 and dst is None:
                dst = t[4:]
        elif t.startswith("dst=") and side == 1:
            pass
        elif t.startswith("sport="):
            v = int(t[6:]) if t[6:].isdigit() else 0
            if sport == 0:
                sport = v
        elif t.startswith("dport="):
            v = int(t[6:]) if t[6:].isdigit() else 0
            if dport == 0:
                dport = v
        elif t.startswith("packets="):
            v = int(t[8:]) if t[8:].isdigit() else 0
            if o_pkts == 0:
                o_pkts = v
            else:
                r_pkts = v
        elif t.startswith("bytes="):
            v = int(t[6:]) if t[6:].isdigit() else 0
            if o_bytes == 0:
                o_bytes = v
            else:
                r_bytes = v
    if not src or not dst:
        return None
    return Flow(proto, src, dst, sport, dport, o_pkts, o_bytes, r_pkts, r_bytes)


class FlowTracker:
    """快照差分 → 窗口流量特征。"""

    def __init__(self):
        self._last: dict = {}
        self._last_ts: float | None = None

    def update(self, lines: list, ts: float) -> dict:
        cur = {}
        for ln in lines:
            f = parse_conntrack_line(ln)
            if f:
                cur[f.key()] = f
        dt = (ts - self._last_ts) if self._last_ts else None
        new_flows = 0
        ul_bytes = dl_bytes = 0
        pkt_sizes = []
        udp_n = tcp_n = 0
        flows_out = []
        for k, f in cur.items():
            prev = self._last.get(k)
            if prev is None:
                new_flows += 1
                continue
            do_b = max(0, f.o_bytes - prev.o_bytes)
            dr_b = max(0, f.r_bytes - prev.r_bytes)
            do_p = max(0, f.o_pkts - prev.o_pkts)
            dr_p = max(0, f.r_pkts - prev.r_pkts)
            if _is_private(f.src):
                ul_bytes += do_b
                dl_bytes += dr_b
            else:
                ul_bytes += dr_b
                dl_bytes += do_b
            for nb, npk in ((do_b, do_p), (dr_b, dr_p)):
                if npk > 0:
                    pkt_sizes.append(nb / npk)
            flows_out.append({"proto": f.proto, "dst": f.dst, "dport": f.dport,
                              "ul": do_b if _is_private(f.src) else dr_b,
                              "dl": dr_b if _is_private(f.src) else do_b})
            if f.proto == "udp":
                udp_n += 1
            elif f.proto == "tcp":
                tcp_n += 1
        self._last = cur
        self._last_ts = ts
        n = len(cur) or 1
        mbps = (lambda b: round(b * 8 / dt / 1e6, 3) if dt and dt > 0 else None)
        out = {
            "ts": ts,
            "n_active": len(cur),
            "new_flows": new_flows,
            "new_flow_ratio": round(new_flows / n, 3),
            "ul_bytes": ul_bytes, "dl_bytes": dl_bytes,
            "ul_mbps": mbps(ul_bytes), "dl_mbps": mbps(dl_bytes),
            "ul_ratio": round(ul_bytes / (ul_bytes + dl_bytes), 3)
            if (ul_bytes + dl_bytes) > 0 else None,
            "udp_ratio": round(udp_n / n, 3),
            "tcp_ratio": round(tcp_n / n, 3),
            "pkt_size_p50": _median(pkt_sizes),
            "small_pkt_ratio": round(sum(1 for s in pkt_sizes if s < 200) / len(pkt_sizes), 3)
            if pkt_sizes else None,
            "top_ports": _top_ports(flows_out),
            "flows": flows_out[:200],
        }
        return out


def _median(v):
    if not v:
        return None
    s = sorted(v)
    return round(s[len(s) // 2], 1)


def _top_ports(flows: list, k: int = 5) -> list:
    cnt: dict = {}
    for f in flows:
        key = "%s/%s" % (f["proto"], f["dport"])
        cnt[key] = cnt.get(key, 0) + 1
    return sorted(cnt.items(), key=lambda x: -x[1])[:k]
