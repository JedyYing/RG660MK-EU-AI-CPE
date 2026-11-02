#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermes token 用量按时间窗统计（单库）。仅用标准库，模组 venv / 笔记本系统 python3 都能跑。

用法: python3 usage_stats.py <state.db 路径> <窗口起始 'YYYY-MM-DD'>
输出: 一行 JSON {db, ok, msg_first, msg_last, daily{'YY-MM-DD':{use,cache,cost}},
                tot{inp,out,cache,cost}, models{}, sessions_total}

口径: 用量=input+output；cache 单列；按会话消息数分摊到天；
      跨窗口会话按 窗口消息/全部消息 缩放；峰谷价按消息时刻加权。
"""
import sqlite3
import time
import json
import sys
from collections import defaultdict

RATES = {'pro': {'inp': (4.5, 9.0), 'cache': (0.15, 0.30), 'out': (13.5, 27.0)},
         'flash': {'inp': (1.2, 2.0), 'cache': (0.02, 0.04), 'out': (4.0, 8.0)}}


def is_peak(lt):
    # 高峰: 工作日 9:00-12:00, 14:00-18:00（本机时区）
    return lt.tm_wday < 5 and (9 <= lt.tm_hour < 12 or 14 <= lt.tm_hour < 18)


def summarize(path, win_start):
    WIN = time.mktime(time.strptime(win_start, '%Y-%m-%d'))
    res = {'db': path, 'window': win_start}
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
    except Exception as e:
        res['error'] = str(e)
        return res
    try:
        rows = list(con.execute(
            "select date(timestamp,'unixepoch','localtime') d, count(*) "
            "from messages where timestamp is not null group by d order by d"))
        res['msg_first'] = rows[0] if rows else None
        res['msg_last'] = rows[-1] if rows else None

        usage = con.execute(
            'select session_id, model, input_tokens, output_tokens, cache_read_tokens '
            'from session_model_usage where last_seen >= ?', (WIN,)).fetchall()

        msg_days = defaultdict(int)   # (sid, 'YY-MM-DD') -> 窗口内消息数
        peak = defaultdict(int)       # sid -> 窗口内高峰时段消息数
        mtotal = defaultdict(int)     # sid -> 全时段消息数
        for sid, ts in con.execute('select session_id, timestamp from messages where timestamp is not null'):
            mtotal[sid] += 1
            if ts >= WIN:
                lt = time.localtime(ts)
                msg_days[(sid, time.strftime('%y-%m-%d', lt))] += 1
                if is_peak(lt):
                    peak[sid] += 1
        win_msgs = defaultdict(int)
        for (sid, d), c in msg_days.items():
            win_msgs[sid] += c

        daily = defaultdict(lambda: {'use': 0.0, 'cache': 0.0, 'cost': 0.0})
        tot = {'inp': 0.0, 'out': 0.0, 'cache': 0.0, 'cost': 0.0}
        models = defaultdict(lambda: {'inp': 0.0, 'out': 0.0, 'cache': 0.0, 'cost': 0.0})

        for sid, model, inp, outp, cr in usage:
            n_w = win_msgs.get(sid, 0)
            n_all = mtotal.get(sid, 0)
            if not n_all or not n_w:
                continue
            scale = n_w / n_all            # 会话跨窗口时只计窗口内的份额
            p = peak.get(sid, 0) / n_w     # 高峰消息占比
            r = RATES['pro' if 'pro' in (model or '').lower() else 'flash']
            ri = r['inp'][0] + (r['inp'][1] - r['inp'][0]) * p
            rc = r['cache'][0] + (r['cache'][1] - r['cache'][0]) * p
            ro = r['out'][0] + (r['out'][1] - r['out'][0]) * p
            c_s = (inp * ri + cr * rc + outp * ro) / 1e6   # 元
            for (s, d), c in msg_days.items():
                if s != sid:
                    continue
                w = c / n_w
                daily[d]['use'] += (inp + outp) * scale * w
                daily[d]['cache'] += cr * scale * w
                daily[d]['cost'] += c_s * scale * w
            tot['inp'] += inp * scale
            tot['out'] += outp * scale
            tot['cache'] += cr * scale
            tot['cost'] += c_s * scale
            mm = models[model]
            mm['inp'] += inp * scale
            mm['out'] += outp * scale
            mm['cache'] += cr * scale
            mm['cost'] += c_s * scale

        res['daily'] = {d: {k: round(v, 2) for k, v in daily[d].items()} for d in sorted(daily)}
        res['tot'] = {k: round(v, 2) for k, v in tot.items()}
        res['models'] = {m: {k: round(v, 2) for k, v in vv.items()} for m, vv in models.items()}
        res['sessions_total'] = con.execute('select count(*) from sessions').fetchone()[0]
        res['ok'] = True
    except Exception as e:
        res['error'] = repr(e)
    return res


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('用法: python3 usage_stats.py <state.db> <窗口起始YYYY-MM-DD>')
        sys.exit(2)
    print(json.dumps(summarize(sys.argv[1], sys.argv[2]), ensure_ascii=False))
