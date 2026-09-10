#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 网络诊断 (纯 stdlib)

汇总 CPE 网络全景：接口/路由/蜂窝状态 + LAN/WiFi 客户端 + 每客户端 MAC 厂商(OUI)。
输出为纯文本报告，供 Hermes 直接转述给用户。

用法:
    python3 netdiag.py            # 完整诊断
    python3 netdiag.py clients    # 只看已连接客户端
"""
import subprocess, sys, re, json
import urllib.request

OUI_CACHE = {}


def sh(cmd, timeout=15):
    try:
        r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception as e:
        return ""


def oui_vendor(mac: str) -> str:
    """MAC -> 厂商 (macvendors.com，带缓存)。"""
    oui = mac.replace(":", "").replace("-", "").upper()[:6]
    if oui in OUI_CACHE:
        return OUI_CACHE[oui]
    vendor = ""
    try:
        req = urllib.request.Request("https://api.macvendors.com/" + oui,
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            vendor = r.read().decode("utf-8", "replace").strip()
    except Exception:
        vendor = ""
    OUI_CACHE[oui] = vendor or "?"
    return OUI_CACHE[oui]


def is_random_mac(mac: str) -> bool:
    """首字节 bit0x02 = 本地管理地址(随机/隐私 MAC)。"""
    try:
        b = int(mac.split(":")[0], 16)
        return bool(b & 0x02)
    except Exception:
        return False


def wifi_clients():
    """遍历 iw dev 接口，收集各 AP 接口的关联客户端。"""
    out = []
    ifs = sh("iw dev 2>/dev/null | awk -F'Interface ' '/Interface/{print $2}'")
    for iface in ifs.splitlines():
        iface = iface.strip()
        if not iface:
            continue
        dump = sh("iw dev %s station dump 2>/dev/null" % iface, timeout=10)
        if not dump:
            continue
        # 每个 Station 块以 "Station <mac> (on <iface>)" 开头
        for m in re.finditer(r"Station ([0-9a-fA-F:]{17})", dump):
            mac = m.group(1).lower()
            # 提取该站的 signal
            sig = ""
            mm = re.search(r"signal:\s*(-?\d+)", dump[m.start():])
            if mm:
                sig = mm.group(1) + " dBm"
            out.append((iface, mac, sig, oui_vendor(mac), is_random_mac(mac)))
    return out


def dhcp_leases():
    """解析 /tmp/dhcp.leases: <expire> <mac> <ip> <hostname> <clientid>"""
    rows = []
    try:
        with open("/tmp/dhcp.leases", "r") as f:
            for line in f:
                p = line.split()
                if len(p) >= 4:
                    rows.append({"mac": p[1].lower(), "ip": p[2], "host": p[3]})
    except OSError:
        pass
    return rows


def section(title):
    print("\n== %s ==" % title)


def report_clients():
    leases = dhcp_leases()
    clients = wifi_clients()
    seen = set()
    section("已连接客户端 (WiFi 关联)")
    if not clients:
        print("(无 WiFi 关联客户端)")
    for iface, mac, sig, vendor, rand in clients:
        seen.add(mac)
        ip = host = ""
        for l in leases:
            if l["mac"] == mac:
                ip, host = l["ip"], l["host"]
                break
        tag = " [随机MAC]" if rand else ""
        print("%-18s %-15s %-12s %-20s %s%s" % (mac, ip or "-", sig, vendor, host, tag))
    section("DHCP 租约 (含有线/离线)")
    for l in leases:
        print("%-18s %-15s %s" % (l["mac"], l["ip"], l["host"]))


def report_full():
    section("系统")
    up = sh("cat /proc/uptime 2>/dev/null | cut -d. -f1")
    load = sh("cat /proc/loadavg 2>/dev/null")
    mem = sh("free -m 2>/dev/null | awk '/Mem:/{print $3\"/\"$2\" MB used\"}'")
    print("uptime=%ss load=%s mem=%s" % (up or "?", load or "?", mem or "?"))
    section("接口")
    print(sh("ip -br addr 2>/dev/null") or "(ip -br 不可用)")
    section("路由 (默认)")
    print(sh("ip route 2>/dev/null | grep -E '^default' ") or "(无默认路由)")
    section("蜂窝/WAN")
    wan = sh("ip -br addr 2>/dev/null | grep -E 'ccmni|usb|eth'")
    print(wan or "(未发现 ccmni*/usb/eth 接口)")
    report_clients()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "clients":
        report_clients()
    else:
        report_full()
