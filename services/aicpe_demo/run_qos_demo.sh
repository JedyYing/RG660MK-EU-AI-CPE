#!/bin/sh
# ─────────────────────────────────────────────────────────────────────
# RG660MK QoS 演示 · 设备侧一键：三流 QoS 仿真（off/on 对比）+ 出报告
#   约 60 秒；结果落 deliver_qos/（桌面「RG660MK-QoS演示」ssh 调用后 scp 取回）。
#   隔离路径 lo 上复现，不触碰 5G(ccmni*)/br-lan/eth。
# ─────────────────────────────────────────────────────────────────────
D=/data/ai_cpe/hermes/home/aicpe_demo
cd "$D" 2>/dev/null || exit 1
OUT="$D/deliver_qos"
rm -rf "$OUT"; mkdir -p "$OUT"

# 清理残留仿真进程（防端口占用导致 Address in use）
for p in $(pgrep -f "[q]os_sim"); do kill "$p" 2>/dev/null; done
sleep 1

echo "[1/3] 三流 QoS 仿真（未启用 vs 启用，2 路视频，约 60 秒）…"
/data/hermes/venv/bin/python qos_sim.py --qos both --streams 2 --dur 18 --ext-ping 223.5.5.5 \
    --json "$OUT/qos_sim.json" > "$OUT/qos_sim.log" 2>&1
RC=$?
tail -40 "$OUT/qos_sim.log" 2>/dev/null
if [ "$RC" != "0" ] || [ ! -s "$OUT/qos_sim.json" ]; then
    echo "✗ 仿真失败（rc=$RC）——详见 deliver_qos/qos_sim.log"; exit 1
fi

echo "[2/3] 生成对比报告…"
/data/hermes/venv/bin/python - "$OUT" <<'PY'
import json, sys, os
out = sys.argv[1]
d = json.load(open(os.path.join(out, "qos_sim.json")))
runs = d.get("runs", [])

lines = []
lines.append("# RG660MK 三流 QoS 仿真对比（设备侧 · 隔离复现）")
lines.append("")
lines.append("- 时间：%s" % d.get("ts", "?"))
lines.append("- 链路：%s · 视频 %s 路 · 单场景 %ss · 路径 = lo（不触碰生产接口）" % (d.get("bw"), d.get("streams"), d.get("dur")))
lines.append("")
lines.append("| 场景 | 视频合计 Mbit/s | 音频抖动 P95 ms | 控制RTT 中位 ms | 控制RTT P95 ms | 控制丢包 % |")
lines.append("|---|---|---|---|---|---|")
for r in runs:
    lines.append("| QoS %s | %s | %s | %s | %s | %s |" % (
        r.get("qos"), r.get("视频合计Mbit/s"), r.get("音频抖动P95_ms"),
        r.get("控制RTT中位_ms"), r.get("控制RTT_P95_ms"), r.get("控制丢包率%")))
v = d.get("verdict") or {}
lines.append("")
lines.append("**判据结论**：%s" % json.dumps(v, ensure_ascii=False))
open(os.path.join(out, "报告.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))

# 简易对比图（PIL；失败不影响主流程）
try:
    from PIL import Image, ImageDraw
    W, H = 900, 520
    img = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    off = runs[0] if runs and runs[0].get("qos") == "未启用" else None
    on = runs[-1] if runs and runs[-1].get("qos") == "启用" else None
    if off and on:
        metrics = [
            ("Ctrl RTT med (ms)", off.get("控制RTT中位_ms") or 0, on.get("控制RTT中位_ms") or 0),
            ("Ctrl RTT P95 (ms)", off.get("控制RTT_P95_ms") or 0, on.get("控制RTT_P95_ms") or 0),
            ("Audio jitter P95 (ms)", off.get("音频抖动P95_ms") or 0, on.get("音频抖动P95_ms") or 0),
            ("Video Mbit/s", off.get("视频合计Mbit/s") or 0, on.get("视频合计Mbit/s") or 0),
        ]
        dr.text((30, 18), "RG660MK three-stream QoS: OFF vs ON (isolated lo path)", fill=(0, 0, 0))
        x0, y0, bh, bw, gap = 60, 90, 300, 90, 150
        maxv = max(max(a, b) for _, a, b in metrics) or 1
        for i, (name, a, b) in enumerate(metrics):
            x = x0 + i * (bw * 2 + gap)
            ha = int(bh * a / maxv); hb = int(bh * b / maxv)
            dr.rectangle([x, y0 + bh - ha, x + bw, y0 + bh], fill=(214, 86, 72))
            dr.rectangle([x + bw + 12, y0 + bh - hb, x + 2 * bw + 12, y0 + bh], fill=(76, 168, 96))
            dr.text((x, y0 + bh - ha - 16), str(a), fill=(160, 40, 30))
            dr.text((x + bw + 12, y0 + bh - hb - 16), str(b), fill=(30, 120, 50))
            dr.text((x, y0 + bh + 8), name, fill=(0, 0, 0))
        dr.text((x0, y0 + bh + 34), "red = QoS OFF   green = QoS ON", fill=(80, 80, 80))
        img.save(os.path.join(out, "QoS对比图.png"))
        print("已生成 QoS对比图.png")
except Exception as e:
    print("chart skipped: %s" % e)
PY

echo "[3/3] 完成 → $OUT"
ls -la "$OUT"
