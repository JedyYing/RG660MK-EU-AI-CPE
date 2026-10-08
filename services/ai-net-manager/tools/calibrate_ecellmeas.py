"""ECELLMEAS 单位标定（开放问题 OP-1；设计 15.10 现场校准步骤）。

背景：+ECELLMEAS 的 val1/val2/val3 物理单位未在文档中给出。当前默认
provisional_div10（val/10）仅为假设。本工具在设备上用只读命令做交叉标定：
反复背靠背采集 AT+ECELLMEAS?（服务小区原始 val）与 AT+ECSQ?（标准语义
RSRP/RSRQ/SNR），按最小二乘/中位数比对候选标定式，输出推荐标定与残差。

只读保证：仅发送以 ? 结尾的查询命令（ATTransport 另有硬护栏）。
产出：reports/ecellmeas_calibration.json（含样本、候选式残差、结论）
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_net.collectors import modem                       # noqa: E402
from ai_net.collectors.at_transport import ATTransport    # noqa: E402

# 候选标定式：名称 → (rsrp(a,b), rsrq(a,b), sinr(a,b))，形式 a*raw + b
CANDIDATES = {
    "div10":            {"rsrp": (0.1, 0.0), "rsrq": (0.1, 0.0), "sinr": (0.1, 0.0)},
    "div10_off_140":    {"rsrp": (0.1, -140.0), "rsrq": (0.1, -19.5), "sinr": (0.1, -20.0)},
    "raw_off_140":      {"rsrp": (1.0, -140.0), "rsrq": (1.0, -19.5), "sinr": (1.0, -20.0)},
    "half_off_140":     {"rsrp": (0.5, -140.0), "rsrq": (0.5, -19.5), "sinr": (0.5, -20.0)},
}


def collect(transport: ATTransport, n: int, interval: float) -> list:
    samples = []
    for i in range(n):
        r1 = transport.send(modem.CMD_ECELLMEAS_QUERY)
        r2 = transport.send(modem.CMD_ECSQ_QUERY)
        if r1.ok and r2.ok:
            rows = modem.parse_ecellmeas_lines(r1.lines)
            esq = modem.parse_ecsq(r2.lines)
            if rows and esq:
                row = rows[0]                    # 服务小区（实测按强度排序）
                samples.append({
                    "t": round(time.time(), 1),
                    "val1": row.get("val1_raw"), "val2": row.get("val2_raw"),
                    "val3": row.get("val3_raw"), "cid": row.get("cid"),
                    "ecsq_rsrp": esq.get("rsrp_dbm"), "ecsq_rsrq": esq.get("rsrq_db"),
                    "ecsq_sinr": esq.get("sinr_db"),
                })
        else:
            print("  sample %d skipped (ecellmeas=%s ecsq=%s)"
                  % (i, r1.error, r2.error))
        if interval:
            time.sleep(interval)
    return samples


def fit_residuals(samples: list) -> dict:
    out = {}
    for name, coef in CANDIDATES.items():
        res = {"rsrp": [], "rsrq": [], "sinr": []}
        for s in samples:
            for field, raw_key in (("rsrp", "val1"), ("rsrq", "val2"), ("sinr", "val3")):
                a, b = coef[field]
                ref = s.get("ecsq_" + field)
                if s.get(raw_key) is None or ref is None:
                    continue
                res[field].append(a * s[raw_key] + b - ref)
        summary = {}
        for field, d in res.items():
            if d:
                summary[field] = {"n": len(d), "median_abs_err": round(statistics.median(
                    abs(x) for x in d), 2),
                    "mean_err": round(statistics.fmean(d), 2)}
        out[name] = summary
    return out


def recommend(fits: dict) -> dict:
    scored = []
    for name, fields in fits.items():
        vals = [v["median_abs_err"] for v in fields.values() if v.get("n")]
        if not vals:
            continue
        scored.append((statistics.fmean(vals), name))
    if not scored:
        return {"calibration": None, "reason": "no_overlap_samples"}
    scored.sort()
    best_err, best = scored[0]
    return {"calibration": best, "mean_median_abs_err": round(best_err, 2),
            "ranking": [{"name": n, "err": round(e, 2)} for e, n in scored],
            "note": "ERR>1.5dB 视为不可用；结论需人工复核后再改 config 默认标定"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="calibrate ECELLMEAS units vs AT+ECSQ")
    ap.add_argument("--n", type=int, default=30, help="采样次数")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--out", default=os.path.join(_ROOT, "reports",
                                                 "ecellmeas_calibration.json"))
    args = ap.parse_args(argv)
    t = ATTransport()
    print("collecting %d back-to-back ECELLMEAS/ECSQ pairs (read-only)..." % args.n)
    samples = collect(t, args.n, args.interval)
    fits = fit_residuals(samples)
    rec = recommend(fits)
    rep = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "n_samples": len(samples), "candidates": fits, "recommendation": rec,
           "samples": samples[:60]}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    print("→ %s" % args.out)
    return 0 if samples else 1


if __name__ == "__main__":
    raise SystemExit(main())
