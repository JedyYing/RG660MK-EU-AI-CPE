#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI CPE 本地 MQTT broker 守护（纯标准库实现，见 mqtt_lib.Broker）。"""
import argparse, sys, time
sys.path.insert(0, "/data/ai_cpe/services/smarthome")
from mqtt_lib import Broker      # noqa: E402


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--host", default="0.0.0.0"); ap.add_argument("--port", type=int, default=1883)
    a = ap.parse_args()
    b = Broker(a.host, a.port, log=lambda m: print("[broker] %s" % m, flush=True))
    b.start()


if __name__ == "__main__":
    main()
