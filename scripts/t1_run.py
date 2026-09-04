#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tier-1 一键测量: 等手机关联 -> tcpdump 抓包 + 吞吐采样 -> 自动分析"""
import subprocess, time, sys, re, json, os

WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 90
MAC = sys.argv[2] if len(sys.argv) > 2 else 'e2:c6:40:26:70:5d'
PCAP = '/tmp/t1.pcap'

def run(cmd, t=30):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
        return (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return ''

def phone_online():
    for iface in ['rai0', 'ra0']:
        s = run(f"iw dev {iface} station dump")
        if MAC in s:
            m = re.search(r'signal:\s*([^\n]+)', s)
            return iface, m.group(1).strip() if m else '?'
    return None, None

# 1. 等待关联
print("等待手机关联 (最多 90s)...")
for i in range(30):
    iface, sig = phone_online()
    if iface:
        print(f"✓ 手机已关联到 {iface} (RSSI {sig})")
        break
    time.sleep(3)
else:
    print("✗ 90s 内手机未关联, 中止。请先连接 RG660MK WiFi。")
    sys.exit(2)

time.sleep(2)  # 让链路稳定

# 2. 启动抓包 (any 接口, 按 MAC 过滤, snaplen 256)
if os.path.exists(PCAP):
    os.remove(PCAP)
td = subprocess.Popen(['tcpdump', '-i', 'any', '-s', '256', '-w', PCAP,
                       f'ether host {MAC}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(f"抓包已启动 (tcpdump PID {td.pid}, snaplen 256, 过滤 MAC {MAC})")
time.sleep(1)

# 3. 吞吐采样 (WINDOW 秒)
print(f"吞吐采样 {WINDOW}s ...")
r = subprocess.run(['/data/ai_cpe/hermes/venv/bin/python3.12',
                    '/data/ai_cpe/hermes/home/diag/t1_throughput.py', str(WINDOW)],
                   capture_output=True, text=True, timeout=WINDOW + 60)
print(r.stdout)

# 4. 停止抓包
td.terminate()
try:
    td.wait(timeout=5)
except Exception:
    td.kill()
time.sleep(1)
print(f"pcap 大小: {os.path.getsize(PCAP)} bytes")

# 5. 分析
print()
r = subprocess.run(['/data/ai_cpe/hermes/venv/bin/python3.12',
                    '/data/ai_cpe/hermes/home/diag/t1_pcap_analyze.py', PCAP],
                   capture_output=True, text=True, timeout=300)
print(r.stdout)
print(r.stderr[-300:] if r.stderr else "")
