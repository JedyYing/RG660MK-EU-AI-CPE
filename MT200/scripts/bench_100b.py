#!/usr/bin/env python3
"""Benchmark a served llama.cpp model with fixed prompts.

Records prefill/decode rates from llama.cpp native /completion timings, plus
process RSS and system memory, and writes a JSON + markdown table.

usage: python bench_100b.py <port> <label> [n_predict]
"""
import ctypes
import json
import statistics
import sys
import time
import urllib.request
from ctypes import wintypes

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
LABEL = sys.argv[2] if len(sys.argv) > 2 else "model"
N_PREDICT = int(sys.argv[3]) if len(sys.argv) > 3 else 128
BASE = f"http://127.0.0.1:{PORT}"

PROMPTS = [
    "用一句话说明什么是 MoE 混合专家模型。",
    "把这句话翻译成英文：本地推理的关键瓶颈是内存带宽。",
    "列出 1 到 10 之间所有质数，只输出数字。",
    "一个仓库有 480 件货，上午出库 175 件，下午入库 132 件，现在多少件？只回答数字。",
    "用 30 字概括：为什么小批量推理吃不满 NPU 峰值算力。",
]


class MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def sys_mem_mb():
    m = MEMSTATUS(); m.dwLength = ctypes.sizeof(MEMSTATUS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return (m.ullTotalPhys - m.ullAvailPhys) / 2**20, m.ullAvailPhys / 2**20


def wait_ready(timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=5) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return time.time() - t0
        except Exception:
            pass
        time.sleep(5)
    raise SystemExit("server did not become ready")


def ask(prompt):
    body = json.dumps({"prompt": prompt, "n_predict": N_PREDICT, "temperature": 0.0,
                       "cache_prompt": False, "stream": False}).encode()
    req = urllib.request.Request(BASE + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=3600) as r:
        d = json.loads(r.read())
    wall = time.perf_counter() - t0
    t = d.get("timings", {})
    return {
        "text": (d.get("content") or "").strip().replace("\n", " ")[:120],
        "prompt_n": t.get("prompt_n"), "prompt_tps": t.get("prompt_per_second"),
        "pred_n": t.get("predicted_n"), "pred_tps": t.get("predicted_per_second"),
        "wall_s": round(wall, 2),
    }


ready_s = wait_ready()
print(f"[{LABEL}] ready in {ready_s:.1f}s", flush=True)
rows = []
for i, p in enumerate(PROMPTS, 1):
    used_mb, avail_mb = sys_mem_mb()
    r = ask(p)
    rows.append(r)
    print(f"  Q{i}: prefill {r['prompt_tps'] or 0:7.1f} tok/s | decode {r['pred_tps'] or 0:6.2f} tok/s "
          f"| {r['pred_n']} tok in {r['wall_s']}s | mem used {used_mb:.0f} MB / avail {avail_mb:.0f} MB", flush=True)
    print(f"       -> {r['text'][:70]}", flush=True)

dec = [r["pred_tps"] for r in rows if r["pred_tps"]]
pre = [r["prompt_tps"] for r in rows if r["prompt_tps"]]
summary = {
    "label": LABEL, "port": PORT, "n_predict": N_PREDICT, "ready_s": round(ready_s, 1),
    "decode_tps_median": round(statistics.median(dec), 2) if dec else None,
    "prefill_tps_median": round(statistics.median(pre), 1) if pre else None,
    "decode_tps_all": dec, "prefill_tps_all": pre,
    "mem_used_mb": round(sys_mem_mb()[0]), "rows": rows,
}
out = rf"C:\AI\logs\bench-{LABEL}.json"
json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=1))
print("saved", out)
