#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计【设备 RG660MK】最近一天的真实 token 用量,并按 DeepSeek 价格折算成人民币。

用法:
  python3 rg660mk_token_cost.py [--minutes N] [--host H] [--peak|--offpeak] [--write-report]

数据源(改版):不再统计 QRIBuddy 对话,而是 SSH 登录设备,只读打开 hermes 本地状态库
  /data/hermes/.hermes/state.db(SQLite),聚合 session_model_usage 表里 last_seen 落在
  最近 N 分钟(默认 1440=一天)内的真实 token 用量(输入/输出/缓存读/缓存写分档)。
  这是设备上 AI 服务(语音助手/Hermes 代理等)调用大模型的实际消耗。
  注:hermes 用量归因参考设备内 hermes-usage-analytics skill 的方法(只读打开、按 usage 表聚合)。

计价口径(DeepSeek 官方 api-docs.deepseek.com/quick_start/pricing,2026-09 核对):
  模型 deepseek-flash (DeepSeek-V4.1-Flash),美元 / 每百万 token:
    输入·缓存未命中  峰 $0.30 / 谷 $0.15
    输入·缓存命中    峰 $0.006 / 谷 $0.003
    输出            峰 $1.20 / 谷 $0.60
  峰时段: 周一至周五 UTC 01:00-04:00 与 06:00-10:00;其余为谷时(谷价=峰价一半)。
  缓存写入 DeepSeek 不单独计价,按输入·缓存未命中价计。
  USD->CNY 固定汇率 7.2(如需改单价/汇率/模型,改下方常量即可)。
  设备实测模型为 deepseek-v4-flash,与本计价口径一致。
