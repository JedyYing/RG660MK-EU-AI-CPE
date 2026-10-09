#!/usr/bin/env python3
"""无 pytest 环境时的轻量跑测器：python3 tests/run_all.py"""
import importlib.util
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

total = passed = 0
for fn in sorted(os.listdir(HERE)):
    if not fn.startswith("test_") or not fn.endswith(".py"):
        continue
    spec = importlib.util.spec_from_file_location(fn[:-3], os.path.join(HERE, fn))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in dir(mod):
        if name.startswith("test_") and callable(getattr(mod, name)):
            total += 1
            try:
                getattr(mod, name)()
                passed += 1
                print("PASS %s::%s" % (fn, name))
            except Exception:
                print("FAIL %s::%s" % (fn, name))
                traceback.print_exc()
print("---- %d/%d passed ----" % (passed, total))
sys.exit(0 if passed == total else 1)
