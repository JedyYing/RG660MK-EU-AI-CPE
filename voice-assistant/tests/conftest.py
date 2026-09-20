# -*- coding: utf-8 -*-
"""pytest 配置：test_acceptance.py 是 plain 脚本（模块级 sys.exit），
按设计用 `python3 tests/test_acceptance.py` 独立运行，不参与 pytest 收集。"""
collect_ignore = ["test_acceptance.py"]
