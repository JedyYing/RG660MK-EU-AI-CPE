#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""估算本次巡检运行的 token 用量,并按 DeepSeek 价格折算成人民币。

用法:
  python3 rg660mk_token_cost.py [--minutes N] [--peak|--offpeak]

原理:巡检脚本本身(SSH 采集)不消耗 token,消耗来自 QRIBuddy 读结果/写结论/发飞书这一轮对话。
本脚本扫描 QRIBuddy 会话 transcript(claude-config/projects/*/*.jsonl),
汇总最近 N 分钟内(默认 15)所有 assistant 消息的 usage,按 DeepSeek 分档单价折算 RMB。
因每日巡检每天只跑一次、几分钟内完成,时间窗内的用量即本次运行的用量。

计价口径(DeepSeek 官方 api-docs.deepseek.com/quick_start/pricing,2026-09 核对):
  模型 deepseek-flash (DeepSeek-V4.1-Flash),美元 / 每百万 token:
    输入·缓存未命中  峰 $0.30 / 谷 $0.15
    输入·缓存命中    峰 $0.006 / 谷 $0.003
    输出            峰 $1.20 / 谷 $0.60
  峰时段: 周一至周五 UTC 01:00-04:00 与 06:00-10:00;其余为谷时(谷价=峰价一半)。
  缓存写入 DeepSeek 不单独计价,按输入·缓存未命中价计。
  USD->CNY 固定汇率 7.2(如需改单价/汇率/模型,改下方常量即可)。
注:实际运行的是内部模型,此处按 DeepSeek deepseek-flash 挂牌价折算,仅供参考。
"""
import json, glob, os, sys
from datetime import datetime, timezone, timedelta

# ---- DeepSeek deepseek-flash 计价(每百万 token 的美元单价,峰价) ----
# 谷价 = 峰价 / 2,由脚本按运行时刻或 --peak/--offpeak 自动选择。
PRICE_USD_PEAK = {
    'input': 0.30,        # 缓存未命中输入
    'cache_read': 0.006,  # 缓存命中输入
    'output': 1.20,       # 输出
}
# 缓存写入按输入·缓存未命中价计(DeepSeek 不单独对缓存写入收费)
PRICE_USD_PEAK['cache_write'] = PRICE_USD_PEAK['input']
USD_TO_CNY = 7.2

PROJECTS_GLOB = os.path.expanduser(
    '~/.config/QRIBuddy/claude-config/projects/*/*.jsonl')

# 巡检报告落盘目录(与本脚本同目录)
REPORT_DIR = os.path.dirname(os.path.abspath(__file__))


def latest_report():
    """返回构建资源目录下最新一份巡检报告的完整路径,没有则返回 None。"""
    files = glob.glob(os.path.join(REPORT_DIR, 'rg660mk_巡检报告_*.txt'))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def is_peak(now_utc):
    """DeepSeek 峰时:周一至周五 UTC 01:00-04:00 与 06:00-10:00。"""
    if now_utc.weekday() >= 5:  # 周六=5 周日=6,全天谷时
        return False
    h = now_utc.hour
    return (1 <= h < 4) or (6 <= h < 10)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


def main():
    minutes = 15
    if '--minutes' in sys.argv:
        try:
            minutes = int(sys.argv[sys.argv.index('--minutes') + 1])
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    if '--peak' in sys.argv:
        peak = True
    elif '--offpeak' in sys.argv:
        peak = False
    else:
        peak = is_peak(now)
    factor = 1.0 if peak else 0.5
    price = {k: v * factor for k, v in PRICE_USD_PEAK.items()}

    cutoff = now - timedelta(minutes=minutes)
    tot = {'input': 0, 'output': 0, 'cache_write': 0, 'cache_read': 0}
    n = 0
    models = set()

    for f in glob.glob(PROJECTS_GLOB):
        try:
            if datetime.fromtimestamp(os.path.getmtime(f), timezone.utc) < cutoff:
                continue
        except Exception:
            continue
        try:
            fh = open(f, encoding='utf-8')
        except Exception:
            continue
        with fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get('type') != 'assistant':
                    continue
                ts = parse_ts(o.get('timestamp'))
                if ts is None or ts < cutoff:
                    continue
                m = o.get('message', {})
                u = m.get('usage')
                if not u:
                    continue
                n += 1
                if m.get('model'):
                    models.add(m['model'])
                tot['input'] += u.get('input_tokens', 0)
                tot['output'] += u.get('output_tokens', 0)
                tot['cache_write'] += u.get('cache_creation_input_tokens', 0)
                tot['cache_read'] += u.get('cache_read_input_tokens', 0)

    cost_usd = {k: tot[k] / 1_000_000 * price[k] for k in tot}
    total_usd = sum(cost_usd.values())
    total_cny = total_usd * USD_TO_CNY
    total_tok = sum(tot.values())

    print('=== RG660MK 巡检 token 用量与成本估算(DeepSeek 计价)===')
    print('统计窗口: 最近 %d 分钟 | 命中 assistant 消息 %d 条' % (minutes, n))
    print('实际模型: %s' % (', '.join(sorted(models)) if models else '未知'))
    print('计价档: deepseek-flash %s(谷价=峰价一半)' % ('峰时' if peak else '谷时'))
    print('token 分档:')
    print('  输入(缓存未命中) %9d  -> $%.5f' % (tot['input'], cost_usd['input']))
    print('  输出              %9d  -> $%.5f' % (tot['output'], cost_usd['output']))
    print('  缓存写入          %9d  -> $%.5f' % (tot['cache_write'], cost_usd['cache_write']))
    print('  缓存读取(命中)   %9d  -> $%.5f' % (tot['cache_read'], cost_usd['cache_read']))
    print('  合计 token        %9d' % total_tok)
    print('成本: $%.5f  ×汇率%.1f  ≈  ￥%.4f' % (total_usd, USD_TO_CNY, total_cny))
    print('(单价口径: DeepSeek deepseek-flash 挂牌价,仅供参考)')

    print('SUMMARY_JSON ' + json.dumps({
        'window_minutes': minutes,
        'messages': n,
        'pricing_model': 'deepseek-flash',
        'peak': peak,
        'tokens': tot,
        'total_tokens': total_tok,
        'cost_usd': round(total_usd, 5),
        'cost_cny': round(total_cny, 4),
        'usd_to_cny': USD_TO_CNY,
    }, ensure_ascii=False))

    # 追加写进最新一份巡检报告(--write-report),让报告里也带上 token 与人民币折算
    if '--write-report' in sys.argv:
        rpt = latest_report()
        if rpt:
            block = (
                '\n' + '=' * 60 + '\n'
                '  Token 用量与成本估算(DeepSeek deepseek-flash 计价,仅供参考)\n'
                + '=' * 60 + '\n'
                '  统计窗口: 最近 %d 分钟内的 QRIBuddy 对话用量\n' % minutes
                + '  计价档: %s(每天 7:00 北京时间属谷时,享半价)\n' % ('峰时' if peak else '谷时')
                + '  输入(缓存未命中) %9d\n' % tot['input']
                + '  输出              %9d\n' % tot['output']
                + '  缓存写入          %9d\n' % tot['cache_write']
                + '  缓存读取(命中)   %9d\n' % tot['cache_read']
                + '  合计 token        %9d\n' % total_tok
                + '  成本: $%.5f  ×汇率%.1f  ≈  ￥%.4f\n' % (total_usd, USD_TO_CNY, total_cny)
            )
            try:
                with open(rpt, 'a', encoding='utf-8') as w:
                    w.write(block)
                print('已追加写入报告: %s' % os.path.basename(rpt))
            except Exception as e:
                print('写入报告失败: %s' % e)
        else:
            print('未找到巡检报告文件,跳过写入')


if __name__ == '__main__':
    main()