"""
import json, glob, os, sys, warnings
warnings.filterwarnings("ignore")  # 屏蔽 paramiko 的 Blowfish 弃用警告等噪声
from datetime import datetime, timezone

# ---- 设备连接参数(与 rg660mk_check.py 一致) ----
HOST = "192.168.1.1"
PORT = 22
USER = "root"
PASS = "oelinux123"
STATE_DB = "/data/hermes/.hermes/state.db"

# ---- DeepSeek deepseek-flash 计价(每百万 token 的美元单价,峰价) ----
PRICE_USD_PEAK = {
    'input': 0.30,        # 缓存未命中输入
    'cache_read': 0.006,  # 缓存命中输入
    'output': 1.20,       # 输出
}
PRICE_USD_PEAK['cache_write'] = PRICE_USD_PEAK['input']
USD_TO_CNY = 7.2

REPORT_DIR = os.path.dirname(os.path.abspath(__file__))


def latest_report():
    files = glob.glob(os.path.join(REPORT_DIR, 'rg660mk_巡检报告_*.txt'))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def is_peak(now_utc):
    """DeepSeek 峰时:周一至周五 UTC 01:00-04:00 与 06:00-10:00。"""
    if now_utc.weekday() >= 5:
        return False
    h = now_utc.hour
    return (1 <= h < 4) or (6 <= h < 10)


# 在设备上执行的取数脚本:只读打开 state.db,聚合 last_seen 落在最近 N 秒内的用量。
# 以 SUMMARY 行输出,避免依赖设备端有无额外库。
_REMOTE_PY = r'''
import sqlite3, time, json, sys
win = int(sys.argv[1])
now = int(time.time()); since = now - win
try:
    con = sqlite3.connect("file:%s?mode=ro", uri=True)
except Exception as e:
    print("DBERR " + str(e)); raise SystemExit(0)
tot = {"input":0,"output":0,"cache_read":0,"cache_write":0}
models = set(); rows = 0; calls = 0
try:
    cur = con.execute(
        "SELECT model,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,api_call_count "
        "FROM session_model_usage WHERE last_seen>=?", (since,))
    for m,i,o,r,w,ac in cur:
        rows += 1
        tot["input"]+= i or 0; tot["output"]+= o or 0
        tot["cache_read"]+= r or 0; tot["cache_write"]+= w or 0
        calls += ac or 0
        if m: models.add(m)
except Exception as e:
    print("QERR " + str(e)); raise SystemExit(0)
print("SUMMARY " + json.dumps({"rows":rows,"api_calls":calls,
    "models":sorted(models),"tokens":tot}))
''' % STATE_DB


def fetch_from_device(host, minutes):
    """SSH 到设备跑取数脚本,返回 dict 或 None(连不上/无数据)。"""
    try:
        import paramiko, warnings
        warnings.filterwarnings("ignore")
    except ImportError:
        print("缺少 paramiko,无法连设备取数: sudo apt install python3-paramiko")
        return None
    win = minutes * 60
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(host, port=PORT, username=USER, password=PASS, timeout=10,
                    allow_agent=False, look_for_keys=False, banner_timeout=15)
    except Exception as e:
        print("SSH 连接设备失败: %s" % e)
        return None
    try:
        # 用 python3 -c 传入远程脚本;窗口秒数作为 argv[1]
        cmd = "python3 -c %s %d" % (_shq(_REMOTE_PY), win)
        si, so, se = cli.exec_command(cmd, timeout=40)
        out = so.read().decode("utf-8", "replace") + se.read().decode("utf-8", "replace")
    finally:
        cli.close()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("SUMMARY "):
            return json.loads(line[len("SUMMARY "):])
        if line.startswith("DBERR") or line.startswith("QERR"):
            print("设备取数出错: %s" % line)
            return None
    print("设备未返回用量数据,原始输出: %s" % out[:200])
    return None


def _shq(s):
    """把多行脚本安全包成单引号参数。"""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main():
    minutes = 1440  # 默认统计最近一天(24 小时)
    host = HOST
    if '--minutes' in sys.argv:
        try:
            minutes = int(sys.argv[sys.argv.index('--minutes') + 1])
        except Exception:
            pass
    if '--host' in sys.argv:
        try:
            host = sys.argv[sys.argv.index('--host') + 1]
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

    data = fetch_from_device(host, minutes)
    if data is None:
        print("=== RG660MK 设备 token 用量统计:取数失败 ===")
        print("未能从设备 %s 的 hermes state.db 取到用量(设备离线/库不存在/无 paramiko)。" % host)
        sys.exit(0)

    tot = data['tokens']
    cost_usd = {k: tot[k] / 1_000_000 * price[k] for k in tot}
    total_usd = sum(cost_usd.values())
    total_cny = total_usd * USD_TO_CNY
    total_tok = sum(tot.values())
    days = minutes / 1440.0
    win_desc = ("%.0f 分钟" % minutes) if minutes < 1440 else ("%.1f 天" % days)

    print('=== RG660MK 设备最近%s token 用量与成本(DeepSeek 计价)===' % win_desc)
    print('数据源: 设备 %s hermes state.db(真实 AI 服务用量)' % host)
    print('统计窗口: 最近 %d 分钟 | 命中用量记录 %d 条 | API 调用 %d 次'
          % (minutes, data['rows'], data.get('api_calls', 0)))
    print('设备模型: %s' % (', '.join(data['models']) if data['models'] else '未知'))
    print('计价档: deepseek-flash %s(谷价=峰价一半)' % ('峰时' if peak else '谷时'))
    print('token 分档:')
    print('  输入(缓存未命中) %10d  -> $%.5f' % (tot['input'], cost_usd['input']))
    print('  输出              %10d  -> $%.5f' % (tot['output'], cost_usd['output']))
    print('  缓存读取(命中)   %10d  -> $%.5f' % (tot['cache_read'], cost_usd['cache_read']))
    print('  缓存写入          %10d  -> $%.5f' % (tot['cache_write'], cost_usd['cache_write']))
    print('  合计 token        %10d' % total_tok)
    print('成本: $%.5f  ×汇率%.1f  ≈  ￥%.4f' % (total_usd, USD_TO_CNY, total_cny))
    print('(单价口径: DeepSeek deepseek-flash 挂牌价)')

    print('SUMMARY_JSON ' + json.dumps({
        'source': 'device_state_db',
        'host': host,
        'window_minutes': minutes,
        'usage_rows': data['rows'],
        'api_calls': data.get('api_calls', 0),
        'models': data['models'],
        'pricing_model': 'deepseek-flash',
        'peak': peak,
        'tokens': tot,
        'total_tokens': total_tok,
        'cost_usd': round(total_usd, 5),
        'cost_cny': round(total_cny, 4),
        'usd_to_cny': USD_TO_CNY,
    }, ensure_ascii=False))

    if '--write-report' in sys.argv:
        rpt = latest_report()
        if rpt:
            block = (
                '\n' + '=' * 60 + '\n'
                '  设备最近%s Token 用量与成本(DeepSeek deepseek-flash 计价)\n' % win_desc
                + '=' * 60 + '\n'
                '  数据源: 设备 hermes state.db(真实 AI 服务用量,非巡检脚本本身)\n'
                + '  统计窗口: 最近 %d 分钟 | 用量记录 %d 条 | API 调用 %d 次\n'
                  % (minutes, data['rows'], data.get('api_calls', 0))
                + '  设备模型: %s | 计价档: %s\n'
                  % (', '.join(data['models']) or '未知', '峰时' if peak else '谷时')
                + '  输入(缓存未命中) %10d\n' % tot['input']
                + '  输出              %10d\n' % tot['output']
                + '  缓存读取(命中)   %10d\n' % tot['cache_read']
                + '  缓存写入          %10d\n' % tot['cache_write']
                + '  合计 token        %10d\n' % total_tok
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
