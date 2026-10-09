# -*- coding: utf-8 -*-
"""QoE 探针（设计 §3.2）：ICMP RTT、TCP 握手 RTT、WAN 计数速率。纯 stdlib/系统工具。"""
import re
import socket
import time

from ..util import sh

RE_PING_TIME = re.compile(r"time=([0-9.]+)\s*ms")


def parse_ping_rtt(out):
    """纯解析：ping 输出 → RTT ms 或 None（丢包/失败）。"""
    m = RE_PING_TIME.search(out or "")
    if m:
        return float(m.group(1))
    return None


class QoEProbe:
    def __init__(self, cfg):
        self.icmp_targets = cfg.get("icmp_targets") or []
        self.tcp_targets = cfg.get("tcp_targets") or []
        self.wan_iface = cfg.get("wan_iface", "ccmni2")
        self.ping_count = int(cfg.get("ping_count", 1))
        self._tcp_cache = {}      # name -> (ip, port) | None
        self._counters = None     # (ts, rx, tx)
        self._tick = 0

    # ---- ICMP ----
    def ping(self, host, timeout_s=2):
        rc, out = sh("ping -c %d -W 1 %s 2>&1" % (self.ping_count, host), timeout_s + self.ping_count)
        return parse_ping_rtt(out)

    # ---- TCP 握手 RTT ----
    def tcp_connect_ms(self, name):
        if name not in self._tcp_cache:
            self._tcp_cache[name] = None
            for t in self.tcp_targets:
                if t.get("name") == name:
                    try:
                        infos = socket.getaddrinfo(t["host"], int(t.get("port", 443)),
                                                   proto=socket.IPPROTO_TCP)
                        self._tcp_cache[name] = (infos[0][4][0], int(t.get("port", 443)))
                    except Exception:
                        pass
                    break
        ent = self._tcp_cache.get(name)
        if not ent:
            return None
        ip, port = ent
        t0 = time.time()
        try:
            s = socket.create_connection((ip, port), timeout=2.0)
            s.close()
            return round((time.time() - t0) * 1000.0, 2)
        except Exception:
            return None

    # ---- WAN 计数 ----
    def wan_counters(self, iface=None):
        iface = iface or self.wan_iface
        try:
            with open("/proc/net/dev", "r") as f:
                for ln in f:
                    if ln.strip().startswith(iface + ":"):
                        parts = ln.split(":", 1)[1].split()
                        return int(parts[0]), int(parts[8])   # rx_bytes, tx_bytes
        except OSError:
            pass
        return None

    def rates(self, now=None):
        """返回 (dl_mbps, ul_mbps) 增量速率；首次调用返回 (None, None)。"""
        now = now or time.time()
        cur = self.wan_counters()
        if cur is None:
            return None, None
        if self._counters is None:
            self._counters = (now, cur[0], cur[1])
            return None, None
        t0, rx0, tx0 = self._counters
        dt = now - t0
        if dt <= 0.2:
            return None, None
        dl = (cur[0] - rx0) * 8.0 / dt / 1e6
        ul = (cur[1] - tx0) * 8.0 / dt / 1e6
        self._counters = (now, cur[0], cur[1])
        return round(max(dl, 0.0), 3), round(max(ul, 0.0), 3)

    def next_tick(self):
        """轮转采样：每 tick 打 ICMP；每 3 个 tick 打一次 TCP。"""
        self._tick += 1
        return (self._tick % 3 == 0)
