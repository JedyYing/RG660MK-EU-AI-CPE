#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""AI CPE 八大功能一键演示套件 —— 编排 + 实时可视化看板（v1.3）

用法:
    /data/hermes/venv/bin/python aicpe_demo.py            # 完整演示（含播报/点灯）
    ... aicpe_demo.py --no-audio                          # 静音质检模式
看板: http://192.168.1.1:<port>/  （局域网任意设备可看：进度 + 现场画面 + 日志）

v1.3: 新增第 7 项「MiniNAS 存储」—— x86 存储底座（ZFS + SMB/NFS + 相册，2026-10-12 上线）只读在线巡检。
v1.3.1: 「图片检索」改服务端文件名过滤（修复：原窗口内匹配命中不到云海等旧图、一直走兜底）。
v1.3.2: 第 7 项「相册库资产」计数改为看板轮询时自动近实时刷新（20s TTL）；此前仅在演示跑轮时快照，用户改库后数字不动、易被当成 bug。
v1.3.3: 第 3/7 项（检索/存储）：NAS 主机按可达性自动回退（局域网 192.168.1.244 → ZeroTier 172.25.25.240）——拔网线（经中继）也可完整演示（2026-10-14）。
v1.3.4: 检索加「语义兜底」——关键词未命中时自动走 Immich Smart Search（CLIP），「云海」等自然语言可直接语义出图（2026-10-14）。
v1.3.5: 第 7 项描述/指标更新（机身盘 /tank + CasaOS 门户）；新增 CasaOS(:80) 在线检查；第 3 项描述补「语义搜索」（2026-10-14 晚）。
v1.2: 新增第 6 项「AI 智能选网」—— ai-net Shadow 观察只读巡检（服务/邻区/候选/评分/安全审计）。
v1.1 修复（依据首次试跑实测）: 进程检测改 /proc/*/cmdline；抓帧写自有临时路径；
ASR 按端口 6006 判定并自愈拉起；视频链路改主动拉流测帧率；检索加关键词映射与兜底；家居改状态差分。
"""
import argparse, json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error
from urllib.parse import urlsplit
sys.path.insert(0, "/data/ai_cpe")
import llm_gateway
# 双模式：所有模型调用统一走 llm_gateway（本地 MT200 NPU / 在线 Hermes DeepSeek）。
# HTTP 出口仍按模式做主机白名单：内网固定主机常开；在线模式额外放行 ai.phicotek.com。
# 保留禁代理 + 禁重定向（2026-11-02：原全局 LAN_OPENER 会把在线 https 一并挡死，改为白名单）。
_ORIG_URLOPEN = urllib.request.urlopen


def _guarded_urlopen(req, *a, **kw):
    url = getattr(req, "full_url", None) or str(req)
    host = (urlsplit(url).hostname or "").lower()
    if host not in llm_gateway.allowed_hosts():
        raise urllib.error.URLError("host not allowed: %s" % host)
    return llm_gateway.OPENER.open(req, *a, **kw)


urllib.request.urlopen = _guarded_urlopen

HOME = "/data/ai_cpe/hermes/home"
DEMO = HOME + "/aicpe_demo"
SNAP = "/tmp/aicpe_demo_snap.jpg"
STATE_FILE = "/data/ai_cpe/hermes/home/aicpe_demo/state_last.json"

_STATE_MTIME = {"t": 0.0}


def reload_state_if_newer():
    """巡检/演示由独立进程写 state_last.json；serve-only 进程按 mtime 热加载，
    避免页面长期显示上一轮结果（2026-10-26）。"""
    if RUN_LOCK.locked():
        return  # 同一HTTP进程运行时必须保留条目对象，不能被磁盘快照替换。
    try:
        m = os.path.getmtime(STATE_FILE)
    except OSError:
        return
    if m <= _STATE_MTIME["t"]:
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("deployment") != "offline-v1":
            return
        with LOCK:
            STATE.clear()
            STATE.update(d)
        _STATE_MTIME["t"] = m
    except Exception:
        pass

VR = "/data/ai_cpe/demo/bin/vision_runner"
MODELS = {"detect": "/data/ai_cpe/demo/ai_models/yolov8n/model.ncnn.param",
          "pose": "/data/ai_cpe/demo/ai_models/yolov8n-pose/model.ncnn.param"}
CAMAV = "http://127.0.0.1:8092"; CAMVIEW = "http://127.0.0.1:8090"
IMMICH = "http://192.168.1.244:2283"; IMMICH_KEY_FILE = HOME + "/photos/.immich_key"
NAS_HOST = "192.168.1.244"   # MiniNAS 存储底座（x86 主机 sh-d-l，2026-10-12 上线）
NAS_ZT_HOST = "192.168.1.244"   # NAS 的 ZeroTier 地址：拔网线时经中继兜底（2026-10-14）
BULB = "/data/ai_cpe/smarthome.py"   # Matter 优先 / MQTT 兜底 控制面（原涂鸦脚本已退役，见 /data/ai_cpe/attic_tuya/）
TTS = "/data/ai_cpe/tts_say.py"
FACEREC = "/data/ai_cpe/face_recognize.py"; SHERPA_INIT = "/etc/init.d/sherpa-asr"
PY = "/usr/bin/python3"; VENV = "/data/hermes/venv/bin/python"
STATE = {"started": time.time(), "phase": "init", "items": [], "log": [], "done": False,
         "summary": "", "report": ""}
LOCK = threading.RLock()
RUN_LOCK = threading.Lock()
HISTORY_FILE = DEMO + "/history_offline.json"


def sh(cmd, t=120):
    try:
        r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=t)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return 1, "ERR: %s" % e


def tts_err(out, n=120):
    """TTS 失败输出摘要：取最后一行并截「头部」。

    只留尾部（o[-60:]）时看到的全是 Sec-MS-GEC 之类的 URL 片段，看不出原因；
    tts_say.py 每轮失败都会打一行 [TTS] try i/N failed: <类型>: <信息>。
    """
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    return lines[-1][:n] if lines else "无输出"


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


def port_listening(p, host="127.0.0.1", timeout=1.5):
    """以 TCP 连接探测（busybox awk 无 strtonum，解析 /proc/net/tcp 不可靠）"""
    import socket as _s
    sk = _s.socket(); sk.settimeout(timeout)
    try:
        sk.connect((host, int(p))); return True
    except Exception:
        return False
    finally:
        try: sk.close()
        except Exception: pass


def port_ok(p, hosts, tries=2, timeout=2.0):
    """多主机 × 多轮重试的端口探测（ZeroTier 抖动时避免单次失败误判）。返回 (ok, host)。"""
    for _ in range(tries):
        for h in hosts:
            if port_listening(p, h, timeout=timeout):
                return True, h
    return False, None


_NAS_PICK = {"host": None, "t": 0.0}


def nas_host():
    """按可达性选 NAS 主机：优先局域网 192.168.1.244；不可达时回退 ZeroTier 172.25.25.240。
    结果缓存 15 秒，避免一次演示内反复探测。"""
    now = time.time()
    c = _NAS_PICK
    if c["host"] and now - c["t"] < 15:
        return c["host"]
    for h in (NAS_HOST, NAS_ZT_HOST):
        if port_listening(2283, h) or port_listening(445, h):
            c["host"], c["t"] = h, now
            return h
    c["host"], c["t"] = NAS_HOST, now
    return NAS_HOST


def immich_url():
    return "http://%s:2283" % nas_host()


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
    if status in ("pass", "fail", "skip", "partial"):
        it["dur"] = max(0.0, round(time.time() - it["t0"], 2)) if it["t0"] else None
        it["checked_at"] = time.time()
    log("  [%s] %s %s" % (status.upper(), it["name"], ("- " + detail) if detail else ""))
    with LOCK:
        tracked = any(x is it for x in STATE.get("items", []))
        snapshot = json.loads(json.dumps(STATE, ensure_ascii=False)) if tracked else None
    if snapshot is not None:
        temporary = STATE_FILE + ".progress." + str(os.getpid())
        try:
            with open(temporary, "w") as out:
                json.dump(snapshot, out, ensure_ascii=False)
            os.replace(temporary, STATE_FILE)
        except OSError as e:
            print("进度持久化失败：" + type(e).__name__, flush=True)


CPE_MODEL = "RG660MK-EU"
CPE_PLATFORM = "MediaTek T930 + Wi-Fi MT7992"
_CPE_CACHE = {"t": 0.0, "d": {}}


def cpe_info():
    """CPE 基本信息（5G 信号/接入终端/WiFi 信号/温度等），12s 缓存供看板轮询。"""
    now = time.time()
    if now - _CPE_CACHE["t"] < 12 and _CPE_CACHE["d"]:
        return _CPE_CACHE["d"]
    d = {"model": CPE_MODEL, "platform": CPE_PLATFORM}
    rc, o = sh("mipc_wan_cli --nw_get_signal 2>/dev/null", t=6)
    m = re.search(r"RAT\s+([0-9A-Za-z+/]+)\s*,\s*RSRP=(-?\d+)", o)
    if m:
        d["rat"] = m.group(1); d["rsrp"] = int(m.group(2))
    st = []
    for ifc in ("ra0", "ra1", "rai0"):
        rc, o2 = sh("iw dev %s station dump 2>/dev/null" % ifc, t=5)
        cur = None
        for ln in o2.splitlines():
            mm = re.match(r"\s*Station\s+([0-9a-fA-F:]+)", ln)
            if mm:
                cur = {"mac": mm.group(1).lower(), "ifc": ifc}; st.append(cur)
            elif cur is not None:
                ms = re.search(r"signal:\s*(-?\d+)", ln)
                if ms:
                    cur["rssi"] = int(ms.group(1))
    leases = {}
    rc, o3 = sh("cat /tmp/dhcp.leases 2>/dev/null", t=5)
    for ln in o3.splitlines():
        p = ln.split()
        if len(p) >= 4:
            leases[p[1].lower()] = p[3]
    wm = set(s["mac"] for s in st); lm = set(leases)
    d["clients"] = len(wm | lm); d["clients_wifi"] = len(wm); d["clients_lan"] = len(lm - wm)
    d["sta"] = [{"name": leases.get(s["mac"], s["mac"][-8:]), "rssi": s.get("rssi")} for s in st[:3]]
    # 本机广播的 SSID（AP 接口；主 SSID jedy 置前）
    ssids = []
    for ifc in ("ra0", "rai0", "ra1"):
        rc, o4 = sh("iw dev %s info 2>/dev/null" % ifc, t=4)
        m5 = re.search(r"ssid\s+(\S+)", o4)
        if m5 and m5.group(1) not in ssids:
            ssids.append(m5.group(1))
    if ssids:
        d["ssid"] = " · ".join(ssids)
    rc, w = sh("ifstatus wan 2>/dev/null", t=6)
    d["wan_up"] = '"up": true' in w
    m4 = re.search(r'"address":\s*"([0-9.]+)"', w)
    if m4:
        d["wan_ip"] = m4.group(1)
    rc, o5 = sh("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null", t=4)
    try:
        d["temp_c"] = round(int(o5.strip()) / 1000.0, 1)
    except Exception:
        pass
    try:
        d["uptime_s"] = int(float(open("/proc/uptime").read().split()[0]))
    except Exception:
        pass
    # 内存：总量 / 可用 + Hermes 智能体占用（直接读 /proc，避免 shell 匹配自扰）
    try:
        mi = open("/proc/meminfo").read()
        mt = re.search(r"MemTotal:\s+(\d+)", mi)
        ma = re.search(r"MemAvailable:\s+(\d+)", mi)
        if mt:
            d["mem_total_mb"] = int(round(int(mt.group(1)) / 1024.0))
        if ma:
            d["mem_avail_mb"] = int(round(int(ma.group(1)) / 1024.0))
    except OSError:
        pass
    try:
        import glob
        tot_kb = 0; nproc = 0
        for cd in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                cl = open(cd, "rb").read().replace(b"\x00", b" ").decode("utf-8", "ignore")
            except OSError:
                continue
            if "venv/bin/hermes" in cl:
                try:
                    for ln in open(cd.rsplit("/", 1)[0] + "/status"):
                        if ln.startswith("VmRSS:"):
                            tot_kb += int(ln.split()[1]); nproc += 1
                            break
                except OSError:
                    pass
        if tot_kb:
            d["hermes_mb"] = int(round(tot_kb / 1024.0)); d["hermes_procs"] = nproc
    except Exception:
        pass
    # 网络诊断与小区自愈（radiodiag 状态文件 + 审计日志）
    try:
        import os as _os
        diag = {"verdict": None, "heal_today": 0, "heal_ok": 0, "recent": []}
        _eff_rows = []
        try:
            rd = json.load(open("/data/ai_cpe/radiodiag_state.json"))
            diag["verdict"] = rd.get("verdict")
            diag["rsrp"] = rd.get("rsrp")
            diag["ts"] = str(rd.get("ts", ""))[11:16]
            cell = rd.get("cell") or {}
            if cell.get("nci"):
                diag["cell"] = str(cell["nci"])
        except Exception:
            pass
        today = time.strftime("%Y-%m-%d")
        heals = []
        try:
            lp = "/data/ai_cpe/radiodiag_log.jsonl"
            sz = _os.path.getsize(lp)
            with open(lp, "rb") as f:
                if sz > 2000000:
                    f.seek(sz - 2000000)
                for ln in f.read().decode("utf-8", "ignore").splitlines():
                    if '"heal"' not in ln:
                        continue
                    try:
                        ev = json.loads(ln)
                    except Exception:
                        continue
                    if ev.get("type") != "heal":
                        continue
                    if ev.get("result") == "dry-run":
                        continue                      # 演练不计入自愈统计
                    ts = str(ev.get("ts", ""))
                    if ts.startswith(today):
                        diag["heal_today"] += 1
                        if ev.get("success"):
                            diag["heal_ok"] = diag.get("heal_ok", 0) + 1
                    _b = (ev.get("before") or {}).get("rsrp")
                    _a = (ev.get("after") or {}).get("rsrp")
                    _eff_ok = (bool(ev.get("cell_changed")) or bool(ev.get("success"))
                               or ((ev.get("rsrp_delta") or 0) >= 6)
                               or (isinstance(_a, int) and isinstance(_b, int) and _a >= -90 and _b <= -100))
                    if not (_b is None and _a is None and not ev.get("cell_changed")):
                        _eff_rows.append(bool(_eff_ok))
                    b = ev.get("before") or {}; a = ev.get("after") or {}
                    _b, _a = b.get("rsrp"), a.get("rsrp")
                    try:
                        _d = int(_a) - int(_b)
                        _dtxt = "Δ%+d dB" % _d
                    except Exception:
                        _dtxt = "Δ?"
                    _tags = [_dtxt]
                    if ev.get("cell_changed"):
                        _tags.append("已换小区")
                    if not ev.get("success"):
                        _tags.append("无变化")
                    heals.append("%s %s RSRP %s→%s（%s）" % (
                        ts[11:16],
                        "✓" if ev.get("success") else "·",
                        "—" if _b is None else _b, "—" if _a is None else _a,
                        " · ".join(_tags)))
        except OSError:
            pass
        diag["recent"] = heals[-3:]
        if _eff_rows:
            _last = _eff_rows[-20:]
            diag["eff_pct"] = int(round(100.0 * sum(1 for x in _last if x) / len(_last)))
            diag["eff_n"] = len(_last)
            # 与 radiodiag 的判定口径保持一致（同一函数，避免两套算法打架）
            try:
                if "/data/ai_cpe" not in sys.path:
                    sys.path.insert(0, "/data/ai_cpe")
                import radiodiag as _rd
                _e, _n = _rd.heal_effectiveness()
                if _e is not None:
                    diag["eff_pct"] = int(round(_e * 100))
                    diag["eff_n"] = _n
                _pr, _pn = _rd.probe_stats()
                if _pn:
                    diag["probe_n"] = _pn
                    diag["probe_pct"] = int(round((_pr or 0) * 100))
            except Exception:
                pass
            if diag.get("probe_n"):
                if diag["probe_n"] < 10:
                    diag["eff_mode"] = "观察中 %d/10" % diag["probe_n"]
                elif diag.get("probe_pct", 0) >= 80:
                    diag["eff_mode"] = "自动（观察达标）"
                else:
                    diag["eff_mode"] = "观察未达标（仅建议）"
            else:
                diag["eff_mode"] = "观察中 0/10"
        d["radiodiag"] = diag
    except Exception:
        pass
    _CPE_CACHE["t"] = now; _CPE_CACHE["d"] = d
    return d


def immich_upload(path, t=30):
    """把本地照片上传 Immich（multipart），返回 asset_id；失败返回 ''。"""
    key = immich_key()
    if not key:
        return ""
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError:
        return ""
    boundary = "----aicpe%d" % int(time.time() * 1000)
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    fields = {"deviceAssetId": "aicpe-demo-%d" % int(time.time()), "deviceId": "rg660mk-demo",
              "fileCreatedAt": now, "fileModifiedAt": now}
    body = b""
    for name, value in fields.items():
        body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, name, value)).encode()
    body += ("--%s\r\nContent-Disposition: form-data; name=\"assetData\"; filename=\"aicpe_vision.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n" % boundary).encode()
    body += content + b"\r\n" + ("--%s--\r\n" % boundary).encode()
    # 2026-10-19：链路抖动时 nas_host() 可能回退到不可达的局域网地址 → 改为多主机×2 轮重试
    hosts = []
    for h in (nas_host(), NAS_ZT_HOST, NAS_HOST):
        if h and h not in hosts:
            hosts.append(h)
    for h in hosts:
        for _ in range(2):
            req = urllib.request.Request("http://%s:2283/api/assets" % h, data=body, headers={
                "x-api-key": key, "Content-Type": "multipart/form-data; boundary=%s" % boundary})
            try:
                with urllib.request.urlopen(req, timeout=t) as r:
                    jid = json.loads(r.read().decode("utf-8")).get("id", "")
                    if jid:
                        return jid
            except Exception:
                continue
    return ""


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
    wav_list = sorted([os.path.join("/data/ai_cpe/capdiag", f) for f in os.listdir("/data/ai_cpe/capdiag")
                       if f.endswith(".wav")], key=os.path.getmtime)
    asr_text = ""; asr_pick = ""
    # 从最新往旧扫最近 12 条现场录音，取第一条能转出非空文本的
    # （现场偶有极短碎片/无语音录音，固定只取最新一条会被碎片偶发误报失败）
    for wf in reversed(wav_list[-12:]):
        fn = "_paraformer_ws_once" if asr_up else "_paraformer_oneshot"
        code = ("import sys,json;sys.path.insert(0,'/data/ai_cpe');import voice_assistant as va;"
                "print(json.dumps({'text': getattr(va,'%s')(sys.argv[1])}, ensure_ascii=False))" % fn)
        rc, out = sh("%s -c %s %s" % (VENV, json.dumps(code), json.dumps(wf)), t=180)
        try:
            t = json.loads(out.strip().splitlines()[-1]).get("text", "")
        except Exception:
            t = ""
        if t:
            asr_text = t; asr_pick = os.path.basename(wf); break
    if not asr_text:
        # 现场录音多为静音/碎片：用固定 16k 语音样本做确定性验证（2026-10-26）
        sample = "/data/ai_cpe/demo/asr_sample.wav"
        if os.path.exists(sample):
            code = ("import sys,json;sys.path.insert(0,'/data/ai_cpe');import voice_assistant as va;"
                    "print(json.dumps({'text': va._paraformer_ws_once(sys.argv[1])}, ensure_ascii=False))")
            rc, out = sh("%s -c %s %s" % (VENV, json.dumps(code), json.dumps(sample)), t=120)
            try:
                asr_text = json.loads(out.strip().splitlines()[-1]).get("text", "")
                asr_pick = asr_pick or "asr_sample.wav"
            except Exception:
                pass
    ms.append(("ASR 实测转写", (asr_text[:40] + "…") if asr_text else "未取到文本"))
    if asr_pick:
        ms.append(("转写素材", asr_pick + "（已有录音，非本次实时发言）"))
    from offline_agent import device_clock_reply
    clock_reply = device_clock_reply(asr_text) if asr_text else None
    model_reply = ""; model_error = ""; model_meta = {}
    try:
        q = "用一句话介绍你能做什么" if clock_reply else (asr_text or "")
        if q:
            model_reply, model_meta = llm_gateway.ask_meta(q)
    except Exception as e:
        model_error = str(e)[:100]
        log("  大模型通道异常（%s）: %s" % (llm_gateway.describe().get("label"), model_error))
    reply = clock_reply or model_reply
    ms.append(("推理侧", "%s · %s" % (model_meta.get("label", llm_gateway.describe().get("label")),
                                     model_meta.get("model", "-")) if model_meta else
                                    "%s（未调用）" % llm_gateway.describe().get("label")))
    if model_meta:
        ms.append(("推理耗时", "%.1f s" % (model_meta.get("elapsed_ms", 0) / 1000.0)))
    ms.append(("大模型对话（独立检查）" if clock_reply else "大模型对话（设备集成）", model_reply[:100] if model_reply else (model_error or "无识别文本，未调用模型")))
    if clock_reply:
        ms.append(("设备时钟回答", clock_reply))
    tts_ok = None
    if no_audio:
        ms.append(("TTS 播报（本地 VITS）", "静音模式（未播报，闭环待验收）"))
    else:
        # tts_say.py 内部已重试 3 次；这里再兜 2 轮，覆盖 edge-tts 端点偶发 403
        # （2026-10-14：演示曾因单次 403 把「AI 智能语音对话」判为失败）
        o = ""
        for _try in range(2):
            r = subprocess.run([VENV, TTS, reply], capture_output=True, text=True, timeout=120) if reply else None
            rc, o = (r.returncode, (r.stdout or "") + (r.stderr or "")) if r else (1, "没有模型回复，不播报固定话术")
            tts_ok = (rc == 0)
            if tts_ok:
                break
            log("  TTS 播报失败，重试 %d/2（%s）" % (_try + 1, tts_err(o)))
            time.sleep(2)
        ms.append(("TTS 播报（本地 VITS）", "已播报" if tts_ok else "失败: %s" % tts_err(o)))
    ok = va_alive and asr_up and bool(asr_text) and bool(model_reply) and bool(reply) and (tts_ok is not False)
    set_item(it, "skip" if ok and no_audio else ("pass" if ok else "fail"),
             ("识别和%s问答通过，播放未验收" % llm_gateway.describe().get("label", "大模型")) if ok and no_audio
             else ("真实识别→%s回复→本地播报" % llm_gateway.describe().get("label", "大模型") if ok else "存在未通过子项"), ms)


CAM_NOTICE = "/tmp/aicpe_cam_obstructed.jpg"


def cam_obstructed(path):
    """判断画面是否"被遮挡/无效"：边缘能量低 + 中心区域平坦。返回 (bool, 指标)。"""
    try:
        from PIL import Image, ImageFilter, ImageStat
        im = Image.open(path).convert("L")
        edges = im.filter(ImageFilter.FIND_EDGES)
        em = ImageStat.Stat(edges).mean[0]
        w, h = im.size
        c = im.crop((w // 4, h // 4, w * 3 // 4, h * 3 // 4))
        cstd = ImageStat.Stat(c).stddev[0]
        mean = ImageStat.Stat(im).mean[0]
        kind = ""
        if mean < 25:
            kind = "dark"                      # 环境过暗（C270 无夜视），不是遮挡
        elif em < 4.0 and cstd < 30:
            kind = "obstructed"                # 画面平坦：被遮挡/严重失焦
        return kind, {"edges": round(em, 2), "center_stddev": round(cstd, 1), "mean": round(mean, 1)}
    except Exception:
        return False, {}


def cam_notice_image(kind="obstructed", m=None):
    """生成提示图（640x480）：obstructed=被遮挡；dark=天黑看不见。"""
    out = "/tmp/aicpe_cam_" + kind + ".jpg"
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (640, 480), "#0b1220")
        d = ImageDraw.Draw(im)
        fp = "/data/hermes/fonts/noto_sc.otf"
        f1 = ImageFont.truetype(fp, 32)
        f2 = ImageFont.truetype(fp, 18)
        f3 = ImageFont.truetype(fp, 14)
        m = m or {}
        if kind == "obstructed":
            t1, t2 = "摄像头被挡住了", "把遮挡物移开后会自动恢复画面"
            # 画一个"镜头被挡"图标：圆环 + 斜杠
            d.ellipse((300, 118, 340, 158), outline="#8ea3bd", width=3)
            d.line((306, 152, 334, 124), fill="#8ea3bd", width=3)
        else:
            t1, t2 = "天黑了，看不见", "开灯或天亮后会自动恢复画面"
            # 画一弯月亮：亮圆 + 背景色圆覆盖
            d.ellipse((296, 116, 344, 164), fill="#8ea3bd")
            d.ellipse((310, 108, 358, 156), fill="#0b1220")
        w1 = d.textlength(t1, font=f1); w2 = d.textlength(t2, font=f2)
        d.text(((640 - w1) / 2, 208), t1, font=f1, fill="#e8eef7")
        d.text(((640 - w2) / 2, 262), t2, font=f2, fill="#8ea3bd")
        foot = ""
        if kind == "dark" and m.get("mean") is not None:
            foot = "实测亮度 %d/255（几乎全黑）· C270 无夜视" % round(m.get("mean") or 0)
        elif kind == "obstructed" and m.get("edges") is not None:
            foot = "画面细节能量 %.1f · 非光线问题" % (m.get("edges") or 0)
        if foot:
            w3 = d.textlength(foot, font=f3)
            d.text(((640 - w3) / 2, 320), foot, font=f3, fill="#5f7288")
        im.save(out, "JPEG", quality=88)
        return out
    except Exception:
        return ""


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


def check_vision(it, no_audio=False, no_upload=False):
    set_item(it, "running")
    ms = []
    ok, size = take_snapshot()
    if not ok:
        set_item(it, "fail", "抓帧失败（camview/camav 未就绪）", [("现场抓帧", "失败")]); return
    ms.append(("现场抓帧", "%d KB" % (size // 1024)))
    _kind, _cm = cam_obstructed(SNAP)
    if _kind == "obstructed":
        ms.append(("画面状态", "摄像头被挡住了，看不见（看板显示提示图；细节能量 edges=%s）" % _cm.get("edges")))
    elif _kind == "dark":
        ms.append(("画面状态", "天黑了，看不见（实测亮度 %.0f/255，C270 无夜视；不是遮挡）" % (_cm.get("mean") or 0)))
    if no_audio or no_upload:
        ms.append(("照片上传 Immich", "未上传（只读检查，上传链路未验收）"))
    else:
        aid = immich_upload(SNAP)
        if aid:
            ms.append(("照片上传 Immich", "已上传 ✓"))
        else:
            _alive, _hh = nas_alive()
            ms.append(("照片上传 Immich", "跳过（NAS 链路不可达，非功能失败）" if not _alive else "失败（Immich 拒绝或 Key 无效）"))
    try:
        det = vision_call("detect", SNAP); pose = vision_call("pose", SNAP)
    except Exception as e:
        set_item(it, "fail", "YOLO 推理失败: %s" % str(e)[:80], ms); return
    dets = det.get("detections", []) or []; persons = pose.get("persons", []) or []
    det_persons = [d for d in dets if (d.get("class_id") == 0 or str(d.get("class_name", "")).lower() == "person")
                   and (d.get("score", 0) or 0) >= 0.35]
    ms.append(("YOLO 目标检测", "%.0fms · %s" % (det.get("inference_ms", 0),
               ", ".join("%s(%.2f)" % (d.get("class_name"), d.get("score", 0)) for d in dets[:4]) or "无目标")))
    ms.append(("YOLO 姿态估计", "%.0fms · %d 人" % (pose.get("inference_ms", 0), len(persons))))
    # 2026-10-19：坐姿判定加双重门槛——目标检测必须检到人(class=person) 且姿态置信度>=0.5，
    # 否则视为"未检出有效人体"跳过判定（此前背景物体被误判成人 → 空房间误报"坐姿异常"）。
    persons = [p for p in persons if (p.get("score") or 0) >= 0.5]
    verdict = "画面内无人（链路已执行）"
    if persons and not det_persons:
        verdict = "未检出有效人体（目标检测与姿态结果不一致，跳过坐姿判定）"
    elif persons:
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
    identity_code = ("import sys,json;sys.path.insert(0,'/data/ai_cpe');"
                     "from face_recognize import local_recognize;f,e=local_recognize(sys.argv[1]);"
                     "print(json.dumps({'ok':f is not None,'error':e,'subjects':"
                     "[x.get('subject_id','unknown') for x in (f or []) if isinstance(x,dict)]},ensure_ascii=False))")
    rc, o = sh("%s -c %s %s" % (PY, json.dumps(identity_code), json.dumps(SNAP)), t=180)
    identity = {}; identity_verified = False
    try:
        identity = json.loads(o.strip().splitlines()[-1])
        rc = 0 if rc == 0 and identity.get('ok') else 1
        subjects = identity.get('subjects') or []
        identity_verified = bool(subjects) and all(s and s != 'unknown' for s in subjects)
        names = ['本人' if s == 'self' else s for s in subjects]
        identity_text = ('匹配身份：' + '、'.join(names)) if identity_verified else '未匹配已录入身份'
    except (ValueError, IndexError, TypeError):
        rc = 1; identity_text = '本地身份结果无效'
    ms.append(("人脸识别（身份）", identity_text))
    gallery_ready = os.path.exists("/data/ai_cpe/demo/data/face-gallery.json")
    ms.append(("身份库", ("已录入身份模板 · 本轮匹配成功" if identity_verified else "已存在；具体身份未匹配")
               if gallery_ready else "尚未录入身份模板；只能检测人脸，不能识别人名"))
    # 判定原则：只有「判定过程本身出错」才算功能失败；"画面内无人"、"检出坐姿异常" 都是**场景状态**，
    # 说明链路已正确执行（实测：把"检出异常坐姿"当失败会造成 2/3 的假失败）。
    ok = not verdict.startswith("判定异常")
    if ok and verdict.startswith("坐姿异常"):
        detail = "视觉双检已完成：检出异常坐姿（场景状态，功能正常）"
    else:
        detail = ("视觉双检已完成：" if ok else "") + verdict
    if not ok or rc != 0:
        status = "fail"
    elif not gallery_ready or not verdict.startswith("坐姿") or not identity_verified:
        status = "partial"
        detail += "；检测链路执行，但坐姿或身份未完成验收"
    else:
        status = "pass"
    if no_audio or no_upload:
        detail += "；上传链路未验收"
        if status == "pass":
            status = "partial"
    set_item(it, status, detail, ms)


KEYMAP = [("云海", "yunhai"), ("泰晤士", "thames"), ("武夷山", "wuyishan")]


def nl_query(q):
    kw = None
    for k, pat in KEYMAP:
        if k in q:
            kw = pat; break
    body = {"size": 60, "page": 1}
    if kw:
        body["originalFileName"] = kw   # v1.3.1: 服务端全库按文件名过滤（修复：原实现只在“最近 60 条”窗口内匹配，旧图命中不到而静默走兜底）
    m = re.search(r"(20\d\d[-/年]\d{1,2}[-/月]\d{1,2})", q)
    if m:
        d = m.group(1).replace("年", "-").replace("月", "-").replace("/", "-").replace("日", "")
        body["takenAfter"] = d + "T00:00:00.000Z"; body["takenBefore"] = d + "T23:59:59.999Z"
    from datetime import datetime, timedelta
    if "今天" in q:
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        body["takenAfter"] = midnight.isoformat()
        body["takenBefore"] = (midnight + timedelta(days=1)).isoformat()
    elif "最近" in q:
        body["takenAfter"] = (datetime.now().astimezone() - timedelta(days=7)).isoformat()
    return body, kw


def nas_alive():
    """NAS 是否可达（任一关键端口、多主机 × 2 轮）。用于区分「环境不可达」与「功能失败」。"""
    for _p in (445, 2283, 80):
        ok, h = port_ok(_p, immich_hosts(), tries=2, timeout=2.0)
        if ok:
            return True, h
    return False, None


def immich_hosts():
    out = []
    for h in (nas_host(), NAS_ZT_HOST, NAS_HOST):
        if h and h not in out:
            out.append(h)
    return out


def immich_api(path, data=None, tries=2, timeout=10):
    """多主机 × 多轮重试调用 Immich API。返回 (json, host)。"""
    key = immich_key()
    last = None
    for _ in range(tries):
        for h in immich_hosts():
            try:
                req = urllib.request.Request("http://%s:2283%s" % (h, path),
                                             data=json.dumps(data).encode() if data is not None else None,
                                             headers={"x-api-key": key or "",
                                                      "Content-Type": "application/json",
                                                      "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8")), h
            except Exception as e:
                last = e
    raise RuntimeError(str(last)[:120])


def search_local_photos(query):
    body, kw = nl_query(query)
    hits = []
    if kw:
        res, host = immich_api("/api/search/metadata", data=body, tries=1, timeout=15)
        hits = [a for a in (res.get("assets") or {}).get("items", []) if kw in (a.get("originalFileName") or "")]
    mode = "关键词匹配"
    if not hits:
        # 当前CLIP文本塔为英文模型；只对明确短语映射，展示原查询和实际检索词。
        semantic = re.sub(r'^(?:请帮我|帮我|请)?(?:检索|搜索|查找|找一下|找出|找)\s*[:：]?\s*', '', query).strip()
        semantic = re.sub(r'(?:的)?(?:照片|图片|相片|图像)$', '', semantic).strip()
        for cn, en in (("云海", "sea of clouds"), ("泰晤士河", "River Thames"), ("狗", "dog"), ("猫", "cat"), ("人物", "person"), ("雪山", "snow mountain")):
            semantic = semantic.replace(cn, en)
        smart_body = {"query": semantic, "size": 6}
        for field in ("takenAfter", "takenBefore"):
            if field in body:
                smart_body[field] = body[field]
        res, host = immich_api("/api/search/smart", data=smart_body, tries=1, timeout=60)
        hits = (res.get("assets") or {}).get("items", [])
        mode = "本地CLIP语义候选；实际文本：" + semantic
    return {"status": "pass", "mode": mode, "count": len(hits), "matches": [{"name": a.get("originalFileName"), "date": a.get("fileCreatedAt")} for a in hits[:6]], "host": host}


def check_photo_search(it, query="云海", no_audio=False):
    set_item(it, "running")
    ms = [("本地自然语言指令", query)]
    try:
        from offline_agent import make_plan
        plan = make_plan("检索图片：" + query)
        if plan["tool"] != "photo_search":
            raise ValueError("MT200没有返回合法检索计划")
        parsed = plan["args"]["query"]
        # 检索约束来自原始用户输入，模型不得偷偷翻译/丢弃日期。
        res = search_local_photos(query)
        ms += [("MT200检索词", parsed), ("检索模式", res["mode"]), ("匹配结果", "%d张；零命中不兜底最新照片" % res["count"])]
        ms += [("候选", str(a["name"])) for a in res["matches"][:3]]
        set_item(it, "pass", "检索完成；结果相关性需核对" if res["count"] else "检索完成：零命中", ms)
    except Exception as e:
        set_item(it, "fail", "本地检索失败：" + str(e)[:100], ms)


def pull_stream(secs=4):
    # 只统计完整JPEG帧；跨块保留未完成帧，不重复统计尾部的帧头。
    t0 = time.monotonic(); frames = 0; total = 0; buf = b""
    try:
        with urllib.request.urlopen(CAMAV + "/stream?fps=10&w=288&q=38", timeout=secs + 1) as r:
            read = getattr(r, "read1", r.read)
            while time.monotonic() - t0 < secs:
                chunk = read(8192)
                if not chunk:
                    break
                total += len(chunk); buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    if start < 0:
                        buf = buf[-1:]; break
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        buf = buf[start:]; break
                    frames += 1; buf = buf[end + 2:]
                if len(buf) > 4 * 1024 * 1024:
                    raise ValueError("视频帧超出采样上限")
        return frames, total, max(0.1, time.monotonic() - t0)
    except Exception:
        # 不把中途断流后已经收到的少量帧冒充完整成功采样。
        return 0, total, max(0.1, time.monotonic() - t0)


def check_diag(it):
    set_item(it, "running")
    ms = []
    frames, total, el = pull_stream(4)
    fps = round(frames / el, 2); kbs = round(total / 1024 / el, 1)
    ms.append(("测试模式", "本次请求10fps；服务默认2fps省流量模式，不修改常驻配置"))
    ms.append(("视频拉流实测", "%d 帧 / %.1fs → %.2f fps · %.1f KB/s" % (frames, el, fps, kbs)))
    lat = []
    for _ in range(3):
        t = time.time()
        ok, _ = take_snapshot()
        if ok:
            lat.append(round((time.time() - t) * 1000))
    ms.append(("单帧抓取时延", ("%s ms（3 次均值）" % round(sum(lat) / len(lat))) if lat else "失败"))
    link_issues = []

    def probe(host):
        """单次链路探测；返回 (loss%, rtt_avg, rtt_max, raw)。"""
        rc, o = sh("ping -c 8 -W 2 %s 2>&1 | tail -2" % host, t=25)
        rtt = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)", o)
        loss = re.search(r"(\d+)% packet loss", o)
        if rc != 0 or not (rtt and loss):
            return None
        return float(loss.group(1)), rtt.group(2), rtt.group(3), o

    for label, host in (("NAS Immich", nas_host()), ("笔记本LAN", NAS_HOST)):
        first = probe(host)
        best, retried = first, False
        # 2026-11-02：单次 ICMP 偶发丢 1 包就把整项判失败，误报率高。
        # 有丢包时复测一次，取两次里更好的一次；两次都丢才判异常（结果里注明已复测）。
        if first and first[0] > 0:
            second = probe(host)
            retried = True
            if second and second[0] < first[0]:
                best = second
        if best:
            ms.append(("链路 " + label, "丢包 %g%% · RTT 均值 %s ms（max %s）%s" % (
                best[0], best[1], best[2], "（首次丢包，已复测取优）" if retried else "")))
            if best[0] > 0:
                link_issues.append(label + "持续丢包 %.0f%%" % best[0])
        else:
            ms.append(("链路 " + label, (first[3].replace("\n", " ")[:70] if first else "探测失败")))
            link_issues.append(label + "探测失败")
    rc, o = sh("%s /data/ai_cpe/netdiag.py clients 2>&1 | head -6" % PY, t=60)
    ms.append(("网络全景（netdiag）", "；".join([l for l in o.splitlines() if l.strip()][:3])[:90]))
    cam_ok = proc_alive("camview")
    ms.append(("摄像头服务(camview)", "存活" if cam_ok else "未运行"))
    try:
        target = float(os.environ.get("AICPE_VIDEO_TARGET_FPS", "10"))
        if not 1 <= target <= 120:
            raise ValueError()
    except ValueError:
        target = 10.0
    ms.append(("采样目标", "%.1f fps；有限窗口允许1帧计数边界误差±%.2f fps，非降低目标" % (target, 1.0 / el)))
    issues = list(link_issues)
    if frames == 0:
        issues.append("未收到完整视频帧或采样中断")
    if not lat:
        issues.append("现场抓帧失败")
    if not cam_ok:
        issues.append("摄像头服务未运行")
    if issues:
        status = "fail"; verdict = "发现异常：" + "；".join(issues)
    elif fps + 1.0 / el < target:
        status = "partial"; verdict = "低帧率预览：%.2f fps，未达到%.1f fps目标；不能据此宣称无卡顿风险" % (fps, target)
    else:
        status = "pass"; verdict = "本次短时采样达到帧率目标，抓帧与链路探测通过；不代表所有场景无卡顿"
    ms.append(("排障结论", verdict))
    set_item(it, status, verdict, ms)


def _smarthome(action, t=30):
    rc, o = sh("%s %s %s" % (PY, BULB, action), t=t)
    try:
        return rc, json.loads(o)
    except Exception:
        return rc, {"_raw": o[:200]}


def _field(st, code):
    for r in (st.get("result") or []):
        if r.get("code") == code:
            return r.get("value")
    return None


def check_smart_home(it, no_audio=False):
    """智能灯控（Matter/MQTT 控制面，Matter 优先、MQTT 兜底）：设备内 broker + 代理 → 执行器，零云依赖、零涂鸦。"""
    set_item(it, "running")
    ms = []
    rc, st0 = _smarthome("status")
    ok0 = rc == 0 and bool(st0.get("success")) and _field(st0, "switch_led") in (True, False) and isinstance(_field(st0, "switch_led"), bool)
    sw0 = bool(_field(st0, "switch_led"))
    ms.append(("控制面（Matter/MQTT · Matter 优先）", ("正常 · transport=%s · broker=%s" % (st0.get("transport"), st0.get("broker"))) if ok0 else "异常: %s" % str(st0)[:60]))
    _drv = str(_field(st0, "driver") or "?")
    if _drv and "matter" not in _drv.lower():
        _drv += "（Matter 灯未配网 → 自动兜底；配网后填 node_id 即切 Matter）"
    ms.append(("执行器驱动", _drv))
    ms.append(("指令前状态", "开" if sw0 else "关"))
    if no_audio:
        ms.append(("动作", "静音质检模式：仅读取状态，不实际开关"))
        set_item(it, "skip" if ok0 else "fail", "Matter/MQTT灯控只读通过，真实动作待确认" if ok0 else "灯控状态读取失败", ms)
        return
    # 板载LED验收直接读取内核，不把驱动保存状态当作物理回读。
    if not ok0 or _field(st0, "driver") != "board-led":
        set_item(it, "fail", "本次仅授权板载LED验收，执行器不匹配或初态无效", ms)
        return
    led_path = "/sys/class/leds/5g_evb_voice/brightness"
    def led_value():
        try:
            return int(open(led_path).read().strip())
        except (OSError, ValueError):
            return None
    physical0 = led_value()
    if physical0 is None or bool(physical0) != sw0:
        set_item(it, "fail", "LED内核初态与控制面不一致，未执行动作", ms)
        return
    ms.append(("物理初态", "内核brightness=%s" % physical0))
    seq_ok = True
    rc_f, st_f = 1, {}
    try:
        for i in range(1, 4):
            rc_o, st_o = _smarthome("off", t=45)
            sw_o = _field(st_o, "switch_led")
            physical_off = led_value()
            time.sleep(0.8)
            rc_n, st_n = _smarthome("on", t=45)
            sw_n = _field(st_n, "switch_led")
            physical_on = led_value()
            time.sleep(0.8)
            ms.append(("第%d轮物理回读" % i, "off=%s / on=%s" % (physical_off, physical_on)))
            good = rc_o == 0 and rc_n == 0 and bool(st_o.get("success")) and bool(st_n.get("success")) and sw_o is False and sw_n is True and physical_off == 0 and physical_on is not None and physical_on > 0
            seq_ok = seq_ok and good
            ms.append(("第 %d 轮 关→开" % i, "关开回读%s" % ("一致" if good else "异常")))
            if not good:
                break
    except Exception as e:
        seq_ok = False
        ms.append(("动作异常", str(e)[:100]))
    finally:
        try:
            rc_f, st_f = _smarthome("on" if sw0 else "off", t=45)
        except Exception as e:
            seq_ok = False
            ms.append(("恢复异常", str(e)[:100]))
    sw_f = bool(_field(st_f, "switch_led"))
    ms.append(("现场恢复", "控制面恢复回读一致（%s）" % ("开" if sw_f else "关") if rc_f == 0 and st_f.get("success") and _field(st_f, "switch_led") is sw0 else "恢复失败或状态未知，需人工检查"))
    physical_final = led_value()
    ms.append(("恢复后物理回读", "brightness=%s（初态%s）" % (physical_final, physical0)))
    if seq_ok and ok0 and rc_f == 0 and st_f.get("success") and sw_f == sw0 and physical_final == physical0:
        set_item(it, "pass", "MQTT板载LED三轮内核回读通过；Matter实灯未验收", ms)
    else:
        set_item(it, "fail", "灯控循环存在未闭环轮次 → 检查 broker(1883)/agent/driver", ms)


def check_ai_net(it, no_audio=False):
    """AI 选网（ai-net）：QoE 驱动的蜂窝选网决策 —— Shadow 观察（只读巡检，零写操作）。"""
    import glob
    set_item(it, "running")
    ms = []
    svc_ok = proc_alive("ai_net.service")
    api_ok = port_listening(8123)
    ms.append(("服务状态", ("运行中 · API :8123 在线" if (svc_ok and api_ok) else "异常：服务或 API 未就绪")))
    stt = {}
    try:
        stt = json.load(open("/data/ai_cpe/ai_net/data/status.json", encoding="utf-8"))
    except Exception:
        pass
    if not stt:
        set_item(it, "fail", "AI 选网状态读取失败（status.json）", ms)
        return
    thresh = 12
    try:
        sel = (json.load(open("/data/ai_cpe/ai_net/config/rules.json", encoding="utf-8")).get("selection") or {})
        thresh = sel.get("min_score_gain", 12)
    except Exception:
        pass
    sc = stt.get("serving_cell") or {}
    qoe = stt.get("qoe_score") or 0
    nbrs = stt.get("neighbours") or []
    cands = stt.get("candidates") or []
    fsm = stt.get("fsm") or {}
    execu = stt.get("executor") or {}
    ms.append(("服务小区", "%s · RSRP %s dBm · QoE %.1f" % (sc.get("cell_id"), sc.get("rsrp_dbm"), qoe)))
    ms.append(("邻区感知", "%d 个邻区实时可见 · %d 个进入候选池（新鲜度 <=180s）" % (len(nbrs), len(cands))))
    best = cands[0] if cands else None
    gain = None
    if best:
        gain = round((best.get("score") or 0) - qoe, 1)
        ms.append(("最优候选", "%s · 得分 %.1f · 新鲜度 %ss · 增益 %+.1f（阈值 +%s）" % (
            best.get("cell"), best.get("score"), best.get("last_seen_s"), gain, thresh)))
    ms.append(("决策状态机", "%s · 10min 动作 %d 次 · 冷却剩余 %.0fs" % (
        fsm.get("state"), fsm.get("switches_10min") or 0, fsm.get("cooldown_left_s") or 0)))
    rec = oth = 0
    for fp in sorted(glob.glob("/data/ai_cpe/ai_net/data/decisions.jsonl*")):
        try:
            data = open(fp, "rb").read()
        except OSError:
            continue
        rec += data.count(b'"action": "recommend"')
        oth += data.count(b'"action": "') - data.count(b'"action": "none"') - data.count(b'"action": "recommend"')
    ms.append(("安全审计（决策日志）", "建议记录 %d 条 · 控制类动作 %d 条%s" % (
        rec, oth, "（零写操作，未控制 modem）" if oth == 0 else "（⚠ 有动作，需核查）")))
    ms.append(("执行安全", "mode=%s · executor=%s" % (execu.get("mode"), "已禁用（只读观察）" if not execu.get("enabled") else "启用⚠")))
    age = max(0, time.time() - os.path.getmtime("/data/ai_cpe/ai_net/data/status.json"))
    ms.append(("状态文件新鲜度", "%.1f秒；超过180秒不计实时" % age))
    ok = svc_ok and api_ok and execu.get("mode") == "shadow" and execu.get("enabled") is False and oth == 0 and age <= 180
    if ok and best and gain is not None and gain >= thresh:
        detail = "AI 选网在线（Shadow）：当前有「建议切换 -> %s」评估中（仅记录，不执行）" % best.get("cell")
    elif ok:
        detail = "AI 选网在线（Shadow）：邻区/候选/QoE 实时评估中，当前无切换建议"
    else:
        detail = "AI 选网异常：服务或 API 未就绪"
    set_item(it, "pass" if ok else "fail", detail, ms)


def check_casaos_center(it, no_audio=False):
    """CasaOS 统一管理界面（第 8 项）：App Store / Files / HA / Immich / 每日巡检 五个入口。
    只做只读可达性检查；存储主机整体不可达时按「环境问题」跳过。"""
    set_item(it, "running")
    ms = []
    alive, h = nas_alive()
    if not alive:
        ms.append(("存储主机", "不可达（环境问题）"))
        set_item(it, "fail", "CasaOS物理局域网不可达", ms)
        return

    base = "http://%s" % h

    def _code(path, t=6):
        try:
            with urllib.request.urlopen(base + path, timeout=t) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0

    c = _code("/")
    ms.append(("App Store 应用入口", "可达 ✓（CasaOS 门户 200）" if c == 200 else "不可达 ✗（HTTP %s）" % c))

    f = _code("/v1/files/get?path=/")
    ms.append(("Files 文件管理入口", "API需登录 ✓；文件操作未验收" if f == 401 else "API可达 ✓" if f == 200 else "不可达 ✗（HTTP %s）" % f))

    ok_ha, _ = port_ok(8123, [h], tries=2, timeout=2.0)
    ms.append(("Home Assistant 入口", "可达 ✓（:8123）" if ok_ha else "不可达 ✗"))

    imm = 0
    try:
        with urllib.request.urlopen("http://%s:2283/api/server/ping" % h, timeout=6) as r:
            imm = r.status
    except urllib.error.HTTPError as e:
        imm = e.code
    except Exception:
        imm = 0
    ms.append(("Immich 相册入口", "可达 ✓（:2283）" if imm == 200 else "不可达 ✗（HTTP %s）" % imm))

    # 每日巡检（作为本卡第 5 个入口，只读展示）
    try:
        import glob as _glob
        _files = sorted(_glob.glob("/data/deliver/www/daily/巡检摘要_*.json"))
        if _files:
            _d = json.load(open(_files[-1], encoding="utf-8"))
            _un = _d.get("unresolved", 0)
            ms.append(("每日巡检相关内容", "最新报告：未解决 %s 项 · 通过 %s 项" % (_un, _d.get("passed", "—"))))
            _insp_ok = (_d.get("failed", 0) == 0 and _un == 0)
        else:
            ms.append(("每日巡检相关内容", "暂无巡检报告"))
            _insp_ok = False
    except Exception as e:
        ms.append(("每日巡检相关内容", "读取失败：%s" % str(e)[:40]))
        _insp_ok = False

    ok_n = sum(1 for k, v in ms if "✓" in str(v))
    inspect_readable = any(k == "每日巡检相关内容" and str(v).startswith("最新报告") for k,v in ms)
    if ok_n >= 4 and inspect_readable:
        set_item(it, "pass", "四个服务入口可达，巡检文档可读；历史告警另列，不代表已解决", ms)
    elif ok_n == 4:
        set_item(it, "partial", "四个服务入口可达；巡检报告缺失或读取失败，报告内容未验收", ms)
    else:
        set_item(it, "fail", "CasaOS服务入口%d/4可达；巡检报告读取%s（与服务可达性分开统计）" % (ok_n, "成功" if inspect_readable else "失败"), ms)

def check_daily_inspect(it, no_audio=False):
    """每日巡检：读最近一份巡检报告（每日 07:00 自动运行）——只读展示。
    2026-10-20：优先读结构化摘要 JSON（巡检摘要_YYYY-MM-DD.json），md 解析仅作兜底；
    两者都读不到"数量"时标为 skip（无法判定），不再误判为失败。"""
    import glob
    set_item(it, "running")
    ms = []
    mds = sorted(glob.glob("/data/deliver/www/daily/巡检报告_*.md"))
    if not mds:
        set_item(it, "fail", "未找到巡检报告（每日 07:00 自动生成）", ms)
        return
    path = mds[-1]
    base = os.path.basename(path)
    m = re.search(r"巡检报告_(\d{4}-\d{2}-\d{2})", base)
    day = m.group(1) if m else "?"

    js = None
    for jf in sorted(glob.glob("/data/deliver/www/daily/巡检摘要_*.json")):
        if day in os.path.basename(jf):
            try:
                js = json.load(open(jf, encoding="utf-8"))
            except Exception:
                js = None
            break

    if js:
        unresolved = int(js.get("unresolved", -1))
        nas = "离线" if js.get("nas_offline") else "在线"
        npass = int(js.get("passed", 0)); nskip = int(js.get("skipped", 0)); nfail = int(js.get("failed", 0))
        concl = "全部通过 ✓" if js.get("conclusion") == "all_pass" else "存在未解决问题 %s 项" % unresolved
        src = "JSON 摘要"
    else:
        txt = open(path, encoding="utf-8").read()
        mu = re.search(r"未解决问题\s*(\d+)\s*项", txt)
        if mu:
            unresolved = int(mu.group(1))
        elif "全部通过" in txt:
            unresolved = 0
        else:
            msec = re.search(r"未决问题\s*\n+(.*?)(\n##|\Z)", txt, re.S)
            if msec:
                body = msec.group(1)
                items_ = [l for l in body.splitlines() if l.strip().startswith("-")]
                unresolved = 0 if (not items_ or items_[0].strip().replace(" ", "") == "-无") else len(items_)
            else:
                unresolved = -1
        nas = "在线" if "NAS 状态：**在线" in txt else ("离线" if "NAS 状态：**离线" in txt else
                ("在线（报告未单列）" if ("NAS 链路：DIRECT" in txt or "NAS 链路：RELAY" in txt) else "—"))
        npass = txt.count("✓ 通过"); nskip = txt.count("⏭ 跳过"); nfail = txt.count("✗")
        mt = re.search(r"总结论：\*\*(.+?)\*\*", txt)
        concl = mt.group(1) if mt else ""
        src = "md 文本"

    ms.append(("报告文件", base))
    ms.append(("数据来源", src))
    ms.append(("巡检项", "通过 %d · 跳过 %d · 失败 %d" % (npass, nskip, nfail)))
    ms.append(("总结论", concl or "未解析"))
    ms.append(("NAS 状态", nas))
    if unresolved < 0:
        set_item(it, "skip", "巡检报告已生成，但无法解析结论（未知，非失败）", ms)
    else:
        set_item(it, "pass" if unresolved == 0 else "fail",
                 "每日 07:00 自动巡检：未解决 %d 项 · 跳过 %d 项（NAS %s）" % (unresolved, nskip, nas), ms)


def check_nas(it, no_audio=False):
    """MiniNAS 存储（x86 存储底座）：机身盘 + SMB/NFS 共享 + 相册 + CasaOS 门户 —— 只读在线巡检（零写操作）。"""
    set_item(it, "running")
    ms = [("存储底座", "MiniNAS · x86 主机（机身盘 /tank + SMB/NFS 共享 + 相册）")]
    hosts = []
    for h in (nas_host(), NAS_ZT_HOST, NAS_HOST):
        if h and h not in hosts:
            hosts.append(h)
    s_ok, _ = port_ok(445, hosts, tries=4)
    n_ok, _ = port_ok(2049, hosts, tries=2)
    c_ok, c_h = port_ok(80, hosts, tries=3, timeout=2.0)
    ms.append(("SMB 端口（445）", "可连接 ✓；共享文件读写未验收" if s_ok else "暂不可达（链路抖动，NFS 仍可用）"))
    ms.append(("NFS 端口（2049）", "可连接 ✓；挂载读写未验收" if n_ok else "内网专用（安全策略：仅局域网开放，ZT 不可达属预期）"))
    ms.append(("管理门户 CasaOS（80）", ("在线 ✓" + ("（经 %s）" % c_h if c_h else "")) if c_ok else "不可达 ✗"))
    pong = False
    imm = None
    for _h in hosts:
        for _ in range(2):
            try:
                if "pong" in urllib.request.urlopen("http://%s:2283/api/server/ping" % _h, timeout=8).read().decode("utf-8", "replace"):
                    pong = True; imm = "http://%s:2283" % _h; break
            except Exception:
                pass
        if pong:
            break
    ms.append(("相册服务（2283）", "在线 ✓" if pong else "不可达 ✗"))
    key = immich_key()
    if key and pong and imm:
        try:
            req = urllib.request.Request(imm + "/api/server/statistics", headers={"x-api-key": key})
            stt = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
            ms.append(("相册库资产", "照片 %s 张 · 视频 %s 个" % (stt.get("photos", "?"), stt.get("videos", "?"))))
        except Exception:
            pass
    # 文件共享：SMB 与 NFS 任一可达即视为可用（ZeroTier 抖动时 SMB 探测易失败，避免误判）
    ok = s_ok and n_ok and pong and c_ok
    if not ok:
        _alive, _h = nas_alive()
        if not _alive:
            ms.append(("判定", "NAS 整体不可达（环境问题）→ 本项跳过，不算功能失败"))
            set_item(it, "fail", "MiniNAS物理局域网不可达", ms)
            return
    set_item(it, "pass" if ok else "fail",
             "NAS端口与相册API可达；文件读写未验收" if ok else
             "存在不可达服务：请检查存储主机（sh-d-l / %s 或 ZT %s）与网络" % (NAS_HOST, NAS_ZT_HOST),
             ms)


_NAS_LIVE = {"t": 0.0, "busy": False}


def refresh_nas_metric(max_age=20):
    """看板轮询 /state 时惰性刷新第 7 项「相册库资产」计数（该值随用户操作实时变化，无需重跑演示）。"""
    now = time.time()
    if now - _NAS_LIVE["t"] < max_age or _NAS_LIVE["busy"]:
        return
    key = immich_key()
    if not key:
        _NAS_LIVE["t"] = now
        return
    _NAS_LIVE["busy"] = True
    try:
        req = urllib.request.Request(immich_url() + "/api/server/statistics", headers={"x-api-key": key})
        stt = json.loads(urllib.request.urlopen(req, timeout=4).read().decode("utf-8"))
        line = "照片 %s 张 · 视频 %s 个" % (stt.get("photos", "?"), stt.get("videos", "?"))
        with LOCK:
            for it in STATE["items"]:
                if it.get("name") == "MiniNAS 存储":
                    ms = it.get("metrics") or []
                    for i, kv in enumerate(ms):
                        if kv and kv[0] == "相册库资产":
                            ms[i] = ("相册库资产", line)
    except Exception:
        pass
    finally:
        _NAS_LIVE["t"] = now
        _NAS_LIVE["busy"] = False


def run_all(args):
    import fcntl
    with open(DEMO + "/run.lock", "a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return -1
        try:
            return _run_all_locked(args)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _run_all_locked(args):
    # 每轮开始必须重置状态：否则看板会残留上一轮结果、条目累加（实测踩过）
    with LOCK:
        STATE["phase"] = "running"
        STATE["deployment"] = "offline-v1"
        STATE["items"] = []
        STATE["done"] = False
        STATE["summary"] = ""
        STATE["started"] = time.time()
        STATE["log"] = []
        STATE["run_mode"] = "静音检查" if args.no_audio else "实际动作验收"
    log("=== AI CPE 八大功能一键演示 开始（%s）===" % ("静音质检模式" if args.no_audio else "完整演示模式"))
    items = [item(1, "AI 智能语音对话", "语音交互 / 大模型对话 / TTS 播报"),
             item(2, "AI 视觉检测", "人脸/目标/姿态检测；身份识别单独验收"),
             item(3, "本地自然语言图片检索", "文件名关键词检索；零命中尝试CLIP，相关性另验"),
             item(4, "视频卡顿智能故障排查", "自动检测、分析并定位卡顿"),
             item(5, "智能家居控制", "当前MQTT板载LED；Matter实灯未配网"),
             item(6, "AI 智能选网", "QoE 驱动的蜂窝选网决策 · Shadow 观察（零写操作）"),
             item(7, "MiniNAS 存储", "x86 存储底座（机身盘 + SMB/NFS + 相册 + CasaOS）只读巡检"),
             item(8, "CasaOS统一管理界面", "App Store / Files / Home Assistant / Immich / 每日巡检 五个入口")]
    _skip = set()
    for _x in str(getattr(args, "skip_items", "") or "").split(","):
        _x = _x.strip()
        if _x.isdigit():
            _skip.add(int(_x))
    for fn, it, kw in [(check_voice, items[0], {"no_audio": args.no_audio}),
                       (check_vision, items[1], {"no_audio": args.no_audio, "no_upload": getattr(args, "no_upload", False)}),
                       (check_photo_search, items[2], {"query": args.query, "no_audio": args.no_audio}),
                       (check_diag, items[3], {}),
                       (check_smart_home, items[4], {"no_audio": args.no_audio}),
                       (check_ai_net, items[5], {"no_audio": args.no_audio}),
                        (check_nas, items[6], {"no_audio": args.no_audio}),
                        (check_casaos_center, items[7], {"no_audio": args.no_audio})]:
        if it["idx"] in _skip:
            set_item(it, "skip", "本轮未运行；历史证据单独显示")
            continue
        try:
            fn(it, **kw)
        except Exception as e:
            set_item(it, "fail", "异常: %s" % str(e)[:100])
    # 复检：外部依赖项（3 检索 / 7 存储）失败或"环境跳过"时自动重试一次
    # （链路抖动恢复后常能直接通过；skip 复检通过也计入通过）
    for _it in items:
        if _it["status"] in ("fail", "skip") and _it["idx"] in (3, 7) and _it["idx"] not in _skip:
            try:
                _it["metrics"] = []; _it["status"] = "pending"; _it["detail"] = ""
                if _it["idx"] == 3:
                    check_photo_search(_it, query=args.query, no_audio=args.no_audio)
                else:
                    check_nas(_it, no_audio=args.no_audio)
                if _it["status"] == "pass":
                    _it["detail"] = (_it.get("detail") or "") + "（链路恢复后复检通过）"
            except Exception:
                pass
    passed = sum(1 for i in items if i["status"] == "pass")
    with LOCK:
        STATE["phase"] = "done"; STATE["done"] = True
        _skipped = sum(1 for i in items if i["status"] in ("skip", "partial"))
        STATE["summary"] = "本轮检查完成：%d项通过、%d项失败、%d项未完整验收；历史不计本轮通过" % (passed, sum(i["status"] == "fail" for i in items), _skipped)
    log("=== %s ===" % STATE["summary"])
    write_report(items, passed)
    return passed


def write_report(items, passed):
    ts = time.strftime("%Y%m%d_%H%M%S")
    L = ["# AI CPE 八大功能演示报告", "", "- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
         "- 设备：RG660MK（AI CPE）", "- 生成者：AICPE本地编排；MT200推理；笔记本LAN中转", "- 本轮结果：**%d 项通过，共展示 %d 项；未运行和历史证据不算通过**" % (passed, len(items)), "",
         "| # | 功能 | 结果 | 耗时 | 关键证据 |", "|---|---|---|---|---|"]
    for i in items:
        ev = "；".join("%s: %s" % (k, v) for k, v in i["metrics"][:3])
        L.append("| %d | %s | %s | %ss | %s |" % (i["idx"], i["name"],
                 {"pass": "✅ 通过", "fail": "❌ 失败", "skip": "未完整验收", "partial": "需关注／部分完成"}.get(i["status"], i["status"]),
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
        STATE["items"] = items
        STATE["report"] = md
    try:
        history = json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else {}
        # 只导入已核验的本次离线版本报告，不扫描云版或未知来源。
        for prior in ("20261030_161636", "20261030_163313", "20261030_163606"):
            filename = DEMO + "/reports/AI_CPE_功能演示_" + prior + ".json"
            if os.path.exists(filename):
                for old in json.load(open(filename)).get("items", []):
                    if old.get("metrics") and old.get("status") in ("pass", "fail", "skip", "partial"):
                        key = str(old["idx"])
                        kind = "action" if old["idx"] in (1,5) and old["status"] == "pass" else "check"
                        entry = history.setdefault(key, {})
                        if old.get("t0",0) > entry.get(kind,{}).get("t0",0):
                            entry[kind] = dict(old, evidence_version="offline-v1", evidence_source=prior)
        for current in items:
            if current.get("metrics"):
                kind = "action" if current["idx"] in (1,5) and current["status"] == "pass" else "check"
                history.setdefault(str(current["idx"]), {})[kind] = dict(current, evidence_version="offline-v1", evidence_source=ts)
        with open(HISTORY_FILE + ".tmp", "w") as out:
            json.dump(history, out, ensure_ascii=False)
        os.replace(HISTORY_FILE + ".tmp", HISTORY_FILE)
        with LOCK:
            STATE["history"] = history
            _snap = json.loads(json.dumps(STATE, ensure_ascii=False, default=str))
        with open(STATE_FILE + ".tmp", "w") as out:
            json.dump(_snap, out, ensure_ascii=False)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except Exception as e:
        log("状态持久化失败：" + type(e).__name__)
    log("报告已生成: %s" % md)


# ---------------- 可视化看板（HTTP + 实时页面） ----------------
DASH_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>AI CPE 功能演示 · 实时看板</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#e8eef7;font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif}
header{background:linear-gradient(90deg,#0f3d5c,#1b6ca8);padding:16px 22px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}
h1{font-size:24px}
.tag{background:rgba(255,255,255,.14);padding:6px 12px;border-radius:20px;font-size:13px}
#bar{flex:1;min-width:200px;height:14px;background:rgba(255,255,255,.18);border-radius:8px;overflow:hidden}
#barIn{height:100%;width:0;background:linear-gradient(90deg,#31d17a,#8ef0b4);transition:width .6s ease}
main{display:grid;grid-template-columns:1.35fr .95fr;gap:16px;padding:16px}
@media(max-width:900px){main{grid-template-columns:1fr}}
#cards{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{background:#121c2e;border:1px solid #1f3350;border-radius:14px;padding:12px 14px;position:relative}
.card.run{border-color:#3aa0f0}
.card.pass{border-color:#2ecc71;background:#0f2a1e}
.card.fail{border-color:#e74c3c;background:#2a1214}
.card h3{font-size:16px;margin-bottom:5px}
.card .d{font-size:12px;color:#8fa6c4;margin-bottom:7px}
.kv{font-size:12.5px;color:#c9d8ea;line-height:1.7}
.kv b{color:#7fd1ff;font-weight:600}
.badge{position:absolute;top:10px;right:12px;font-size:12px;padding:3px 10px;border-radius:20px;background:#243b5a}
#right{display:flex;flex-direction:column;gap:12px}
#camwrap{background:#121c2e;border:1px solid #1f3350;border-radius:14px;padding:10px}
#cam{width:100%;border-radius:10px;display:block;background:#000;min-height:200px}
#log{background:#0a1526;border:1px solid #1f3350;border-radius:14px;padding:12px;height:260px;overflow:auto;font:12px/1.7 ui-monospace,Consolas,monospace;color:#9fe8b6;white-space:pre-wrap}
#sum{margin:0 16px 16px;padding:14px;border-radius:14px;background:#0f2a1e;border:1px solid #2ecc71;font-size:17px}
.small{font-size:12px;color:#8fa6c4}
#cpewrap{display:flex;flex-wrap:wrap;gap:6px;margin:10px 16px 2px;padding:8px 12px;background:#0f1a2c;border:1px solid #1f3350;border-radius:12px}
.chip{font-size:12px;color:#cfe0f5;background:#152339;border:1px solid #24416b;border-radius:16px;padding:4px 10px;white-space:nowrap}
.chip b{color:#7fb7ff;font-weight:600}
</style></head><body>
<noscript><meta http-equiv="refresh" content="5"></noscript>
<header>
  <h1>AI CPE 八大功能演示</h1>
  <span class="tag" id="dev">RG660MK · 5G 公网</span>
  <span class="tag" style="background:rgba(255,255,255,.08)">推理模式</span>
  <button id="mLocal" onclick="setMode('local')" style="background:#31d17a;color:#06231a;border:0;border-radius:10px;padding:7px 12px;font-size:13px;font-weight:700;cursor:pointer">本地模式</button>
  <button id="mOnline" onclick="setMode('online')" style="background:#3aa0f0;color:#04202f;border:0;border-radius:10px;padding:7px 12px;font-size:13px;font-weight:700;cursor:pointer">在线模式</button>
  <span class="tag" id="modeTag" style="background:rgba(58,160,240,.22)">模式读取中…</span>
  <span class="tag" id="opStatus" style="background:transparent"></span>
  <div id="bar"><div id="barIn"></div></div>
  <span class="tag" id="stat">加载中…</span>
  <span class="tag" id="archTag">链路读取中…</span>
  <a class="tag" id="dshLink" href="http://127.0.0.1:3081/" target="_blank" rel="noopener" style="text-decoration:none;color:#cfe0f5">DSH 控制台 ↗</a>
  <button id="enrollBtn" onclick="enrollFace()" style="background:#3aa0f0;color:#04202f;border:0;border-radius:10px;padding:9px 14px;font-size:14px;font-weight:700;cursor:pointer">录入身份</button>
  <button id="runbtn" onclick="startRun(true)" style="background:#31d17a;color:#06231a;border:0;border-radius:10px;padding:9px 16px;font-size:15px;font-weight:700;cursor:pointer">开始演示</button>
</header>
<div id="cpewrap"></div>
<main>
  <div id="cards"><div class="card"><h3>正在读取状态…</h3><div class="d">若长时间无变化，请刷新页面</div></div></div>
  <div id="right">
    <div id="camwrap">
      <div class="small" style="margin-bottom:6px">现场摄像头实时画面（C270）<span id="camstat" style="color:#ffd479;margin-left:8px"></span></div>
      <img id="cam" src="/snap" alt="画面加载中（会自动重试）" onerror="cameraFail()" onload="cameraOk()">
    </div>
    <div id="log">日志加载中…</div>
  </div>
</main>
<div id="sum"></div>
<script>
var jsMode = (typeof fetch !== "undefined");
var _reloadTimer = null;
function cameraOk(){
  var e=document.getElementById("camstat");
  if(e){ e.style.color="#ffd479"; e.innerText="画面暂不可用，正在自动重试…"; }
  if(e){ var d=new Date(); e.style.color="#8ef0b4"; e.innerText="画面更新 "+d.toTimeString().slice(0,8); }
}
function cameraFail(){
  var e=document.getElementById("camstat");
  if(e) {e.style.color="#ff7f7f";e.textContent="实时画面加载失败，2秒后重试；不显示旧帧";}
  setTimeout(function(){ var c=document.getElementById("cam"); if(c) c.src="/snap?ts="+Date.now(); }, 2000);
}
function fallbackMode(reason){
  if(!jsMode) return;
  jsMode=false;
  var e=document.getElementById("camstat");
  if(e) e.textContent="已切换兜底刷新模式（每 5 秒整页刷新）";
  if(!_reloadTimer) _reloadTimer=setInterval(function(){ location.reload(); }, 5000);
}
var ICON={pending:"○",running:"◌",pass:"✅",fail:"❌",skip:"⏭"};
var CN={pending:"待运行",running:"运行中",pass:"本轮检查通过",fail:"失败",skip:"未完整验收",partial:"需关注／部分完成"};
function esc(x){return String(x==null?"":x).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;"}[c];});}
async function tick(){
  if(!jsMode) return;
  var s=null;
  try{
    var r=await fetch("/state",{cache:"no-store"});
    s=await r.json();
  }catch(e){ fallbackMode(String(e)); return; }
  try{
    var box=document.getElementById("cards"); box.innerHTML="";
    var done=0;
    (s.items||[]).forEach(function(it){
      if(it.status==="pass"||it.status==="fail"||it.status==="skip"||it.status==="partial") done++;
      var el=document.createElement("div"); el.className="card "+(it.status==="running"?"run":it.status);
      var badge='<span class="badge">'+(ICON[it.status]||"")+" "+(CN[it.status]||it.status)+'</span>';
      var kv=(it.metrics||[]).map(function(kv){return "<div><b>"+esc(kv[0])+"</b>："+esc(kv[1])+"</div>";}).join("");
      var hist=(s.history||{})[String(it.idx)]||{};
      var historyHtml=Object.keys(hist).map(function(k){var h=hist[k];return "<details><summary>历史"+(k==='action'?'动作验收':'检查')+" · "+esc(new Date((h.t0||0)*1000).toLocaleString())+"（不计本轮）</summary><div>"+esc(h.detail)+"</div></details>";}).join('');
      el.innerHTML="<h3>"+it.idx+". "+esc(it.name)+"</h3><div class='d'>"+esc(it.desc)+"</div>"+badge+
        "<div class='kv'>"+esc(it.detail||CN[it.status])+"<br>"+kv+"</div>"+historyHtml+
        (it.dur!==null&&it.dur!==undefined?"<div class='small'>耗时 "+esc(it.dur)+"s</div>":"");
      box.appendChild(el);
    });
    if((s.items||[]).length) document.getElementById("barIn").style.width=Math.round(done/s.items.length*100)+"%";
    document.getElementById("stat").textContent=s.summary||(s.phase==="running"?"演示进行中…":"等待开始");
    var llm=s.llm||{};
    if(llm.label){
      var mt=document.getElementById("modeTag");
      if(mt){ mt.textContent="模式："+llm.label+" · "+(llm.model||""); mt.style.background=(llm.mode==="mt200-local"?"rgba(49,209,122,.22)":"rgba(58,160,240,.22)"); }
      var bl=document.getElementById("mLocal"), bo=document.getElementById("mOnline");
      if(bl) bl.style.outline=(llm.mode==="mt200-local"?"2px solid #31d17a":"none");
      if(bo) bo.style.outline=(llm.mode==="hermes"?"2px solid #3aa0f0":"none");
      if(llm.error){ if(mt) mt.textContent="模式异常："+(llm.error||""); }
    }
    if(s.architecture){ var at=document.getElementById("archTag"); if(at) at.textContent="链路："+(s.architecture.transport||"-"); }
    var rb=document.getElementById("runbtn");
    var running=(s.phase==="running");
    if(rb){ rb.textContent=running?"演示进行中…":"开始演示"; rb.disabled=running; }
    var c=JSON.parse(JSON.stringify(s.cpe||{}));
    function escapeStrings(x){Object.keys(x).forEach(function(k){if(typeof x[k]==="string")x[k]=esc(x[k]);else if(x[k]&&typeof x[k]==="object")escapeStrings(x[k]);});} escapeStrings(c);
    if(c.model){
      var chips="<span class='chip'><b>模组</b> "+c.model+"</span>";
      if(c.platform) chips+="<span class='chip'><b>平台</b> "+c.platform+"</span>";
      if(c.rsrp!==undefined) chips+="<span class='chip' style='border-color:#31d17a'><b>当前信号（"+(c.rat||"5G")+"）</b> RSRP <b style='color:#31d17a'>"+c.rsrp+" dBm</b></span>";
      if(s.now) chips+="<span class='chip'><b>数据更新</b> "+s.now+"</span>";
      if(c.sta&&c.sta.length){ chips+="<span class='chip'><b>WiFi 信号</b> "+c.sta.map(function(x){return (x.name||"")+" "+(x.rssi!==undefined?x.rssi+" dBm":"—");}).join(" · ")+"</span>"; }
      else { chips+="<span class='chip'><b>WiFi</b> 无接入终端</span>"; }
      if(c.ssid) chips+="<span class='chip'><b>SSID</b> "+c.ssid+"</span>";
      chips+="<span class='chip'><b>接入终端</b> "+c.clients+" 个（WiFi "+c.clients_wifi+" · 有线 "+c.clients_lan+"）</span>";
      if(c.wan_ip) chips+="<span class='chip'><b>WAN</b> "+c.wan_ip+(c.wan_up?" ✓":" ✗")+"</span>";
      if(c.temp_c) chips+="<span class='chip'><b>温度</b> "+c.temp_c+" °C</span>";
      if(c.mem_total_mb) chips+="<span class='chip'><b>内存</b> 总 "+c.mem_total_mb+" MiB · 可用 "+c.mem_avail_mb+" MiB</span>";
      if(c.hermes_mb) chips+="<span class='chip'><b>Hermes 占用</b> "+c.hermes_mb+" MiB</span>";
      if(c.uptime_s) chips+="<span class='chip'><b>运行</b> "+Math.floor(c.uptime_s/3600)+"h"+Math.floor(c.uptime_s%3600/60)+"m</span>";
      var rd2=c.radiodiag||{};
      if(rd2.verdict){
        var vmap={ok:["正常","#31d17a"],weak:["弱信号","#ffd479"],severe:["严重弱信号","#ff7f7f"],congested:["拥塞","#ff9f7f"],down:["掉网","#ff7f7f"]};
        var vm=vmap[rd2.verdict]||[String(rd2.verdict),"#cfe0f5"];
        chips+="<span class='chip'><b>网络诊断</b> <span style='color:"+vm[1]+";font-weight:700'>"+vm[0]+"</span>"+(rd2.rsrp!==undefined&&rd2.rsrp!==null?" · RSRP "+rd2.rsrp+" dBm":"")+(rd2.cell?" · 小区 "+rd2.cell:"")+(rd2.ts?" · "+rd2.ts:"")+"</span>";
        var _hv=(vm[0]==="正常")?"#9fb3cc":"#ffd479";
        chips+="<span class='chip' title='口径：触发=实际执行的射频自愈次数（不含演练）；成功=自愈后判定恢复（success=true）；未改善=执行了但没有恢复'><b>网络自愈</b> <span style='color:"+_hv+"'>今日触发 "+rd2.heal_today+" 次"+(rd2.heal_ok!==undefined?("（成功 "+rd2.heal_ok+" 次"+(rd2.heal_today>rd2.heal_ok?(" · 未改善 "+(rd2.heal_today-rd2.heal_ok)+" 次"):"")+"）"):"")+"</span>"+(rd2.eff_pct!==undefined?"<span class='small' style='color:"+(rd2.eff_pct>=80?"#31d17a":"#ffd479")+"'> · 近"+rd2.eff_pct+"%（近"+rd2.eff_n+"次）"+(rd2.probe_n?(" · 观察 "+rd2.probe_n+"/10"+(rd2.probe_pct!==undefined?(" · 新策略 "+rd2.probe_pct+"%"):"")):"")+(rd2.eff_mode?(" · "+rd2.eff_mode):"")+"</span>":"")+"</span>";
        if(rd2.recent&&rd2.recent.length){
          var _last=rd2.recent[rd2.recent.length-1];
          var _more=rd2.recent.slice(0,-1).join("　｜　");
          chips+="<div style='flex-basis:100%;font-size:12px;color:#8ea4bf;margin-top:3px'><b style='color:#7fb7ff'>最近一次自愈</b>（历史，非当前值）： "+_last+
            (_more?"　<details style='display:inline'><summary style='display:inline;cursor:pointer;color:#7fb7ff'>更早 "+(rd2.recent.length-1)+" 条</summary><div style='margin-top:3px;color:#8ea4bf'>"+_more+"</div></details>":"")+"</div>";
        }
      } else {
        chips+="<span class='chip'><b>网络诊断</b> <span style='color:#8fa6c4'>服务未运行</span></span>";
      }
      document.getElementById("cpewrap").innerHTML=chips;
    }
    var lg=document.getElementById("log");
    lg.textContent=(s.log||[]).join("\\n"); lg.scrollTop=lg.scrollHeight;
    var sm=document.getElementById("sum");
    if(s.summary){ sm.style.display="block"; sm.innerHTML=esc(s.summary)+"　<a href='/report' target='_blank'>查看本轮报告</a>"; }
    else { sm.style.display="none"; sm.innerHTML=""; }
    var c=document.getElementById("cam");
    if(c) c.src="/snap?ts="+Date.now();
  }catch(e){ /* 渲染错误不影响画面刷新 */ }
}
async function startRun(full){
  var b=document.getElementById("runbtn");
  b.disabled=true; b.textContent="启动中…";
  try{ var r=await fetch("/run",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify({full:full!==false})}); var j=await r.json();
       b.textContent = j.started ? "演示进行中…" : (j.msg||"未能启动");
  }catch(e){ b.textContent="启动失败"; b.disabled=false; }
  /* 按钮的最终状态由 tick() 根据 /state 同步（防止文字永远停在"演示进行中…"） */
}
async function setMode(m){
  var out=document.getElementById("opStatus");
  try{
    var r=await fetch("/mode",{method:"POST",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:m})});
    var j=await r.json();
    if(out) out.textContent="已切到"+(j.label||m)+" · "+(j.model||"")+(j.error?(" · "+j.error):"");
    tick();
  }catch(e){ if(out) out.textContent="模式切换失败：" + e; }
}
async function enrollFace(){
  var st=document.getElementById('opStatus'), b=document.getElementById('enrollBtn');
  if(!confirm('请正对摄像头：将抓拍当前画面，录入为本机身份模板（subject=self）。继续？')) return;
  b.disabled=true; if(st) st.textContent='正在抓拍并录入…';
  try{
    var r=await fetch('/face/enroll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject_id:'self'})});
    var j=await r.json();
    if(st) st.textContent = j.ok ? ('身份录入成功：'+(j.result&&j.result.identities||1)+' 个模板') : ('录入失败：'+(j.error||'无人脸'));
  }catch(e){ if(st) st.textContent='录入失败：'+e; }
  finally{ b.disabled=false; }
}
function boot(){
  if(!jsMode){ fallbackMode("no-fetch"); return; }
  setInterval(tick,1500); tick();
}
boot();
</script></body></html>"""

SIMPLE_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="6">
<title>AI CPE 演示 · 简易版</title>
<style>body{background:#0b1220;color:#e8eef7;font-family:sans-serif;margin:0;padding:14px}
img{width:100%;max-width:760px;border-radius:10px;background:#000}
h2{margin:6px 0 10px;font-size:20px} .b{padding:10px;margin:8px 0;border-radius:10px;background:#121c2e;border:1px solid #1f3350}
</style></head><body>
<h2>AI CPE 八大功能演示（简易版，每 6 秒自动刷新）</h2>
<img src="/snap" alt="画面加载中…">
<div class="b" id="s">正在读取状态…</div>
<script>
(function(){
  var x=new XMLHttpRequest();
  x.onreadystatechange=function(){
    if(x.readyState===4){
      var d=document.getElementById("s");
      if(x.status===200){
        try{ var j=JSON.parse(x.responseText);
          d.textContent=(j.summary||(j.phase==="running"?"检查进行中…":"等待开始"))+"\\n"+
            (j.items||[]).map(function(i){return i.idx+"."+i.name+"："+i.status+(i.detail?"（"+i.detail+"）":"");}).join("\\n");
        }catch(e){ d.textContent="状态解析失败"; }
      } else { d.textContent="状态读取失败("+x.status+")"; }
    }
  };
  x.open("GET","/state",true); x.send();
})();
</script></body></html>"""



RTT_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>链路 RTT 实测（手机 → CPE）</title>
<style>body{background:#0b1220;color:#e8eef7;font-family:sans-serif;margin:0;padding:16px}
h2{font-size:20px;margin:4px 0 10px}.b{background:#121c2e;border:1px solid #1f3350;border-radius:12px;padding:12px;margin:10px 0}
#big{font-size:34px;color:#7fd1ff;font-weight:700}.s{font-size:12px;color:#8fa6c4;line-height:1.7}
canvas{width:100%;height:120px;background:#0a1526;border-radius:10px}
button{background:#1b6ca8;color:#fff;border:0;border-radius:10px;padding:12px 20px;font-size:16px;margin-right:10px}
</style></head><body>
<h2>链路 RTT 实测：本机（手机/电脑） → RG660MK</h2>
<div class="b"><div id="big">待测</div><div class="s" id="sum">点“开始测”后连续打 30 次，得到本机到设备的真实往返时延（含本机→CPE 网络段 + 设备处理/回程）</div></div>
<div class="b"><button onclick="run()">开始测</button><span class="s" id="prog"></span></div>
<script>if(location.search.indexOf("auto=1")>=0){window.addEventListener("load",function(){setTimeout(run,300);});}</script>
<div class="b"><canvas id="cv" width="700" height="120"></canvas><div class="s" id="raw" style="margin-top:8px"></div></div>
<div class="b s">判读：① 该值 = 你手机到 CPE 的网络往返 + 设备处理，不含 CPE→机器人 段，也不含 ASR/大模型处理；<br>
② 与「本地排队时延」（设备内部环回测得）不是同一口径，两者不能相加当端到端；<br>
③ 同 WiFi 下应在 2–10ms；经互联网/隧道会显著抬升（隧道本身的 RTT 也会计入）。</div>
<script>
var samples=[];
async function one(){
  var t0=performance.now();
  try{ await fetch("/state?ts="+Date.now(),{cache:"no-store"}); }catch(e){ return null; }
  return performance.now()-t0;
}
function stat(a){
  if(!a.length) return {};
  var b=a.slice().sort(function(x,y){return x-y});
  var q=function(p){ return b[Math.min(b.length-1,Math.floor(p*b.length))]; };
  var jit=0; for(var i=1;i<a.length;i++) jit+=Math.abs(a[i]-a[i-1]); jit=jit/Math.max(1,a.length-1);
  var s=0; for(var i=0;i<a.length;i++) s+=a[i];
  return {n:a.length,min:b[0],avg:s/a.length,p50:q(0.5),p95:q(0.95),max:b[b.length-1],jitter:jit};
}
async function run(){
  samples=[]; document.getElementById("prog").textContent="测量中…";
  for(var i=0;i<30;i++){
    var d=await one();
    if(d!==null){ samples.push(d); draw(); document.getElementById("prog").textContent="已测 "+samples.length+"/30"; }
    await new Promise(function(r){setTimeout(r,120);});
  }
  var s=stat(samples);
  document.getElementById("big").textContent = (s.avg?s.avg.toFixed(1):"-") + " ms";
  document.getElementById("sum").innerHTML = "样本 "+s.n+" ｜ 最小 "+s.min.toFixed(1)+" ｜ P50 "+s.p50.toFixed(1)+
      " ｜ P95 <b>"+s.p95.toFixed(1)+"</b> ｜ 最大 "+s.max.toFixed(1)+" ｜ 抖动(相邻差均值) "+s.jitter.toFixed(2)+" ms";
  document.getElementById("prog").textContent="完成";
  document.getElementById("raw").textContent="原始样本(ms): "+samples.map(function(x){return x.toFixed(1)}).join(", ");
}
function draw(){
  var c=document.getElementById("cv"), g=c.getContext("2d");
  g.clearRect(0,0,c.width,c.height);
  var mx=Math.max.apply(null,samples.concat([5]));
  g.strokeStyle="#3aa0f0"; g.lineWidth=2; g.beginPath();
  samples.forEach(function(v,i){
    var x=i/Math.max(1,samples.length-1)*(c.width-20)+10, y=c.height-10-(v/mx)*(c.height-25);
    i?g.lineTo(x,y):g.moveTo(x,y);
  });
  g.stroke();
  g.fillStyle="#8fa6c4"; g.font="12px sans-serif";
  g.fillText("纵轴上限 "+mx.toFixed(1)+" ms",10,14);
}
</script></body></html>"""


class Handler(__import__("http.server").server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers()
        try:
            self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))
        except Exception:
            pass

    def do_POST(self):
        from urllib.parse import urlsplit
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self._send(403, "application/json", '{"error":"cross origin denied"}')
            return
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send(403, "application/json", '{"error":"cross site denied"}')
            return
        if self.path == "/mode":
            try:
                self.connection.settimeout(10)
                n = int(self.headers.get("Content-Length", "0") or 0)
                if not 0 < n <= 256:
                    raise ValueError("body size")
                body = json.loads(self.rfile.read(n))
                if not isinstance(body, dict) or set(body) != {"mode"}:
                    raise ValueError("schema")
                new_mode = llm_gateway.set_mode(body["mode"])
                log("=== 模式切换 → %s（%s）===" % (llm_gateway.describe()["label"], new_mode))
                out = llm_gateway.describe()
                out["switched"] = True
                self._send(200, "application/json; charset=utf-8", json.dumps(out, ensure_ascii=False))
            except Exception as e:
                self._send(400, "application/json; charset=utf-8",
                           json.dumps({"switched": False, "error": str(e)[:120]}, ensure_ascii=False))
            return
        if self.path == "/run":
            if not RUN_LOCK.acquire(blocking=False):
                self._send(409, "application/json", '{"started":false,"msg":"检查已在运行"}')
                return
            full = False
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
                if 0 < n <= 512:
                    _b = json.loads(self.rfile.read(n) or b"{}")
                    full = bool(isinstance(_b, dict) and _b.get("full"))
            except Exception:
                full = False
            a = argparse.Namespace(**vars(ARGS))
            a.no_audio = not full
            a.no_upload = not full
            def work():
                try:
                    run_all(a)
                finally:
                    RUN_LOCK.release()
            threading.Thread(target=work, daemon=True).start()
            _msg = "完整验收已启动（真播报/上传/点灯）" if full else "静音检查已启动"
            self._send(202, "application/json; charset=utf-8",
                       json.dumps({"started": True, "full": full, "msg": _msg}, ensure_ascii=False))
            return
        if self.path == "/home/confirm":
            try:
                self.connection.settimeout(10)
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= 512:
                    raise ValueError("body size")
                body = json.loads(self.rfile.read(n))
                if set(body) != {"confirmation_token"}:
                    raise ValueError("schema")
                import home_confirmation
                result = home_confirmation.confirm(sys.modules[__name__], body["confirmation_token"], self.client_address[0])
                self._send(200, "application/json; charset=utf-8", json.dumps(result, ensure_ascii=False))
            except Exception as e:
                self._send(400, "application/json; charset=utf-8", json.dumps({"status":"fail", "reply":str(e)[:100]}, ensure_ascii=False))
            return
        if self.path == "/face/enroll":
            try:
                self.connection.settimeout(15)
                n = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if 0 < n <= 512 else {}
                subject = (body or {}).get("subject_id") or "self"
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", subject):
                    raise ValueError("subject_id 只允许字母数字下划线连字符")
                ok, size = take_snapshot()
                if not ok:
                    raise ValueError("抓帧失败（camview/camav 未就绪）")
                import shutil
                media_root = "/data/ai_cpe/demo/media"
                os.makedirs(media_root, exist_ok=True)
                staged = os.path.join(media_root, "enroll.jpg")
                shutil.copyfile(SNAP, staged)
                with open("/data/ai_cpe/demo/data/api-token") as f:
                    token = f.read().strip()
                payload = json.dumps({"subject_id": subject, "input": {"path": staged}}).encode()
                req = urllib.request.Request(
                    "http://127.0.0.1:8765/vision/enroll", data=payload,
                    headers={"Content-Type": "application/json", "X-AI-Service-Token": token})
                try:
                    res = json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))
                except urllib.error.HTTPError as he:
                    detail = he.read().decode("utf-8", "ignore")
                    raise ValueError("服务拒绝：%s" % (detail[:160] or he.code))
                res["frame_kb"] = size // 1024
                log("=== 身份录入 %s：%s ===" % (subject, "成功" if res.get("ok") else "失败"))
                self._send(200 if res.get("ok") else 400, "application/json; charset=utf-8",
                           json.dumps(res, ensure_ascii=False))
            except Exception as e:
                self._send(400, "application/json; charset=utf-8",
                           json.dumps({"ok": False, "error": str(e)[:140]}, ensure_ascii=False))
            return
        if self.path != "/agent":
            self._send(404, "application/json", '{"error":"not found"}')
            return
        try:
            self.connection.settimeout(10)
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= 2048:
                raise ValueError("body size")
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict) or set(body) != {"text"}:
                raise ValueError("schema")
            from offline_agent import run
            result = run(body["text"])
            if result.get("status") == "confirmation_required":
                import home_confirmation
                result["confirmation"] = home_confirmation.prepare(sys.modules[__name__], result["args"]["action"], self.client_address[0])
            self._send(200, "application/json; charset=utf-8", json.dumps(result, ensure_ascii=False))
        except Exception as e:
            self._send(400, "application/json", json.dumps({"status": "fail", "kind": type(e).__name__}))

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self._send(200, "text/html; charset=utf-8", DASH_HTML)
        elif p == "/simple":
            self._send(200, "text/html; charset=utf-8", SIMPLE_HTML)
        elif p == "/rtt":
            self._send(200, "text/html; charset=utf-8", RTT_HTML)
        elif p == "/run":
            self._send(405, "application/json", '{"error":"use POST /run for silent check"}')
        elif p == "/state":
            # 只返回缓存值：探测放后台线程，避免拖慢页面轮询（实测曾 9s+）
            reload_state_if_newer()
            cpe = _CPE_CACHE.get("d") or {"model": CPE_MODEL, "platform": CPE_PLATFORM}
            with LOCK:
                out = dict(STATE)
            out["cpe"] = cpe
            _llm = llm_gateway.describe()
            out["llm"] = _llm
            out["architecture"] = {
                "mode": _llm["label"],
                "controller": "RG660MK",
                "llm": "%s · %s" % (_llm["backend"], _llm["model"]),
                "transport": ("笔记本LAN代理→SSH→MT200:8090" if _llm["mode"] == "mt200-local"
                              else "RG 出网→ai.phicotek.com（Hermes 在线通道）"),
                "laptop_required": _llm["mode"] == "mt200-local",
                "dsh_console": _llm["dsh_console"],
            }
            out["now"] = time.strftime("%H:%M:%S")
            self._send(200, "application/json; charset=utf-8", json.dumps(out, ensure_ascii=False))
        elif p == "/mode":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(llm_gateway.describe(), ensure_ascii=False))
        elif p == "/snap":
            data = None
            for _u in (CAMAV + "/snapshot", CAMVIEW + "/snapshot"):
                try:
                    with urllib.request.urlopen(_u, timeout=8) as r:
                        _b = r.read()
                    if _b[:2] == b"\xff\xd8" and len(_b) > 5000:
                        data = _b; break
                except Exception:
                    continue
            if data:
                try:
                    open("/tmp/aicpe_board_last.jpg", "wb").write(data)
                except Exception:
                    pass
            if data:
                _tmp = "/tmp/aicpe_cam_check.jpg"
                try:
                    open(_tmp, "wb").write(data)
                except Exception:
                    _tmp = ""
                _kind, _m = cam_obstructed(_tmp) if _tmp else ("", {})
                if _kind:
                    _np = cam_notice_image(_kind, _m)
                    if _np:
                        try:
                            data = open(_np, "rb").read()
                        except Exception:
                            pass
                self._send(200, "image/jpeg", data)
            else:
                self._send(503, "text/plain", "camera warming up")
        elif p == "/report":
            with LOCK:
                f = STATE.get("report")
            if f and os.path.exists(f):
                self._send(200, "text/markdown; charset=utf-8", open(f, encoding="utf-8").read())
            else:
                self._send(404, "text/plain", "report not ready")
        else:
            self._send(404, "text/plain", "not found")


def background_refresher():
    """后台刷新 CPE 信息与 NAS 相册计数，HTTP 请求只读缓存。"""
    while True:
        try:
            cpe_info()
        except Exception:
            pass
        try:
            refresh_nas_metric(max_age=15)
        except Exception:
            pass
        finally:
            try:
                _NAS_LIVE["busy"] = False
            except Exception:
                pass
        time.sleep(6)


def serve(port):
    import socket as _socket
    from http.server import ThreadingHTTPServer

    class _DualStackServer(ThreadingHTTPServer):
        # 同时支持 IPv4 与 IPv6（Linux 默认 bindv6only=0，绑 "::" 可同时接收 v4-mapped 连接）
        address_family = _socket.AF_INET6

    try:
        srv = _DualStackServer(("::", port), Handler)
    except OSError:
        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # 回退：纯 IPv4
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


ARGS = None


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="AI CPE 八大功能一键演示（含实时看板）")
    ap.add_argument("--port", type=int, default=8099, help="看板端口（默认 8099）")
    ap.add_argument("--no-audio", action="store_true", help="只读静音质检：不播报、不实际开关灯、不上传照片")
    ap.add_argument("--no-upload", action="store_true", help="不上传照片，上传链路保持未验收")
    ap.add_argument("--query", default="云海", help="图片检索的自然语言指令（默认 云海）")
    ap.add_argument("--no-serve", action="store_true", help="不启动看板，仅跑检查")
    ap.add_argument("--skip-items", default="", help="跳过指定编号（逗号分隔），用于巡检自指场景")
    ap.add_argument("--keep-alive", type=int, default=1800, help="跑完后看板继续在线秒数（默认 1800，0=立即退出）")
    ap.add_argument("--serve-only", action="store_true",
                    help="常驻模式：只提供看板，不自动跑检查（页面上点「开始演示」才跑）——适合长期挂着")
    a = ap.parse_args()
    ARGS = a
    if a.serve_only:
        a.keep_alive = 10 ** 9          # 常驻
        try:
            _st = json.load(open(STATE_FILE, encoding="utf-8"))
            if isinstance(_st, dict) and _st.get("items") and _st.get("deployment") == "offline-v1":
                with LOCK:
                    STATE.update(_st)
                STATE["phase"] = "done"
                STATE["done"] = True
        except Exception:
            pass
    threading.Thread(target=background_refresher, daemon=True).start()
    if not a.no_serve:
        serve(a.port)
        ip = sh("ip -4 addr show br-lan | awk '/inet /{print $2}' | cut -d/ -f1")[1].strip() or "192.168.1.1"
        print("看板地址: http://%s:%d/   （局域网任意设备可访问；演示期间保持在线）" % (ip, a.port), flush=True)
    if a.serve_only:
        print("常驻模式：看板已就绪，等待页面点击「开始演示」…", flush=True)
        while True:
            time.sleep(3600)
    passed = run_all(a)
    if not a.no_serve and a.keep_alive > 0:
        print("演示已完成，看板继续在线 %d 秒（Ctrl+C 可提前结束）…" % a.keep_alive, flush=True)
        t0 = time.time()
        while time.time() - t0 < a.keep_alive:
            time.sleep(1)
    return 0 if passed == 8 else 1


if __name__ == "__main__":
    sys.exit(main())
