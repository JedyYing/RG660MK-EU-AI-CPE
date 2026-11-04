#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总 Hermes token 用量与人民币费用（模组 + 笔记本两套库），供看板展示。

- 今日：当天 00:00 起
- 近 30 天：今天往前 29 天
- 口径沿用 hermes-usage-stats 技能：用量=input+output，cache 单列，峰谷价按消息时刻加权
- 输出 /data/ai_cpe/token_stats.json
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

USAGE = "/data/ai_cpe/tools/usage_stats.py"
PY = "/data/hermes/venv/bin/python"
LOCAL_DB = "/data/hermes/.hermes/state.db"
OUT = "/data/ai_cpe/token_stats.json"

# 设备端 ssh 是 dropbear 客户端：不支持 -o 选项（-o BatchMode=yes 会直接报错），
# 用 -y（自动接受 host key）+ -T（不分配 tty），公钥在 /root/.ssh/id_ed25519_hermes。
KEY = "/root/.ssh/id_ed25519_hermes"
REMOTE_USER = "jedyying"
# 笔记本 LAN IP 由 DHCP 下发会变（曾 .244 → .212），优先从本机 DHCP 租约里按 MAC 取。
LAPTOP_MAC = "00:e0:4c:55:75:98"
LAPTOP_FALLBACK = "192.168.1.212"

REMOTE_PY = "/usr/bin/python3"
REMOTE_SCRIPT = "/home/jedyying/work_cpe_board/tools/usage_stats.py"
REMOTE_DBS = ["/home/jedyying/.hermes/state.db",
              "/home/jedyying/.hermes/profiles/phicoteklegacy/state.db"]


def laptop_ip():
    """从本机 dnsmasq 租约里按 MAC 找笔记本当前 IP。"""
    try:
        with open("/tmp/dhcp.leases", encoding="utf-8", errors="ignore") as f:
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[1].lower() == LAPTOP_MAC:
                    return p[2]
    except OSError:
        pass
    return LAPTOP_FALLBACK


def run_local(db, start):
    r = subprocess.run([PY, USAGE, db, start], capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-200:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def run_remote(db, start):
    cmd = "%s %s %s %s" % (REMOTE_PY, REMOTE_SCRIPT, db, start)
    ssh = ["ssh", "-y", "-T", "-i", KEY, "%s@%s" % (REMOTE_USER, laptop_ip())]
    r = subprocess.run(ssh + [cmd], capture_output=True, text=True, timeout=180,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-200:])
    return json.loads(r.stdout.strip().splitlines()[-1])


def merge(items):
    tot_use = 0.0
    tot_cost = 0.0
    tot_cache = 0.0
    ok = 0
    for d in items:
        if not isinstance(d, dict) or not d.get("ok"):
            continue
        ok += 1
        t = d.get("tot") or {}
        tot_use += float(t.get("inp", 0)) + float(t.get("out", 0))
        tot_cache += float(t.get("cache", 0))
        tot_cost += float(t.get("cost", 0))
    return {"tokens": round(tot_use), "cache_tokens": round(tot_cache),
            "cny": round(tot_cost, 2), "sources_ok": ok}


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    start30 = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
    ip = laptop_ip()
    res = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "laptop_ip": ip,
           "today": None, "m30": None, "errors": []}

    for label, start in (("today", today), ("m30", start30)):
        items = []
        for db in [LOCAL_DB]:
            try:
                items.append(run_local(db, start))
            except Exception as e:
                res["errors"].append("%s local %s" % (label, str(e)[:80]))
        for db in REMOTE_DBS:
            try:
                items.append(run_remote(db, start))
            except Exception as e:
                res["errors"].append("%s remote %s" % (label, str(e)[:80]))
        res[label] = merge(items)
        res[label]["window_start"] = start

    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
