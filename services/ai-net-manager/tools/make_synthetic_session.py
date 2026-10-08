"""确定性合成会话生成器（离线回归/回放输入；不依赖设备）。

场景：NR 服务小区（pci 784）从 -90dBm 线性劣化到 -125dBm，邻区 pci 493 稳定 -86dBm；
QoE 探针稳定良好。预期行为（设计 7.1 迟滞 + 驻留）：
  ~35s 出现 score_gain 但被 min_dwell(60s) 拦截 → 65s 驻留满足后产生 lock_cell 决策（shadow 不执行）。

用法：python3 tools/make_synthetic_session.py --out /tmp/session
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for _p in (os.path.join(_ROOT, "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_net.collectors import modem      # noqa: E402

T0 = 1759900000000
N = 90


def _meas(val1, pci, cid, arfcn=426030, val3=29):
    return ('+ECELLMEAS: 11,%d,%d,%d,-46,%d,"%s",1,"46011","CHN-CT",1,38880'
            % (arfcn, pci, val1, val3, cid))


def build(out_dir: str, n: int = N) -> tuple:
    os.makedirs(out_dir, exist_ok=True)
    c5g = ['+C5GREG: 2,1,"594FCB484","46011",7,11,0,0,"00","00000001",7']
    radio = []
    for i in range(n):
        serv = -900 - i * 4                       # -90.0 → -125.6 (div10 口径)
        lines = [_meas(serv, 784, "594FCB484"),   # 服务小区
                 _meas(-860, 493, "5959A0401"),   # 邻区 A（稳定强）
                 _meas(-1000, 492, "5959A0400")]  # 邻区 B
        s = modem.build_radio_sample(lines, c5g, None, ts_ms=T0 + i * 1000)
        radio.append(s.to_dict())
    qoe = [{"ts_ms": T0 + i * 1000, "wan_if": "ccmni2", "dl_mbps": 20.0, "ul_mbps": 5.0,
            "rtt_ms_p50": 35.0, "rtt_ms_p95": 60.0, "jitter_ms_p95": 8.0,
            "loss_rate": 0.0, "active_flows": 12, "missing": [], "source": "synthetic"}
           for i in range(n)]
    rp = os.path.join(out_dir, "radio.jsonl")
    qp = os.path.join(out_dir, "qoe.jsonl")
    with open(rp, "w", encoding="utf-8") as f:
        for r in radio:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(qp, "w", encoding="utf-8") as f:
        for q in qoe:
            f.write(json.dumps(q) + "\n")
    return rp, qp


def main(argv=None):
    ap = argparse.ArgumentParser(description="deterministic synthetic session generator")
    ap.add_argument("--out", default="/tmp/ai_net_session")
    ap.add_argument("--n", type=int, default=N)
    args = ap.parse_args(argv)
    rp, qp = build(args.out, args.n)
    print("wrote %s\n      %s" % (rp, qp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
