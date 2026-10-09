# -*- coding: utf-8 -*-
"""通用工具：子进程、时间、统计（纯 stdlib）。"""
import json
import math
import subprocess
import time


def now_ms():
    return int(time.time() * 1000)


def sh(cmd, t=10):
    """执行 shell 命令，返回 (rc, 输出文本)。超时/异常安全。
    ⚠ 坑（2026-10-09 实测）：stdout/stderr 分别捕获后直接拼接，若 stdout 无结尾换行，
    会与其后 stderr 的 libtrm 噪音粘成一行而被 clean_out 误删（信号丢失）。
    修复：拼接前给 stdout 补换行。"""
    try:
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=t)
        so = p.stdout or ""
        se = p.stderr or ""
        if so and not so.endswith("\n"):
            so += "\n"
        return p.returncode, so + se
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as e:  # pragma: no cover
        return 127, str(e)


def clean_out(out):
    """剔除以 libtrm 开头的噪音行（仅行首匹配，避免误删含数据的行）。"""
    return "\n".join(ln for ln in (out or "").splitlines()
                     if not ln.strip().startswith("libtrm")).strip()


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fnum(v):
    """宽松转 float，失败返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- 统计（不依赖 statistics，python3-light 也有保障） ----------

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def std(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def percentile(xs, q):
    """q in 0..100；xs 可为乱序。"""
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * (q / 100.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(xs[lo])
    return float(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo))


def slope(xs):
    """线性回归斜率（对索引）。不足 2 点返回 None。"""
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 2:
        return None
    xm = (n - 1) / 2.0
    ym = sum(xs) / n
    num = sum((i - xm) * (xs[i] - ym) for i in range(n))
    den = sum((i - xm) ** 2 for i in range(n))
    return num / den if den else None


def summarize(xs):
    """一组值的常用统计（供特征窗）。"""
    return {
        "mean": mean(xs), "std": std(xs),
        "min": min((x for x in xs if x is not None), default=None),
        "max": max((x for x in xs if x is not None), default=None),
        "p10": percentile(xs, 10), "p50": percentile(xs, 50), "p90": percentile(xs, 90),
        "slope": slope(xs),
    }


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)
