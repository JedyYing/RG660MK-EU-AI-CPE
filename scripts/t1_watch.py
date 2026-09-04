#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守望进程: 等手机关联后自动执行 Tier-1 测量"""
import subprocess, time, sys, re

MAC = 'e2:c6:40:26:70:5d'
def phone_online():
    for iface in ['rai0', 'ra0']:
        r = subprocess.run(f"iw dev {iface} station dump", shell=True,
                           capture_output=True, text=True, timeout=10)
        if MAC in r.stdout:
            return iface
    return None

print("守望启动: 等待手机关联 (最长 15 分钟)...", flush=True)
for i in range(300):
    iface = phone_online()
    if iface:
        print(f"手机已关联 {iface}! 3 秒后开始测量", flush=True)
        time.sleep(3)
        r = subprocess.run(['/data/ai_cpe/hermes/venv/bin/python3.12',
                            '/data/ai_cpe/hermes/home/diag/t1_run.py', '90', MAC],
                           capture_output=True, text=True, timeout=420)
        print(r.stdout, flush=True)
        if r.stderr:
            print(r.stderr[-500:], flush=True)
        print("===== 测量完成, 守望退出 =====", flush=True)
        sys.exit(0)
    time.sleep(3)
print("守望超时: 15 分钟内手机未关联", flush=True)
sys.exit(1)
