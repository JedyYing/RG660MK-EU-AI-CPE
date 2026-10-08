"""AT 解析回归（设计 18：解析验证交付物）。

把设备上 raw_at/at.jsonl 的原始回包重放通过 modem.py 解析器，统计：
- 各命令成功率（AT 层）+ 解析成功率（结构层）
- ECELLMEAS 行数分布 / 服务小区匹配方式（nci 或 first_row）
- 与录制时的解析结果对比（如日志里存了 radio.jsonl，做一致性抽查）

用法：python3 tools/validate_at_parser.py --raw-at /path/at.jsonl [--radio radio.jsonl]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_net.collectors import modem      # noqa: E402


def read_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    return out


def validate(raw_path: str, radio_path: str | None = None) -> dict:
    recs = read_jsonl(raw_path)
    stat = {"total": len(recs), "at_ok": 0, "at_err": 0,
            "by_cmd": Counter(), "parse_ok": Counter(), "parse_fail": Counter(),
            "no_raw": Counter(),
            "ecellmeas_rows": Counter(), "serving_match": Counter(),
            "errors": Counter()}
    last_c5g: list = []
    last_ecsq: list = []
    n_radio_cmp = 0
    cmp_mismatch = []
    radio = read_jsonl(radio_path) if radio_path else []

    for r in recs:
        cmd = (r.get("cmd") or "").strip()
        stat["by_cmd"][cmd] += 1
        if not r.get("ok"):
            stat["at_err"] += 1
            stat["errors"][str(r.get("error"))[:60]] += 1
            continue
        stat["at_ok"] += 1
        lines = [ln for ln in (r.get("raw") or "").splitlines()
                 if ln.strip() and not ln.strip().startswith("AT+")]
        if not lines:
            stat["no_raw"][cmd] += 1              # 录制时未存 raw（如 save_raw=false）
            continue
        if cmd.startswith("AT+ECELLMEAS?"):
            rows = modem.parse_ecellmeas_lines(lines)
            if rows:
                stat["parse_ok"]["ECELLMEAS"] += 1
                stat["ecellmeas_rows"][len(rows)] += 1
                try:
                    s = modem.build_radio_sample(lines, last_c5g, last_ecsq,
                                                 ts_ms=int(float(r.get("ts", 0)) * 1000))
                    stat["serving_match"][s.source.split("|")[-1]] += 1
                    for rec_radio in radio[n_radio_cmp:n_radio_cmp + 3]:
                        if abs(rec_radio.get("ts_ms", 0) / 1000.0
                               - float(r.get("ts", 0))) < 1.5:
                            n_radio_cmp += 1
                            if (rec_radio.get("cell_id") != s.cell_id
                                    or rec_radio.get("pci") != s.pci):
                                cmp_mismatch.append({"ts": r.get("ts"),
                                                     "logged": rec_radio.get("cell_id"),
                                                     "reparsed": s.cell_id})
                            break
                except Exception as e:                     # 解析器异常必须暴露
                    stat["parse_fail"]["ECELLMEAS:build:" + type(e).__name__] += 1
            else:
                stat["parse_fail"]["ECELLMEAS:empty"] += 1
        elif cmd.startswith("AT+C5GREG?"):
            last_c5g = lines
            stat["parse_ok" if modem.parse_c5greg(lines) else "parse_fail"]["C5GREG"] += 1
        elif cmd.startswith("AT+ECSQ?"):
            last_ecsq = lines
            stat["parse_ok" if modem.parse_ecsq(lines) else "parse_fail"]["ECSQ"] += 1
        elif cmd.startswith("AT+EMMCHLCK?"):
            v = modem.parse_lock_state(lines)
            stat["parse_ok" if v is not None else "parse_fail"]["EMMCHLCK"] += 1

    out = {
        "raw_at_file": raw_path,
        "total": stat["total"], "at_ok": stat["at_ok"], "at_err": stat["at_err"],
        "at_success_rate": round(stat["at_ok"] / stat["total"], 4) if stat["total"] else None,
        "by_cmd": dict(stat["by_cmd"]), "parse_ok": dict(stat["parse_ok"]),
        "parse_fail": dict(stat["parse_fail"]), "no_raw": dict(stat["no_raw"]),
        "ecellmeas_rows_dist": dict(stat["ecellmeas_rows"]),
        "serving_match": dict(stat["serving_match"]),
        "at_errors": dict(stat["errors"].most_common(8)),
        "radio_consistency_checked": n_radio_cmp,
        "radio_consistency_mismatch": cmp_mismatch[:10],
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="validate AT parser against recorded raw logs")
    ap.add_argument("--raw-at", required=True)
    ap.add_argument("--radio", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    rep = validate(args.raw_at, args.radio)
    rep["generated_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z")
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("→ %s" % args.out)
    print(text[:2200])
    fails = sum(rep["parse_fail"].values())
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
