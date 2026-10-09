#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YAML → JSON 转换（仓库存 YAML 权威版，设备消费 JSON）。
用法: python3 tools/yaml2json.py [--dir config]  （需要主机安装 PyYAML）"""
import argparse
import json
import os
import sys

try:
    import yaml
except ImportError:
    print("缺少 PyYAML：在主机执行 `python3 -m pip install pyyaml` 后重试")
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config"))
    args = ap.parse_args()
    n = 0
    for fn in sorted(os.listdir(args.dir)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        src = os.path.join(args.dir, fn)
        dst = os.path.splitext(src)[0] + ".json"
        with open(src, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("%s -> %s" % (fn, os.path.basename(dst)))
        n += 1
    print("done:", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
