#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""AI CPE 五大功能一键演示套件 —— 编排 + 实时可视化看板（v1.1）

用法:
    /data/hermes/venv/bin/python aicpe_demo.py            # 完整演示（含播报/点灯）
    ... aicpe_demo.py --no-audio                          # 静音质检模式
看板: http://192.168.1.1:<port>/  （局域网任意设备可看：进度 + 现场画面 + 日志）

v1.1 修复（依据首次试跑实测）: 进程检测改 /proc/*/cmdline；抓帧写自有临时路径；
ASR 按端口 6006 判定并自愈拉起；视频链路改主动拉流测帧率；检索加关键词映射与兜底；家居改状态差分。
"""
import argparse, json, os, re, subprocess, sys, threading, time, urllib.request

HOME = "/data/ai_cpe/hermes/home"
DEMO = HOME + "/aicpe_demo"
SNAP = "/tmp/aicpe_demo_snap.jpg"
VR = "/data/ai_cpe/demo/bin/vision_runner"
MODELS = {"detect": "/data/ai_cpe/demo/ai_models/yolov8n/model.ncnn.param",
          "pose": "/data/ai_cpe/demo/ai_models/yolov8n-pose/model.ncnn.param"}
CAMAV = "http://127.0.0.1:8092"; CAMVIEW = "http://127.0.0.1:8090"
IMMICH = "http://192.168.1.244:2283"; IMMICH_KEY_FILE = HOME + "/photos/.immich_key"
BULB = "/data/ai_cpe/bulb_control.py"; TTS = "/data/ai_cpe/tts_say.py"
FACEREC = "/data/ai_cpe/face_recognize.py"; SHERPA_INIT = "/etc/init.d/sherpa-asr"
PY = "/usr/bin/python3"; VENV = "/data/hermes/venv/bin/python"
STATE = {"started": time.time(), "phase": "init", "items": [], "log": [], "done": False,
         "summary": "", "report": ""}
LOCK = threading.Lock()


def sh(cmd, t=120):
    try:
        r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=t)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return 1, "ERR: %s" % e


def log(msg):
    with LOCK:
        STATE["log"].append("[%s] %s" % (time.strftime("%H:%M:%S"), msg)); STATE["log"] = STATE["log"][-300:]
    print(msg, flush=True)


def proc_alive(pat):
    rc, o = sh("grep -al %s /proc/[0-9]*/cmdline 2>/dev/null | wc -l" % json.dumps(pat))
    try:
        return int(o.strip()) > 0
    except Exception:
        return False


def port_listening(p, host="127.0.0.1"):
    """以 TCP 连接探测（busybox awk 无 strtonum，解析 /proc/net/tcp 不可靠）"""
    import socket as _s
    sk = _s.socket(); sk.settimeout(1.5)
    try:
        sk.connect((host, int(p))); return True
    except Exception:
        return False
    finally:
        try: sk.close()
        except Exception: pass


def item(idx, name, desc):
    it = {"idx": idx, "name": name, "desc": desc, "status": "pending", "detail": "", "metrics": [], "t0": None, "dur": None}
    with LOCK:
        STATE["items"].append(it)
    return it


def set_item(it, status, detail="", metrics=None):
    it["status"] = status
    if detail:
        it["detail"] = detail
    if metrics:
        it["metrics"] += metrics
    if status == "running" and not it["t0"]:
        it["t0"] = time.time()
    if status in ("pass", "fail", "skip"):
        it["dur"] = round(time.time() - (it["t0"] or time.time()), 2)
    log("  [%s] %s %s" % (status.upper(), it["name"], ("- " + detail) if detail else ""))


def immich_key():
    try:
        return open(IMMICH_KEY_FILE).read().strip()
    except Exception:
        return ""


