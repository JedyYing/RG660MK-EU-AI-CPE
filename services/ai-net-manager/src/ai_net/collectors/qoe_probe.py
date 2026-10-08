"""QoE 采集：WAN 字节计数 / ICMP RTT 探针 / conntrack 流计数（设计文档 15.4）。

- 纯标准库；ICMP 用 SOCK_DGRAM(IPPROTO_ICMP)（Linux 非特权 ping，设备上 root 亦可 SOCK_RAW）。
- WAN 网卡必须运行期探测（ccmni 编号会随 re-attach 迁移，禁止硬编码）。
- 探针目标一律 IP 字面量（域名解析失败会伪造 loss 信号）。
"""
from __future__ import annotations

import os
import select
import socket
import struct
import time

WAN_CANDIDATE_PREFIXES = ("ccmni", "wwan", "rmnet", "usb", "ppp", "eth")


def detect_wan_if(proc_route: str = "/proc/net/route") -> str | None:
    """按默认路由探测 WAN 接口（0.0.0.0/0 的最低 metric 项）。"""
    try:
        with open(proc_route, encoding="utf-8") as f:
            next(f)
            best = None
            for ln in f:
                p = ln.split()
                if len(p) < 8:
                    continue
                if p[1] != "00000000":           # Destination 0.0.0.0
                    continue
                metric = int(p[6]) if p[6].isdigit() else 0
                if best is None or metric < best[0]:
                    best = (metric, p[0])
            return best[1] if best else None
    except OSError:
        return None


def read_netdev(ifname: str, proc_net_dev: str = "/proc/net/dev") -> dict | None:
    """返回 {rx_bytes, tx_bytes, rx_pkts, tx_pkts}（/proc/net/dev 字段 1/2/3/9/10/11）。"""
    try:
        with open(proc_net_dev, encoding="utf-8") as f:
            for ln in f:
                if ":" not in ln:
                    continue
                name, rest = ln.split(":", 1)
                if name.strip() != ifname:
                    continue
                v = rest.split()
                return {"rx_bytes": int(v[0]), "rx_pkts": int(v[1]),
                        "tx_bytes": int(v[8]), "tx_pkts": int(v[9])}
    except (OSError, ValueError, IndexError):
        return None
    return None


class RateMeter:
    """字节计数器 → Mbps（处理计数回绕/网卡切换）。"""

    def __init__(self):
        self._last = None
        self._t = None

    def update(self, counters: dict | None, now: float | None = None) -> tuple:
        now = now or time.monotonic()
        if not counters:
            return (None, None)
        dl = ul = None
        if self._last and self._t is not None:
            dt = now - self._t
            drx = counters["rx_bytes"] - self._last["rx_bytes"]
            dtx = counters["tx_bytes"] - self._last["tx_bytes"]
            if dt > 0 and drx >= 0 and dtx >= 0:      # 负数=回绕/切换，跳过本窗
                dl = drx * 8 / dt / 1e6
                ul = dtx * 8 / dt / 1e6
        self._last, self._t = counters, now
        return (round(dl, 3) if dl is not None else None,
                round(ul, 3) if ul is not None else None)


def icmp_ping(target: str, timeout_ms: int = 800, seq: int = 1) -> float | None:
    """单发 ICMP echo，返回 RTT ms 或 None。仅 Linux。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except (OSError, AttributeError):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        except OSError:
            return None
    try:
        s.settimeout(timeout_ms / 1000.0)
        pkt_id = os.getpid() & 0xFFFF
        payload = struct.pack("d", time.monotonic()) + b"ai-net-probe"
        hdr = struct.pack("!BBHHH", 8, 0, 0, pkt_id, seq)
        chk = _checksum(hdr + payload)
        pkt = struct.pack("!BBHHH", 8, 0, chk, pkt_id, seq) + payload
        t0 = time.monotonic()
        s.sendto(pkt, (target, 0))
        deadline = t0 + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            r, _, _ = select.select([s], [], [], max(0.0, deadline - time.monotonic()))
            if not r:
                return None
            data, _ = s.recvfrom(1024)
            if len(data) < 28:
                continue
            # SOCK_DGRAM 时内核已剥 IP 头（返回 ICMP 报文），SOCK_RAW 含 IP 头
            off = 20 if (data[0] >> 4) == 4 else 0
            icmp = data[off:]
            if len(icmp) >= 8 and icmp[0] == 0:            # echo reply
                rid, rseq = struct.unpack("!HH", icmp[4:8])
                if rid == pkt_id and rseq == seq:
                    return round((time.monotonic() - t0) * 1000, 2)
        return None
    except OSError:
        return None
    finally:
        s.close()


def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def count_conntrack(path: str = "/proc/net/nf_conntrack") -> tuple:
    """返回 (active_flows, entries)；文件不存在返回 (None, [])。"""
    entries = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if ln.strip():
                    entries.append(ln.strip())
    except OSError:
        return (None, [])
    return (len(entries), entries)
