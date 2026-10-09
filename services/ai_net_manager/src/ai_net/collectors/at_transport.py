# -*- coding: utf-8 -*-
"""ATTransport（MTK 适配版，设计 §15.3）
- 串行化所有 modem CLI 调用（全局锁 + 最小间隔），避免并发打爆 AT 口。
- 原始回包保存到 raw dict 由上层落盘（设计 §3.1 三层适配要求）。
"""
import os
import threading
import time

from ..util import clean_out, sh

MIPC = os.environ.get("AI_NET_MIPC", "mipc_wan_cli")
_LOCK = threading.RLock()
_LAST = [0.0]
MIN_GAP_S = 0.03          # 相邻两次 modem 调用的最小间隔


def mipc(args, t=10):
    """调用 mipc_wan_cli。args 为已拼好的参数片段列表（字符串）。"""
    with _LOCK:
        gap = time.time() - _LAST[0]
        if gap < MIN_GAP_S:
            time.sleep(MIN_GAP_S - gap)
        cmd = MIPC + " " + " ".join(args)
        rc, out = sh(cmd, t)
        _LAST[0] = time.time()
        return rc, out


def mipc_clean(args, t=10):
    rc, out = mipc(args, t)
    return rc, clean_out(out)


def at(cmd, t=12):
    """AT 透传（单引号包裹 AT 文本；命令内不要含单引号）。"""
    return mipc(["--at_cmd", "'%s'" % cmd], t)


def at_clean(cmd, t=12):
    rc, out = at(cmd, t)
    return rc, clean_out(out)
