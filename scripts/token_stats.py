#!/usr/bin/env python3
"""Hermes token 用量 & 费用统计
用法: python3 token_stats.py [--db /data/hermes/.hermes/state.db] [--month 2026-09] [--chart out.png]
口径(已核对 Hermes agent/usage_pricing.py):
  input_tokens      = 缓存未命中输入
  cache_read_tokens = 缓存命中输入
  output_tokens     = completion_tokens（已含推理）
  用量 = input + output；缓存命中单列
按天归属: Hermes 按会话记账, 用「当天消息条数」为权重把会话用量分摊到天。
计价: DeepSeek 官方人民币价, 高峰=工作日9-12/14-18, 其余半价, 按消息时间分布做峰谷混合。
"""
import sqlite3, datetime, collections, argparse

RATE = {  # 元 / 百万 token  (高峰价; 空闲=一半)
    "deepseek-v4-pro":   {"hit":0.30, "miss":9.0, "out":27.0},
    "deepseek-v4-flash": {"hit":0.04, "miss":2.0, "out":8.0},
    "deepseek-flash":    {"hit":0.04, "miss":2.0, "out":8.0},
}
TZ = datetime.timezone(datetime.timedelta(hours=8))

def day(ts): return datetime.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d")
def is_peak(ts):
    t = datetime.datetime.fromtimestamp(ts, TZ)
    if t.weekday() >= 5: return False
    h = t.hour + t.minute/60
    return (9 <= h < 12) or (14 <= h < 18)

def collect(db, month):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True); cur = con.cursor()
    msg = collections.defaultdict(collections.Counter)
    pk  = collections.defaultdict(collections.Counter)
    of  = collections.defaultdict(collections.Counter)
    for sid, ts in cur.execute("SELECT session_id, timestamp FROM messages"):
        msg[sid][day(ts)] += 1
        (pk if is_peak(ts) else of)[sid][day(ts)] += 1
    per = collections.defaultdict(lambda: collections.Counter())
    q = ("SELECT session_id,model,task,input_tokens,output_tokens,cache_read_tokens,"
         "reasoning_tokens,first_seen,last_seen FROM session_model_usage")
    for sid, model, task, i, o, cr, reas, fs, ls in cur.execute(q):
        if not fs or model not in RATE: continue
        c = datetime.datetime.strptime(day(fs), "%Y-%m-%d")
        e = datetime.datetime.strptime(day(ls), "%Y-%m-%d")
        ds = []
        while c <= e: ds.append(c.strftime("%Y-%m-%d")); c += datetime.timedelta(days=1)
        w = {d: msg[sid].get(d, 0) for d in ds}
        if sum(w.values()) == 0: w = {d: 1 for d in ds}
        tw = sum(w.values()); R = RATE[model]
        for d in ds:
            p, q2 = pk[sid].get(d, 0), of[sid].get(d, 0)
            bl = (p/(p+q2)) + (1 - p/(p+q2))*0.5 if (p+q2) else 0.75
            f = w[d]/tw
            per[d]["in"]    += (i or 0)*f
            per[d]["out"]   += (o or 0)*f
            per[d]["cache"] += (cr or 0)*f
            per[d]["cost"]  += ((i or 0)/1e6*R["miss"] + (cr or 0)/1e6*R["hit"] + (o or 0)/1e6*R["out"])*f*bl
    return {d: per[d] for d in per if d.startswith(month)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/data/hermes/.hermes/state.db")
    ap.add_argument("--month", default=datetime.datetime.now(TZ).strftime("%Y-%m"))
    ap.add_argument("--chart")   # 可选: 输出 PNG 路径
    a = ap.parse_args()
    data = collect(a.db, a.month)
    tu = sum(int(v["in"]+v["out"]) for v in data.values())
    tc = sum(int(v["cache"]) for v in data.values())
    ty = sum(v["cost"] for v in data.values())
    print("日期        用量(入+出)  缓存命中    费用")
    for d in sorted(data):
        v = data[d]
        print("  %s  %-11s %-11s ¥%.2f" % (d, format(int(v["in"]+v["out"]), ","), format(int(v["cache"]), ","), v["cost"]))
    print("合计: 用量 %s | 缓存 %s | ¥%.2f" % (format(tu, ","), format(tc, ","), ty))
    if a.chart:
        make_chart(sorted(data), data, a.chart)

def make_chart(days, data, path):
    from PIL import Image, ImageDraw, ImageFont
    import glob
    fp = None
    for c in ["/tmp/fonts/SimHei.ttf"] + glob.glob("/usr/share/fonts/**/*.tt[fc]", recursive=True):
        if os.path.exists(c): fp = c; break
    F = lambda s: ImageFont.truetype(fp, s) if fp else ImageFont.load_default()
    W, H = 1240, 900
    im = Image.new("RGB", (W, H), "white"); dr = ImageDraw.Draw(im)
    def fmt(v): return "%.1f万" % (v/10000) if v >= 10000 else "%d" % v
    P = [(50, 60, W-50, 400, "token 用量(输入+输出)", (37,99,235)), (50, 480, W-50, 820, "缓存命中", (217,119,6))]
    for (x0,y0,x1,y1,title,col) in P:
        vals = [int(data[d]["in"]+data[d]["out"]) for d in days] if "用量" in title else [int(data[d]["cache"]) for d in days]
        mv = max(vals) or 1
        dr.text((x0, y0-30), title, font=F(20), fill=col)
        st = (x1-x0)/(len(vals)-1)
        pts = [(x0+i*st, y1-(v/mv)*(y1-y0)) for i, v in enumerate(vals)]
        dr.line(pts, fill=col, width=3)
        for (px,py),v,d in zip(pts, vals, days):
            dr.ellipse([px-4,py-4,px+4,py+4], fill="white", outline=col, width=3)
            dr.text((px, py-15), fmt(v), font=F(14), fill=col, anchor="mb")
            dr.text((px, y1+8), d[5:], font=F(14), fill=(90,90,90), anchor="ma")
    im.save(path)

if __name__ == "__main__":
    import os; main()