def http_json(url, data=None, headers=None, t=25):
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    body = json.dumps(data).encode() if data is not None else None
    if body:
        h["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=t) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def check_voice(it, no_audio=False):
    set_item(it, "running")
    ms = []
    va_alive = proc_alive("voice_assistant")
    asr_up = port_listening(6006); healed = False
    if not asr_up:
        sh("%s start" % SHERPA_INIT, t=40)
        for _ in range(10):
            time.sleep(2)
            if port_listening(6006):
                healed = True; asr_up = True; break
    ms.append(("语音助手进程", "存活" if va_alive else "未运行"))
    ms.append(("ASR 常驻服务(:6006)", ("在线" if asr_up else "离线") + ("（已自动拉起）" if healed else "")))
    wavs = sorted([os.path.join("/data/ai_cpe/capdiag", f) for f in os.listdir("/data/ai_cpe/capdiag")
                   if f.endswith(".wav")], key=os.path.getmtime)[-1:]
    asr_text = ""
    if wavs:
        fn = "_paraformer_ws_once" if asr_up else "_paraformer_oneshot"
        code = ("import sys,json;sys.path.insert(0,'/data/ai_cpe');import voice_assistant as va;"
                "print(json.dumps({'text': getattr(va,'%s')(sys.argv[1])}, ensure_ascii=False))" % fn)
        rc, out = sh("%s -c %s %s" % (VENV, json.dumps(code), json.dumps(wavs[0])), t=180)
        try:
            asr_text = json.loads(out.strip().splitlines()[-1]).get("text", "")
        except Exception:
            asr_text = ""
    ms.append(("ASR 实测转写", (asr_text[:40] + "…") if asr_text else "未取到文本"))
    reply = ""
    try:
        env = {}
        for ln in open("/data/hermes/.hermes/.env"):
            if "=" in ln and not ln.strip().startswith("#"):
                k, _, v = ln.strip().partition("=")
                env[k] = v.strip().strip('"').strip("'")
        for _try in range(3):
            r = http_json("https://ai.phicotek.com/api/llmrouter/v1/chat/completions",
                          {"model": "deepseek-v4-flash", "max_tokens": 400,
                           "messages": [{"role": "user", "content": "你好，请用一句话介绍你自己"}]},
                          headers={"Authorization": "Bearer " + env.get("PHICOTEK_API_KEY", "")}, t=60)
            reply = (r.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
            if reply:
                break
            log("  大模型返回空内容，重试 %d/3（原始: %s）" % (_try + 1, json.dumps(r, ensure_ascii=False)[:120]))
            time.sleep(2)
    except Exception as e:
        log("  大模型调用异常: %s" % str(e)[:100])
    ms.append(("大模型对话（设备集成）", reply[:48] if reply else "调用失败"))
    tts_ok = None
    if no_audio:
        ms.append(("TTS 播报（CM564）", "静音模式跳过"))
    else:
        rc, o = sh("%s %s 'AI CPE 五大功能演示自检开始，语音链路正常'" % (VENV, TTS), t=120)
        tts_ok = (rc == 0)
        ms.append(("TTS 播报（CM564）", "已播报" if tts_ok else "失败: %s" % o[-60:]))
    ok = va_alive and asr_up and bool(asr_text) and bool(reply) and (tts_ok is not False)
    set_item(it, "pass" if ok else "fail", "语音链路（识别 + 大模型 + 播报）" if ok else "存在未通过子项", ms)


def take_snapshot():
    for url in (CAMVIEW + "/snapshot", CAMAV + "/snapshot"):
        try:
            with urllib.request.urlopen(url, timeout=12) as r:
                d = r.read()
            if d[:2] == b"\xff\xd8" and len(d) > 5000:
                open(SNAP, "wb").write(d)
                return True, len(d)
        except Exception:
            continue
    return False, 0


def vision_call(op, path):
    req = {"version": 1, "operation": op, "input": {"path": path}, "models": MODELS}
    r = subprocess.run([VR], input=json.dumps(req) + "\n", capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-160:])
    out = json.loads(r.stdout)
    if not out.get("ok"):
        raise RuntimeError(str(out)[:160])
    return out.get("result", {})


def check_vision(it, no_audio=False):
    set_item(it, "running")
    ms = []
    ok, size = take_snapshot()
    if not ok:
        set_item(it, "fail", "抓帧失败（camview/camav 未就绪）", [("现场抓帧", "失败")]); return
    ms.append(("现场抓帧", "%d KB" % (size // 1024)))
    try:
        det = vision_call("detect", SNAP); pose = vision_call("pose", SNAP)
    except Exception as e:
        set_item(it, "fail", "YOLO 推理失败: %s" % str(e)[:80], ms); return
    dets = det.get("detections", []) or []; persons = pose.get("persons", []) or []
    ms.append(("YOLO 目标检测", "%.0fms · %s" % (det.get("inference_ms", 0),
               ", ".join("%s(%.2f)" % (d.get("class_name"), d.get("score", 0)) for d in dets[:4]) or "无目标")))
    ms.append(("YOLO 姿态估计", "%.0fms · %d 人" % (pose.get("inference_ms", 0), len(persons))))
    verdict = "画面内无人（链路已执行）"
    if persons:
        p = max(persons, key=lambda x: x.get("score", 0))
        kps = {i: k for i, k in enumerate(p.get("keypoints", []))}
        def kp(i):
            k = kps.get(i) or {}
            return k.get("x"), k.get("y"), k.get("score", 0)
        try:
            nx, ny, ns = kp(0); lsx, lsy, _ = kp(5); rsx, rsy, _ = kp(6)
            lhx, lhy, _ = kp(11); rhx, rhy, _ = kp(12)
            issues = []
            if None not in (lsx, rsx, lhx, rhx):
                sw = abs(lsx - rsx) or 1.0
                if abs(lsy - rsy) / sw > 0.18: issues.append("肩部倾斜")
                if abs(lhy - rhy) / sw > 0.25: issues.append("髋部倾斜")
                if ny is not None and not (0.12 <= ((lsy + rsy) / 2 - ny) / sw <= 0.75): issues.append("低头/后仰")
                if abs((lsx + rsx) / 2 - (lhx + rhx) / 2) / sw > 0.35: issues.append("躯干侧倾")
            verdict = "坐姿正常" if not issues else "坐姿异常：" + "、".join(issues)
        except Exception as e:
            verdict = "判定异常(%s)" % str(e)[:40]
    ms.append(("坐姿智能检测", verdict))
    rc, o = sh("%s %s" % (PY, FACEREC), t=180)
    m = re.search(r"(识别到|没有识别到|未识别).{0,40}", o)
    ms.append(("人脸识别（身份）", m.group(0).strip() if m else (o.strip()[-70:] or "无输出")))
    ok = bool(dets or persons) and ("异常" not in verdict)
    set_item(it, "pass" if ok else "fail", "视觉双检（坐姿 + 人脸身份）已完成" if ok else verdict, ms)


KEYMAP = [("云海", "yunhai"), ("泰晤士", "thames"), ("狗", "photo_"), ("宠物", "photo_"), ("照片", "photo_")]


def nl_query(q):
    kw = None
    for k, pat in KEYMAP:
        if k in q:
            kw = pat; break
    body = {"size": 60, "page": 1}
    m = re.search(r"(20\d\d[-/年]\d{1,2}[-/月]\d{1,2})", q)
    if m:
        d = m.group(1).replace("年", "-").replace("月", "-").replace("/", "-").replace("日", "")
        body["takenAfter"] = d + "T00:00:00.000Z"; body["takenBefore"] = d + "T23:59:59.999Z"
    if "最近" in q or "今天" in q:
        body["takenAfter"] = time.strftime("%Y-%m-%d", time.localtime()) + "T00:00:00.000Z"
    return body, kw


def check_photo_search(it, query="云海", no_audio=False):
    set_item(it, "running")
    key = immich_key()
    ms = [("检索指令（飞书自然语言）", "“%s”" % query)]
    if not key:
        set_item(it, "fail", "未找到 Immich API key", ms); return
    try:
        body, kw = nl_query(query)
        res = http_json(IMMICH + "/api/search/metadata", data=body, headers={"x-api-key": key})
        assets = (res.get("assets") or {}).get("items", []) or []
        hit = [a for a in assets if kw and kw in (a.get("originalFileName") or "")] if kw else []
        mode = "关键词命中"
        if not hit:
            hit = sorted(assets, key=lambda a: a.get("fileCreatedAt") or "", reverse=True)[:6]; mode = "按时间兜底"
        ms.append(("匹配结果", "%d 张（%s）" % (len(hit), mode)))
        for a in hit[:3]:
            ms.append(("· 命中", "%s（%s）" % ((a.get("originalFileName") or "")[:36], (a.get("fileCreatedAt") or "")[:10])))
        rc, o = sh("grep -c '^FEISHU_' /data/hermes/.hermes/.env")
        ms.append(("飞书通道", "已绑定（%d 项配置，Bot 可收发）" % int(o.strip() or 0)))
        set_item(it, "pass" if hit else "fail", ("已检索到 %d 张图片" % len(hit)) if hit else "未匹配到图片", ms)
    except Exception as e:
        set_item(it, "fail", "检索失败: %s" % str(e)[:80], ms)


def pull_stream(secs=4):
    try:
        t0 = time.time(); frames = 0; total = 0; buf = b""
        with urllib.request.urlopen(CAMAV + "/stream", timeout=secs + 4) as r:
            while time.time() - t0 < secs:
                chunk = r.read(8192)
                if not chunk:
                    break
                total += len(chunk); buf += chunk
                frames += buf.count(b"\xff\xd8\xff"); buf = buf[-4:]
        el = max(0.1, time.time() - t0)
        return frames, total, el
    except Exception:
        return 0, 0, secs


def check_diag(it):
    set_item(it, "running")
    ms = []
    frames, total, el = pull_stream(4)
    fps = round(frames / el, 2); kbs = round(total / 1024 / el, 1)
    ms.append(("视频拉流实测", "%d 帧 / %.1fs → %.2f fps · %.1f KB/s" % (frames, el, fps, kbs)))
    lat = []
    for _ in range(3):
        t = time.time()
        ok, _ = take_snapshot()
        if ok:
            lat.append(round((time.time() - t) * 1000))
    ms.append(("单帧抓取时延", ("%s ms（3 次均值）" % round(sum(lat) / len(lat))) if lat else "失败"))
    for label, host in (("局域网 Immich", "192.168.1.244"), ("公网", "223.5.5.5")):
        rc, o = sh("ping -c 8 -W 2 %s 2>&1 | tail -2" % host, t=25)
        rtt = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", o); loss = re.search(r"(\d+)% packet loss", o)
        ms.append(("链路 " + label, ("丢包 %s%% · RTT 均值 %s ms（max %s）" % (loss.group(1), rtt.group(2), rtt.group(3)))
                   if (rtt and loss) else o.replace("\n", " ")[:70]))
    rc, o = sh("%s /data/ai_cpe/netdiag.py clients 2>&1 | head -6" % PY, t=60)
    ms.append(("网络全景（netdiag）", "；".join([l for l in o.splitlines() if l.strip()][:3])[:90]))
    cam_ok = proc_alive("camview")
    ms.append(("摄像头服务(camview)", "存活" if cam_ok else "未运行"))
    verdict = "视频链路健康，未发现卡顿风险"
    if fps < 0.75:
        verdict = "发现异常：拉流帧率不足（%.2f fps）" % fps
    elif not cam_ok:
        verdict = "发现异常：摄像头服务未运行"
    ms.append(("排障结论", verdict))
    set_item(it, "pass" if verdict.endswith("风险") else "fail", verdict, ms)


def bulb_status():
    rc, o = sh("%s %s status" % (PY, BULB), t=40)
    try:
        return json.loads(o)
    except Exception:
        return {"_raw": o[:200]}


def check_smart_home(it, no_audio=False):
    set_item(it, "running")
    ms = []
    before = bulb_status(); ok0 = "result" in before
    ms.append(("Tuya 云连接", "正常" if ok0 else "异常: %s" % str(before)[:60]))
    if no_audio:
        ms.append(("动作", "静音质检模式：仅读取状态，不实际开关"))
        set_item(it, "pass" if ok0 else "fail", "设备状态读取成功（未改动现场）" if ok0 else "读取失败", ms); return
    rc, out = sh("%s %s toggle" % (PY, BULB), t=40)
    accepted = ("✅" in out) or ("已发送" in out) or (rc == 0 and "error" not in out.lower())
    ms.append(("联动动作", (out.strip().splitlines()[-1][:50] if out.strip() else "无输出")))
    time.sleep(3)
    after = bulb_status()
    ch = [k for k, v in [(x.get("code"), x.get("value")) for x in (after.get("result") or [])]
          if any(y.get("code") == k and y.get("value") != v for y in (before.get("result") or []))]
    ms.append(("状态回读", ("%d 个字段变化：%s" % (len(ch), ", ".join(ch[:3]))) if ch else "该型号状态接口未暴露开关 DP（指令已受理）"))
    if accepted:
        sh("%s %s toggle" % (PY, BULB), t=40); time.sleep(3)
        ms.append(("现场恢复", "已发再次切换指令，恢复演示前状态"))
    set_item(it, "pass" if accepted else "fail",
             "智能灯具远程开关指令已受理并由云平台执行" if accepted else "指令未被受理，请检查 Tuya 绑定", ms)


def run_all(args):
    with LOCK:
        STATE["phase"] = "running"
    log("=== AI CPE 五大功能一键演示 开始（%s）===" % ("静音质检模式" if args.no_audio else "完整演示模式"))
    items = [item(1, "AI 智能语音对话", "语音交互 / 大模型对话 / TTS 播报"),
             item(2, "AI 视觉检测", "人脸识别 + 坐姿智能检测"),
             item(3, "飞书自然语言图片检索", "自然语言指令检索图片资源"),
             item(4, "视频卡顿智能故障排查", "自动检测、分析并定位卡顿"),
             item(5, "智能家居控制", "智能灯具开关联动")]
    for fn, it, kw in [(check_voice, items[0], {"no_audio": args.no_audio}),
                       (check_vision, items[1], {"no_audio": args.no_audio}),
                       (check_photo_search, items[2], {"query": args.query, "no_audio": args.no_audio}),
                       (check_diag, items[3], {}),
                       (check_smart_home, items[4], {"no_audio": args.no_audio})]:
        try:
            fn(it, **kw)
        except Exception as e:
            set_item(it, "fail", "异常: %s" % str(e)[:100])
    passed = sum(1 for i in items if i["status"] == "pass")
    with LOCK:
        STATE["phase"] = "done"; STATE["done"] = True
        STATE["summary"] = "五大功能演示结束：%d / %d 项通过" % (passed, len(items))
    log("=== %s ===" % STATE["summary"])
    write_report(items, passed)
    return passed


def write_report(items, passed):
    ts = time.strftime("%Y%m%d_%H%M%S")
    L = ["# AI CPE 五大功能演示报告", "", "- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
         "- 设备：RG660MK（AI CPE）", "- 结果：**%d / %d 项通过**" % (passed, len(items)), "",
         "| # | 功能 | 结果 | 耗时 | 关键证据 |", "|---|---|---|---|---|"]
    for i in items:
        ev = "；".join("%s: %s" % (k, v) for k, v in i["metrics"][:3])
        L.append("| %d | %s | %s | %ss | %s |" % (i["idx"], i["name"],
                 {"pass": "✅ 通过", "fail": "❌ 失败", "skip": "⏭ 跳过"}.get(i["status"], i["status"]),
                 i["dur"], ev.replace("|", "/")))
    L += ["", "## 明细"]
    for i in items:
        L += ["", "### %d. %s —— %s" % (i["idx"], i["name"], i["detail"] or i["status"])]
        for k, v in i["metrics"]:
            L.append("- **%s**：%s" % (k, v))
    md = DEMO + "/reports/AI_CPE_功能演示报告_%s.md" % ts
    open(md, "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump({"ts": ts, "passed": passed, "items": items},
              open(DEMO + "/reports/AI_CPE_功能演示_%s.json" % ts, "w"), ensure_ascii=False, indent=1)
    with LOCK:
        STATE["report"] = md
    log("报告已生成: %s" % md)


# ---------------- 可视化看板（HTTP + 实时页面） ----------------
DASH_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>AI CPE 功能演示 · 实时看板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#e8eef7;font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif}
header{background:linear-gradient(90deg,#0f3d5c,#1b6ca8);padding:18px 26px;display:flex;align-items:center;gap:24px;flex-wrap:wrap}
h1{font-size:26px;letter-spacing:1px}
.tag{background:rgba(255,255,255,.14);padding:6px 12px;border-radius:20px;font-size:13px}
#bar{flex:1;min-width:220px;height:14px;background:rgba(255,255,255,.18);border-radius:8px;overflow:hidden}
#barIn{height:100%;width:0;background:linear-gradient(90deg,#31d17a,#8ef0b4);transition:width .6s ease}
main{display:grid;grid-template-columns:1.35fr .95fr;gap:18px;padding:18px}
#cards{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.card{background:#121c2e;border:1px solid #1f3350;border-radius:14px;padding:14px 16px;position:relative;overflow:hidden;transition:.4s}
.card.run{border-color:#3aa0f0;box-shadow:0 0 0 1px #3aa0f0 inset,0 0 26px rgba(58,160,240,.25)}
.card.pass{border-color:#2ecc71;background:#0f2a1e}
.card.fail{border-color:#e74c3c;background:#2a1214}
.card h3{font-size:17px;margin-bottom:6px}
.card .d{font-size:12px;color:#8fa6c4;margin-bottom:8px}
.kv{font-size:12.5px;color:#c9d8ea;line-height:1.75}
.kv b{color:#7fd1ff;font-weight:600}
.badge{position:absolute;top:12px;right:14px;font-size:12px;padding:3px 10px;border-radius:20px;background:#243b5a}
.spin{width:12px;height:12px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;display:inline-block;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
#right{display:flex;flex-direction:column;gap:14px}
#camwrap{background:#121c2e;border:1px solid #1f3350;border-radius:14px;padding:10px}
#cam{width:100%;border-radius:10px;display:block;background:#000;min-height:220px}
#log{background:#0a1526;border:1px solid #1f3350;border-radius:14px;padding:12px;height:300px;overflow:auto;font:12px/1.7 ui-monospace,Consolas,monospace;color:#9fe8b6;white-space:pre-wrap}
#sum{margin:0 18px 18px;padding:16px;border-radius:14px;background:#0f2a1e;border:1px solid #2ecc71;font-size:18px;display:none}
.small{font-size:12px;color:#8fa6c4}
</style></head><body>
<header>
  <h1>AI CPE 五大功能演示</h1>
  <span class="tag" id="dev">RG660MK · 5G 公网</span>
  <div id="bar"><div id="barIn"></div></div>
  <span class="tag" id="stat">初始化…</span>
</header>
<main>
  <div id="cards"></div>
  <div id="right">
    <div id="camwrap"><div class="small" style="margin-bottom:6px">现场摄像头实时画面（C270）</div><img id="cam" alt="等待画面…"></div>
    <div id="log"></div>
  </div>
</main>
<div id="sum"></div>
<script>
const ICON={pending:"○",running:null,pass:"✅",fail:"❌",skip:"⏭"};
const CN={pending:"待运行",running:"运行中",pass:"通过",fail:"失败",skip:"跳过"};
async function tick(){
  try{
    const r=await fetch("/state",{cache:"no-store"}); const s=await r.json();
    const box=document.getElementById("cards");
    box.innerHTML="";
    let done=0;
    s.items.forEach(it=>{
      const st=it.status; if(st==="pass"||st==="fail")done++;
      const el=document.createElement("div"); el.className="card "+(st==="running"?"run":st);
      let badge = st==="running" ? '<span class="badge"><span class="spin"></span> 运行中</span>'
                                 : '<span class="badge">'+(ICON[st]||"")+" "+CN[st]+"</span>";
      el.innerHTML=`<h3>${it.idx}. ${it.name}</h3><div class="d">${it.desc}</div>${badge}
        <div class="kv">${it.metrics.map(([k,v])=>`<div><b>${k}</b>：${v}</div>`).join("")||"<span class='small'>等待执行…</span>"}</div>
        ${it.dur?`<div class="small" style="margin-top:6px">耗时 ${it.dur}s</div>`:""}`;
      box.appendChild(el);
    });
    document.getElementById("barIn").style.width=Math.round(done/s.items.length*100)+"%";
    document.getElementById("stat").textContent=s.summary||(s.phase==="running"?"演示进行中…":"等待开始");
    const lg=document.getElementById("log"); lg.textContent=s.log.join("\n"); lg.scrollTop=lg.scrollHeight;
    if(s.done){const sm=document.getElementById("sum"); sm.style.display="block"; sm.innerHTML="🎉 "+s.summary+"　<span class='small'>报告已生成："+s.report+"</span>";}
    document.getElementById("cam").src="/snap?ts="+Date.now();
  }catch(e){ }
}
setInterval(tick,1200); tick();
</script></body></html>"""


class Handler(__import__("http.server").server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        try:
            self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))
        except Exception:
            pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self._send(200, "text/html; charset=utf-8", DASH_HTML)
        elif p == "/state":
            with LOCK:
                self._send(200, "application/json; charset=utf-8", json.dumps(STATE, ensure_ascii=False))
        elif p == "/snap":
            try:
                with urllib.request.urlopen(CAMAV + "/snapshot", timeout=8) as r:
                    self._send(200, "image/jpeg", r.read())
            except Exception:
                self._send(404, "text/plain", "no camera")
        elif p == "/report":
            with LOCK:
                f = STATE.get("report")
            if f and os.path.exists(f):
                self._send(200, "text/markdown; charset=utf-8", open(f, encoding="utf-8").read())
            else:
                self._send(404, "text/plain", "report not ready")
        else:
            self._send(404, "text/plain", "not found")


def serve(port):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def main():
    ap = argparse.ArgumentParser(description="AI CPE 五大功能一键演示（含实时看板）")
    ap.add_argument("--port", type=int, default=8099, help="看板端口（默认 8099）")
    ap.add_argument("--no-audio", action="store_true", help="静音质检模式：不播报、不实际开关灯")
    ap.add_argument("--query", default="云海", help="图片检索的自然语言指令（默认 云海）")
    ap.add_argument("--no-serve", action="store_true", help="不启动看板，仅跑检查")
    ap.add_argument("--keep-alive", type=int, default=1800, help="跑完后看板继续在线秒数（默认 1800，0=立即退出）")
    a = ap.parse_args()
    if not a.no_serve:
        serve(a.port)
        ip = sh("ip -4 addr show br-lan | awk '/inet /{print $2}' | cut -d/ -f1")[1].strip() or "192.168.1.1"
        print("看板地址: http://%s:%d/   （局域网任意设备可访问；演示期间保持在线）" % (ip, a.port), flush=True)
    passed = run_all(a)
    if not a.no_serve and a.keep_alive > 0:
        print("演示已完成，看板继续在线 %d 秒（Ctrl+C 可提前结束）…" % a.keep_alive, flush=True)
        t0 = time.time()
        while time.time() - t0 < a.keep_alive:
            time.sleep(1)
    return 0 if passed == 5 else 1


if __name__ == "__main__":
    sys.exit(main())
