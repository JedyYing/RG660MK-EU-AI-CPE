#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tier-1 吞吐采样器: 每 1s 采集接口计数器, 输出均值/峰值/波动幅度(CV)"""
import sys, time, json, re, subprocess

WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 60
OUTJSON = sys.argv[2] if len(sys.argv) > 2 else '/tmp/t1_throughput.json'

IFACES = ['rai0', 'ra0', 'br-lan', 'ccmni3']

def counter(iface, kind):
    try:
        with open(f'/sys/class/net/{iface}/statistics/{kind}_bytes') as f:
            return int(f.read().strip())
    except Exception:
        return 0

def station(iface='rai0'):
    try:
        r = subprocess.run(['iw', 'dev', iface, 'station', 'dump'],
                           capture_output=True, text=True, timeout=10)
        s = r.stdout
        m = re.search(r'Station (\S+)', s)
        mac = m.group(1) if m else '-'
        m = re.search(r'signal:\s*([^\n]+)', s)
        sig = m.group(1).strip() if m else '-'
        m = re.search(r'tx retries:\s*(\d+)', s)
        retry = int(m.group(1)) if m else 0
        return mac, sig, retry
    except Exception:
        return '-', '-', 0

prev = {i: (counter(i, 'rx'), counter(i, 'tx')) for i in IFACES}
series = {i: {'tx': [], 'rx': []} for i in IFACES}
retry_series = []

t0 = time.time()
print(f"# 采样开始, 窗口 {WINDOW}s, 间隔 1s")
for sec in range(WINDOW):
    time.sleep(1)
    cur = {i: (counter(i, 'rx'), counter(i, 'tx')) for i in IFACES}
    for i in IFACES:
        series[i]['tx'].append((cur[i][1] - prev[i][1]) * 8 / 1e6)  # Mbps
        series[i]['rx'].append((cur[i][0] - prev[i][0]) * 8 / 1e6)
    if sec % 5 == 0:
        mac, sig, r = station('rai0')
        retry_series.append((sec, r))
    prev = cur

def stat(vals):
    if not vals:
        return {'avg': 0, 'peak': 0, 'std': 0, 'cv': 0}
    n = len(vals)
    avg = sum(vals) / n
    peak = max(vals)
    var = sum((x - avg) ** 2 for x in vals) / n
    std = var ** 0.5
    cv = (std / avg * 100) if avg > 0.001 else 0
    return {'avg': round(avg, 3), 'peak': round(peak, 3),
            'std': round(std, 3), 'cv': round(cv, 1)}

result = {'window_s': WINDOW, 'elapsed': round(time.time() - t0, 1)}
for i in IFACES:
    result[i] = {'down_AP2STA_or_egress': stat(series[i]['tx']),
                 'up_or_ingress': stat(series[i]['rx'])}

# 手机链路 (AP->STA = rai0 tx; 2.4G 合并)
ap2sta = [series['rai0']['tx'][k] + series['ra0']['tx'][k] for k in range(WINDOW)]
result['phone_downlink_Mbps'] = stat(ap2sta)
mac, sig, r_end = station('rai0')
result['station'] = {'mac': mac, 'final_rssi': sig, 'retry_samples': retry_series}

with open(OUTJSON, 'w') as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

print(f"\n===== Tier-1 吞吐汇总 ({WINDOW}s) =====")
print(f"手机下行 (AP→STA): 均值 {result['phone_downlink_Mbps']['avg']:.2f} Mbps | "
      f"峰值 {result['phone_downlink_Mbps']['peak']:.2f} Mbps | "
      f"σ {result['phone_downlink_Mbps']['std']:.2f} | 波动系数 {result['phone_downlink_Mbps']['cv']:.0f}%")
print(f"WAN 下行 (ccmni3): 均值 {result['ccmni3']['down_AP2STA_or_egress']['avg']:.2f} Mbps | "
      f"峰值 {result['ccmni3']['down_AP2STA_or_egress']['peak']:.2f} Mbps")
print(f"终端: {mac} RSSI {sig}")
print(f"JSON: {OUTJSON}")
