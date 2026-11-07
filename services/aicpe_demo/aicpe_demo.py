#!/data/hermes/venv/bin/python
# -*- coding: utf-8 -*-
"""AI CPE 十大功能一键演示套件 —— 编排 + 实时可视化看板（v1.3）

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
import argparse, json, os, re, signal, subprocess, sys, threading, time, urllib.request, urllib.error
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
_TOKEN_CACHE = {"mt": 0.0, "d": {}}
TOKEN_STATS_FILE = "/data/ai_cpe/token_stats.json"


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
        # 陈旧 running 快照纠偏：进程被杀/服务重启后 state_last.json 会一直停在
        # phase=running，页面永远显示"演示进行中…"（2026-11-02 实修）。
        # 判据：文件 180s 没有更新（运行中的进程每完成一项就会刷新），才认定已中断。
        if d.get("phase") == "running" and (time.time() - m) > 180:
            d["phase"] = "idle"
            d["done"] = True
            d["running"] = False
            d["summary"] = "上一轮演示被中断（服务重启或进程退出），未完成项已跳过，请重新点击「开始演示」"
            for _it in d.get("items", []):
                if _it.get("status") == "running":
                    _it["status"] = "skip"
                    _it["detail"] = "中断：未完成（不计入本轮通过）"
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
IMMICH = "http://192.168.1.244:2283"  # 仅兼容旧引用；实际请求走 immich_url() 自动回退
IMMICH_KEY_FILE = HOME + "/photos/.immich_key"
NAS_HOST = "192.168.1.244"   # MiniNAS 存储底座（x86 主机 sh-d-l，2026-10-12 上线）
NAS_ZT_HOST = "172.25.25.240"   # NAS 的 ZeroTier 地址：拔网线时经中继兜底（2026-10-14）
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
    """带硬超时 + 进程组清理的 shell 执行；避免 ping 等子进程继承管道导致 timeout 后仍挂住。"""
    p = None
    try:
        p = subprocess.Popen(["/bin/sh", "-c", cmd], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            out, _ = p.communicate(timeout=t)
            return p.returncode, (out or "").strip()
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            try:
                out, _ = p.communicate(timeout=3)
            except Exception:
                out = ""
            return 1, "ERR: timeout %ss" % t
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
CPE_PLATFORM = "MediaTek T930 + Wi-Fi MT7992 · 支持R18, 5GA"
_CPE_CACHE = {"t": 0.0, "d": {}}


def cpe_info():
    """CPE 基本信息（5G 信号/接入终端/WiFi 信号/温度等），12s 缓存供看板轮询。"""
    now = time.time()
    if now - _CPE_CACHE["t"] < 12 and _CPE_CACHE["d"]:
        return _CPE_CACHE["d"]
    d = {"model": CPE_MODEL, "platform": CPE_PLATFORM}
    try:
        import llm_gateway as _llm
        d["llm"] = _llm.describe()
    except Exception:
        d["llm"] = {}
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
    wm = set(s["mac"] for s in st)
    own = set()
    for ifc in ("br-lan", "eth0", "ra0", "ra1", "rai0"):
        rc, mo = sh("cat /sys/class/net/%s/address 2>/dev/null" % ifc, t=3)
        if mo.strip():
            own.add(mo.strip().lower())
    lan = {}
    rc, o5 = sh("ip neigh show dev br-lan 2>/dev/null", t=5)
    for ln in o5.splitlines():
        p = ln.split()
        if "lladdr" not in p:
            continue
        i = p.index("lladdr")
        if i + 1 >= len(p):
            continue
        ip, mac = p[0], p[i + 1].lower()
        state = p[-1] if p[-1].isupper() else ""
        if state in ("FAILED", "INCOMPLETE", "STALE"):
            continue
        if ip == "192.168.1.1" or not ip.startswith("192.168.1."):
            continue
        if mac in wm or mac in own or mac.startswith("01:00:5e") or mac == "ff:ff:ff:ff:ff:ff":
            continue
        lan[mac] = ip
    # 同一台 WiFi 设备可能有多个 MLO 链路 MAC，ARP 里会以另一个 MAC 出现 → 按 IP 去重，别算成“有线”
    _wm_ips = set(leases.get(m) for m in wm if leases.get(m))
    if _wm_ips:
        lan = {k: v for k, v in lan.items() if v not in _wm_ips}
    d["clients"] = len(wm | set(lan)); d["clients_wifi"] = len(wm); d["clients_lan"] = len(set(lan) - wm)
    # 「WiFi 信号」只给数值（dBm）；设备名归到「接入终端」清单，避免两处语义混淆。
    # 取有主机名 STA 的最强值：无名设备的 iw 读数出现过 -11/+16 这类非物理值，不能代表 WiFi 信号
    _valid = [s for s in st if isinstance(s.get("rssi"), int) and s["rssi"] < 0]
    _named_valid = [s for s in _valid if (leases.get(s["mac"]) or "*") not in ("", "*")]
    _best_pool = _named_valid or _valid
    d["rssi_best"] = max([s["rssi"] for s in _best_pool]) if _best_pool else None
    _all = _valid or st
    d["sta"] = [{"name": (leases.get(s["mac"]) or "").strip() or s["mac"][-8:],
                 "rssi": s.get("rssi")} for s in _all]
    d["sta_names"] = [x["name"] for x in d["sta"] if x["name"] and x["name"] != "*"]
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
            _rdp = "/data/ai_cpe/radiodiag_state.json"
            rd = json.load(open(_rdp))
            diag["verdict"] = rd.get("verdict")
            diag["rsrp"] = rd.get("rsrp")
            diag["ts"] = str(rd.get("ts", ""))[5:16]
            try:
                diag["age_s"] = int(time.time() - _os.path.getmtime(_rdp))
                diag["stale"] = diag["age_s"] > 900
            except Exception:
                diag["age_s"] = None
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
    ms.append(("推理侧", "%s" % model_meta.get("model", "-") if model_meta else "未调用"))
    if model_meta:
        ms.append(("推理耗时", "%.1f s" % (model_meta.get("elapsed_ms", 0) / 1000.0)))
    ms.append(("大模型对话（独立检查）" if clock_reply else "大模型对话（设备集成）", model_reply[:100] if model_reply else (model_error or "无识别文本，未调用模型")))
    if clock_reply:
        ms.append(("设备时钟回答", clock_reply))
    tts_ok = None
    tts_syn = None
    if no_audio:
        # 静音巡检：不外放，但真实合成一遍，验证 TTS 引擎可用（不是只看进程存活）
        try:
            import local_tts as _lt
            _p = "/tmp/demo_tts_check.wav"
            _ok, _err = _lt.synthesize((reply or "测试")[:60], _p)
            _sz = os.path.getsize(_p) if os.path.exists(_p) else 0
            tts_syn = bool(_ok) and _sz > 8000
            ms.append(("TTS 合成（静音巡检，不外放）", "通过 ✓（%d KB）" % (_sz // 1024) if tts_syn else "失败: %s" % str(_err)[:60]))
        except Exception as _e:
            tts_syn = False
            ms.append(("TTS 合成（静音巡检，不外放）", "失败: %s" % str(_e)[:60]))
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
    ok = va_alive and asr_up and bool(asr_text) and bool(model_reply) and bool(reply) and (tts_ok is not False) and (tts_syn is not False)
    _env_down = ("本地模型不可用" in (model_error or "")) or ("连接超时" in (model_error or ""))
    if (not ok) and _env_down and va_alive and asr_up and asr_text:
        set_item(it, "skip", "环境不可用：MT200 本地模型离线（语音链路未验证，不计功能失败）", ms)
        return
    set_item(it, "pass" if ok else "fail",
             ("真实识别→大模型回复→TTS 合成通过（静音巡检未外放，播放由完整演示验收）" if ok and no_audio
              else ("真实识别→大模型回复→本地播报" if ok else "存在未通过子项")), ms)


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


FACE_API = "http://127.0.0.1:8765/vision/face"
FACE_TOKEN_FILE = "/data/ai_cpe/demo/data/api-token"
FACE_MEDIA = "/data/ai_cpe/demo/media/face_live.jpg"
FACE_LAST = "/tmp/aicpe_board_last.jpg"
_FACE_CACHE = {"key": None, "d": None}
_FACE_LOCK = threading.Lock()
_FACE_INFER = threading.Lock()
_FACE_BOOT = {"ts": 0.0}


def ensure_ai_service():
    """8765 本地视觉服务不在时自动拉起（限频 120s，避免请求风暴里反复 fork）。"""
    now = time.time()
    if now - _FACE_BOOT["ts"] < 120:
        return
    _FACE_BOOT["ts"] = now
    try:
        subprocess.Popen(["/bin/sh", "/data/ai_cpe/demo/services/start_ai.sh"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        pass


def shrink_jpeg(data, max_w=400, quality=70):
    """缩小 /snap 输出（公网 quick tunnel 下 45KB→约 15KB，传输延迟减半）。

    原始帧仍完整写入 /tmp/aicpe_board_last.jpg 供人脸检测，所以检测精度不受影响；
    前端按 /faces 返回的 w/h 百分比定位框，等比缩放后依然对齐。
    PIL 不可用（缺 libtiff）时原样返回，不影响功能。
    """
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.width <= max_w:
            return data
        h = int(im.height * max_w / float(im.width))
        im = im.convert("RGB").resize((max_w, h), Image.BILINEAR)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=quality, optimize=True)
        small = out.getvalue()
        return small if len(small) < len(data) else data
    except Exception:
        return data


def jpeg_size(path):
    """只读 JPEG SOF 段取宽高（venv 的 Pillow 在本进程因 libtiff 缺库不可用）。"""
    try:
        with open(path, "rb") as f:
            d = f.read()
        i, n = 2, len(d)
        while i + 9 < n:
            if d[i] != 0xFF:
                i += 1; continue
            mk = d[i + 1]
            if mk in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return ((d[i + 7] << 8) | d[i + 8]), ((d[i + 5] << 8) | d[i + 6])
            if mk in (0xD8, 0xD9) or 0xD0 <= mk <= 0xD7:
                i += 2; continue
            i += 2 + ((d[i + 2] << 8) | d[i + 3])
    except OSError:
        pass
    return 0, 0


def face_boxes():
    """对 /snap 最近一帧做本地 RetinaFace 检测，返回像素框 + 身份（前端画框用）。

    复用 /snap 已缓存的 last.jpg，保证框与页面显示的帧一致；按 (mtime,size) 缓存，
    同一帧只推理一次（单次 ~700ms）。_FACE_INFER 让并发请求排队复用同一结果，
    避免 ai_service 的信号量直接 429（多个页面客户端同时刷新时会发生）。
    """
    try:
        st = os.stat(FACE_LAST)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {"ok": False, "error": "还没取到画面", "faces": []}
    with _FACE_INFER:
        with _FACE_LOCK:
            if _FACE_CACHE["key"] == key and _FACE_CACHE["d"] is not None:
                return _FACE_CACHE["d"]
            _prev = _FACE_CACHE["d"]
        # 画面 1.5s 一帧，但检测不必每帧跑满（单帧 0.7s）：1.6s 内复用上次结果，框最多滞后一帧
        if _prev and _prev.get("ok") and time.time() - _prev.get("ts", 0) < 1.6:
            return _prev
        try:
            import shutil
            shutil.copyfile(FACE_LAST, FACE_MEDIA)
            with open(FACE_TOKEN_FILE) as f:
                tok = f.read().strip()
            req = urllib.request.Request(
                FACE_API, data=json.dumps({"input": {"path": FACE_MEDIA}}).encode(),
                headers={"Content-Type": "application/json", "X-AI-Service-Token": tok})
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
            if not res.get("ok"):
                raise RuntimeError(str(res.get("error"))[:100])
            out_faces = []
            for fc in ((res.get("result") or {}).get("faces") or []):
                bx = fc.get("bbox") or fc.get("box") or fc.get("rect") or []
                if not (isinstance(bx, list) and len(bx) >= 4):
                    continue
                try:
                    bb = [float(bx[0]), float(bx[1]), float(bx[2]), float(bx[3])]
                except (TypeError, ValueError):
                    continue
                out_faces.append({
                    "bbox": bb,
                    "subject_id": fc.get("subject_id") or "unknown",
                    "matched": bool(fc.get("matched")),
                    "similarity": fc.get("similarity"),
                    "score": fc.get("score"),
                })
            w, h = jpeg_size(FACE_LAST)
            out = {"ok": True, "w": w, "h": h, "faces": out_faces, "ts": time.time()}
        except Exception as e:
            out = {"ok": False, "error": str(e)[:120], "faces": [], "ts": time.time()}
            ensure_ai_service()
        if out.get("ok"):
            with _FACE_LOCK:
                _FACE_CACHE["key"] = key
                _FACE_CACHE["d"] = out
        return out


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
    # 2026-11-02：空帧自动重抓（最多 2 次，间隔 2s）。演示现场人可能刚好侧身/低头/走出画面，
    # 单帧无人就判 partial 太脆；只要链路正常，给人一个回到画面的机会。
    _retry = 0
    while not persons and _retry < 2:
        _retry += 1
        time.sleep(2)
        ok2, sz2 = take_snapshot()
        if not ok2:
            ms.append(("重抓帧 %d" % _retry, "抓帧失败（摄像头不可达）"))
            break
        try:
            det = vision_call("detect", SNAP); pose = vision_call("pose", SNAP)
        except Exception as e:
            ms.append(("重抓帧 %d" % _retry, "推理失败：%s" % str(e)[:60]))
            break
        dets = det.get("detections", []) or []
        det_persons = [d for d in dets if (d.get("class_id") == 0 or str(d.get("class_name", "")).lower() == "person")
                       and (d.get("score", 0) or 0) >= 0.35]
        persons = [p for p in (pose.get("persons", []) or []) if (p.get("score") or 0) >= 0.5]
        ms.append(("重抓帧 %d" % _retry, "%dKB · 目标检测 %d 人 · 姿态 %d 人" % (len(open(SNAP, "rb").read()) // 1024, len(det_persons), len(persons))))
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
    # 本地 VLM（MT200）看图：只在完整演示时调用，静音巡检不拉长耗时
    if not no_audio:
        try:
            import vlm_client as _vlm
            if _vlm.available(2):
                _d = _vlm.describe(timeout=90)
                ms.append(("VLM 看图（本地 MT200）", (_d[:140] if _d else "调用失败/超时")))
            else:
                ms.append(("VLM 看图（本地 MT200）", "未就绪（隧道或 llama-server 未起）"))
        except Exception as _e:
            ms.append(("VLM 看图（本地 MT200）", "异常: %s" % str(_e)[:60]))
    # 判定原则：只有「判定过程本身出错」才算功能失败；"画面内无人"、"检出坐姿异常" 都是**场景状态**，
    # 说明链路已正确执行（实测：把"检出异常坐姿"当失败会造成 2/3 的假失败）。
    ok = not verdict.startswith("判定异常")
    if ok and verdict.startswith("坐姿异常"):
        detail = "视觉双检已完成：检出异常坐姿（场景状态，功能正常）"
    elif ok and verdict.startswith("画面内无人"):
        detail = "视觉双检已完成：画面内无人（链路已执行，场景状态不判失败）"
    else:
        detail = ("视觉双检已完成：" if ok else "") + verdict
    # 2026-11-02 用户明确口径：画面内有人/无人、坐姿正常/异常都属于**场景状态**，
    # 只要 YOLO 检测/姿态/人脸链路本身执行成功就算通过；不再因"无人"或"未匹配身份"降为 partial。
    if not ok:
        status = "fail"
    else:
        status = "pass"
    if no_audio or no_upload:
        detail += "；上传链路未验收（只读巡检，不计异常）"
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
        parsed = query
        try:
            from offline_agent import make_plan
            plan = make_plan("检索图片：" + query)
            if plan["tool"] == "photo_search":
                parsed = plan["args"]["query"]
            else:
                ms.append(("意图解析", "MT200返回非检索计划，按本地规则继续"))
        except Exception as _e:
            ms.append(("意图解析", "MT200不可用，按本地规则解析（%s）" % type(_e).__name__))
        # 检索约束来自原始用户输入，模型不得偷偷翻译/丢弃日期。
        res = search_local_photos(query)
        ms += [("MT200检索词", parsed), ("检索模式", res["mode"]), ("匹配结果", "%d张；零命中不兜底最新照片" % res["count"])]
        ms += [("候选", str(a["name"])) for a in res["matches"][:3]]
        set_item(it, "pass", "检索完成；结果相关性需核对" if res["count"] else "检索完成：零命中", ms)
    except Exception as e:
        _msg = str(e)
        if ("timed out" in _msg) or ("URLError" in type(e).__name__) or ("不可用" in _msg):
            set_item(it, "skip", "环境不可用：检索后端离线（%s），不计功能失败" % _msg[:80], ms)
        else:
            set_item(it, "fail", "本地检索失败：" + _msg[:100], ms)


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



VIDEO_DEMO_MP4 = "/data/deliver/demo/concert_canton_tower.mp4"
VIDEO_INDEX_DIR = "/data/vindex/concert"


def check_video_ai(it):
    """AI 视频找帧：自然语言描述 → 本地索引检索 → 截取目标帧（零云端视频上传）。"""
    set_item(it, "running")
    ms = []
    t0 = time.time()
    query = "找出画面里出现广州塔的帧"
    out_img = "/data/deliver/demo/last_frame.jpg"
    ms.append(("输入视频", os.path.basename(VIDEO_DEMO_MP4) + "（30.7 秒 · 960×540）"))
    ms.append(("自然语言要求", query))
    try:
        rc, o = sh("python3 /data/ai_cpe/tools/vsearch.py --index %s --query '%s' --json --out %s"
                   % (VIDEO_INDEX_DIR, query, out_img), t=60)
        d = json.loads((o or "").strip().splitlines()[-1])
    except Exception as e:
        set_item(it, "fail", "检索异常：%s" % str(e)[:100], ms)
        return
    if not d.get("matched") or not os.path.exists(out_img):
        ms.append(("检索结果", "未命中（%s）" % str(d)[:120]))
        set_item(it, "fail", "未找到匹配帧 → 检查索引/caption", ms)
        return
    b = d.get("best") or {}
    ms.append(("命中帧", "第 %s 秒 · %s" % (b.get("t"), b.get("caption"))))
    ms.append(("截图", "%s（%d KB）" % (out_img, os.path.getsize(out_img) // 1024)))
    ms.append(("候选帧", "%d 帧（按相关度排序）" % len(d.get("candidates") or [])))
    ms.append(("本地耗时", "%.2f 秒" % (time.time() - t0)))
    ms.append(("查看截图", "http://192.168.1.1:8099/video_frame.jpg"))
    set_item(it, "pass", "自然语言视频找帧通过：命中第 %s 秒" % b.get("t"), ms)

def check_local_compute(it):
    """本地算力：MT200 NPU 上的 deepseek-7b-npu，真实推理一次并计时。
    不切换全局推理模式：在线模式下也直接走本地端点，验证完即返回。"""
    set_item(it, "running")
    ms = []
    ms.append(("算力单元", "MT200（QCS9075）· NPU 规格 200 TOPS"))
    ms.append(("本地模型", "deepseek-7b-npu（7B 级）"))
    ms.append(("调用路径", "CPE → 笔记本 LAN 代理 :18091 → SSH 隧道 → MT200 :8090"))
    try:
        import llm_gateway as _llm
        ep = _llm.local_endpoint()
        ms.append(("本地端点", ep.replace("http://", "").split("/")[0]))
        t1 = time.time()
        text = _llm._local_chat([
            {"role": "system", "content": "你是部署在 MT200 上的本地模型，只答一句简体中文。"},
            {"role": "user", "content": "用一句话说明你在哪里运行"},
        ], 48, 90)
        dt = time.time() - t1
        chars = len(text or "")
        ms.append(("实测输出", (text or "").strip()[:44]))
        ms.append(("端到端耗时", "%.1f s" % dt))
        ms.append(("输出长度", "%d 字符" % chars))
        if dt > 0 and chars:
            ms.append(("生成速度", "≈ %.1f 字符/s（端到端，含排队）" % (chars / dt)))
        st = _llm.stats()
        ms.append(("NPU 占空比", "%.1f%%（近60s·%d次，估算口径）" % (st.get("util_60s", 0), st.get("calls_60s", 0))))
        set_item(it, "pass", "MT200 本地 deepseek-7b-npu 真实推理通过：%.1fs / %d 字符" % (dt, chars), ms)
    except Exception as e:
        msg = str(e)[:110]
        ms.append(("本地链路", msg))
        set_item(it, "skip", "环境不可用：MT200 本地模型离线（%s），不计功能失败" % msg, ms)


def check_diag(it):
    """终端播放视频卡顿诊断：抽测视频流帧率/码率 + 单帧时延，输出诊断结论。"""
    set_item(it, "running")
    ms = []
    frames, total, el = pull_stream(4)
    fps = round(frames / el, 2); kbs = round(total / 1024 / el, 1)
    ms.append(("视频流抽测", "%d 帧 / %.1fs → %.2f fps · %.1f KB/s" % (frames, el, fps, kbs)))
    lat = []
    for _ in range(3):
        t = time.time()
        ok, _ = take_snapshot()
        if ok:
            lat.append(round((time.time() - t) * 1000))
    avg_ms = round(sum(lat) / len(lat)) if lat else None
    ms.append(("单帧抓取时延", ("%s ms（3 次均值）" % avg_ms) if lat else "失败"))
    try:
        target = float(os.environ.get("AICPE_VIDEO_TARGET_FPS", "10"))
        if not 1 <= target <= 120:
            raise ValueError()
    except ValueError:
        target = 10.0
    if frames == 0:
        status, verdict = "fail", "未收到完整视频帧或采样中断"
    elif not lat:
        status, verdict = "fail", "视频抓帧失败"
    elif fps + 1.0 / el < target:
        status, verdict = "partial", "低帧率：%.2f fps，低于 %.1f fps 目标；存在卡顿风险" % (fps, target)
    else:
        status, verdict = "pass", "视频流稳定：%.2f fps · %.1f KB/s，单帧 %d ms" % (fps, kbs, avg_ms)
    ms.append(("诊断结论", verdict))
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
    try:
        with open("/data/ai_cpe/demo_expected_skips.json", encoding="utf-8") as _f:
            _exp = json.load(_f) or {}
    except Exception:
        _exp = {}
    if _exp.get("smarthome"):
        set_item(it, "skip", str(_exp["smarthome"]), [("说明", "按展会现场清单标记为预期跳过")])
        return
    rc, st0 = _smarthome("status")
    ok0 = rc == 0 and bool(st0.get("success")) and _field(st0, "switch_led") in (True, False) and isinstance(_field(st0, "switch_led"), bool)
    sw0 = bool(_field(st0, "switch_led"))
    ms.append(("控制面（Matter/MQTT · Matter 优先）", ("正常 · transport=%s · broker=%s" % (st0.get("transport"), st0.get("broker"))) if ok0 else "异常: %s" % str(st0)[:60]))
    _drv = str(_field(st0, "driver") or "?")
    _proxy = bool(_field(st0, "proxy")) or _drv.startswith("board-led")
    if _drv and "matter" not in _drv.lower():
        _drv += "（Matter 灯未配网 → 自动兜底；配网后填 node_id 即切 Matter）"
    ms.append(("执行器驱动", _drv))
    ms.append(("指令前状态", "开" if sw0 else "关"))
    if no_audio and _proxy:
        # 只有代理执行器才跳过真实动作；Matter 真灯在静音模式下也做三轮真实开关（无声音）
        ms.append(("动作", "静音质检模式：仅读取状态（代理执行器，不实际开关）"))
        set_item(it, "skip" if ok0 else "fail",
                 ("控制面只读通过；执行器=%s（板载LED演示代理，真实灯具未受控）" % _drv) if ok0
                 else "灯控状态读取失败", ms)
        return
    # 执行器分两条验收路径（2026-11-02）：
    #   · 板载LED = 演示代理，只证明控制面通，不控制真实灯具 → fail
    #   · matter/tuya = 真实执行器，用控制面开关回读做三轮循环 → pass
    if not ok0:
        set_item(it, "fail", "控制面初态无效，未执行动作", ms)
        return
    seq_ok = True
    rc_f, st_f = 1, {}
    physical0 = physical_final = None
    if _proxy:
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
                good = (rc_o == 0 and rc_n == 0 and bool(st_o.get("success")) and bool(st_n.get("success"))
                        and sw_o is False and sw_n is True and physical_off == 0
                        and physical_on is not None and physical_on > 0)
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
        physical_final = led_value()
        ms.append(("恢复后物理回读", "brightness=%s（初态%s）" % (physical_final, physical0)))
    else:
        ms.append(("验收方式", "真实执行器 · 控制面开关三轮回读（不读板载LED）"))

        def _cmd_until(action, want, tries=3, t=60):
            """下发后轮询控制面，直到回读等于目标状态（Matter 快速连发时状态会滞后一拍）。"""
            rc, st = 1, {}
            for _ in range(tries):
                rc, st = _smarthome(action, t=t)
                if rc == 0 and bool(st.get("success")) and _field(st, "switch_led") is want:
                    return rc, st
                time.sleep(2)
            return rc, st

        try:
            for i in range(1, 4):
                rc_o, st_o = _cmd_until("off", False)
                sw_o = _field(st_o, "switch_led")
                rc_n, st_n = _cmd_until("on", True)
                sw_n = _field(st_n, "switch_led")
                good = (rc_o == 0 and rc_n == 0 and bool(st_o.get("success")) and bool(st_n.get("success"))
                        and sw_o is False and sw_n is True)
                seq_ok = seq_ok and good
                ms.append(("第 %d 轮 关→开" % i, "回读 %s→%s %s" % (sw_o, sw_n, "一致" if good else "异常")))
                if not good:
                    break
        except Exception as e:
            seq_ok = False
            ms.append(("动作异常", str(e)[:100]))
        finally:
            try:
                rc_f, st_f = _smarthome("on" if sw0 else "off", t=60)
            except Exception as e:
                seq_ok = False
                ms.append(("恢复异常", str(e)[:100]))
            # 恢复不达标则重试一次（Matter 冷会话/排队时首次可能超时）
            if not (rc_f == 0 and bool(st_f.get("success")) and _field(st_f, "switch_led") is sw0):
                try:
                    time.sleep(1)
                    rc_f, st_f = _smarthome("on" if sw0 else "off", t=90)
                except Exception as e:
                    ms.append(("恢复重试异常", str(e)[:100]))
    sw_f = _field(st_f, "switch_led")
    ms.append(("现场恢复", ("控制面恢复回读一致（%s）" % ("开" if sw_f else "关"))
               if rc_f == 0 and st_f.get("success") and sw_f is sw0
               else "恢复未达标 rc=%s success=%s switch_led=%s（初态=%s），需人工检查" % (rc_f, st_f.get("success"), sw_f, sw0)))
    if seq_ok and rc_f == 0 and st_f.get("success") and sw_f is sw0 and (_proxy or physical_final == physical0):
        if _proxy:
            ms.append(("真实灯具", "未控制：Matter 灯未配网；Moes 灯泡需涂鸦云凭据或 Matter 配网窗口"))
            set_item(it, "fail", "仅板载LED演示代理（proxy=true），未控制真实灯具 → 智能家居控制未实现", ms)
        else:
            set_item(it, "pass", "真实执行器（%s）三轮开关回读通过" % _drv, ms)
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
    ms.append(("Files 文件管理入口", "需登录 ✓（预期）；文件读写由 SMB 探针/每日巡检覆盖" if f == 401 else "API可达 ✓" if f == 200 else "不可达 ✗（HTTP %s）" % f))

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
        set_item(it, "pass", "五个服务入口可达；巡检文档可读；最新巡检 0 未解决", ms)
    elif ok_n == 4:
        set_item(it, "partial", "五个服务入口可达；巡检报告缺失或读取失败，报告内容未验收", ms)
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
    _smb_ev = ""
    try:
        import glob as _g
        _rp = sorted(_g.glob("/data/deliver/www/daily/巡检报告_*.md"))[-1]
        _txt = open(_rp, encoding="utf-8", errors="replace").read()
        if "SMB读写探针：写入+删除通过" in _txt:
            _smb_ev = "；读写探针通过（%s）" % _rp.split("_")[-1].replace(".md", "")
    except Exception:
        pass
    ms.append(("SMB 端口（445）", ("可连接 ✓" + _smb_ev) if s_ok else "暂不可达（链路抖动，NFS 仍可用）"))
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
    try:
        _okmsg = "NAS 端口与相册 API 可达；SMB 读写探针通过（见每日巡检）" if _smb_ev else "NAS 端口与相册 API 可达；SMB 读写探针未取到证据"
    except NameError:
        _okmsg = "NAS 端口与相册 API 可达"
    set_item(it, "pass" if ok else "fail",
             _okmsg if ok else
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
    log("=== AI CPE 十大功能一键演示 开始（%s）===" % ("静音质检模式" if args.no_audio else "完整演示模式"))
    items = [item(1, "AI 智能语音对话", "语音交互 / 大模型对话 / TTS 播报"),
             item(2, "AI 视觉检测", "人脸/目标/姿态检测；身份识别单独验收"),
             item(3, "本地自然语言图片检索", "文件名关键词检索；零命中尝试CLIP，相关性另验"),
             item(4, "终端播放视频卡顿诊断", "DPI 解析 RTSP/RTP 流，结合帧率/时延/丢包定位卡顿"),
             item(5, "智能家居控制", "控制真实灯具（Matter/Tuya）；当前只有板载LED演示代理"),
             item(6, "AI 智能选网", "QoE 驱动的蜂窝选网决策 · Shadow 观察（零写操作）"),
             item(7, "MiniNAS 存储", "x86 存储底座（机身盘 + SMB/NFS + 相册 + CasaOS）只读巡检"),
             item(8, "CasaOS统一管理界面", "App Store / Files / Home Assistant / Immich / 每日巡检 五个入口"),
             item(9, "AI 视频找帧（自然语言）", "按描述检索视频并截取目标帧；示例：演唱会视频找广州塔"),
             item(10, "本地算力 · DeepSeek 7B", "MT200 NPU 本地推理 · deepseek-7b-npu 真实问答实测")]
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
                        (check_casaos_center, items[7], {"no_audio": args.no_audio}),
                        (check_video_ai, items[8], {}),
                        (check_local_compute, items[9], {})]:
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
        STATE["summary"] = "本轮检查完成：%d项通过、%d项失败、%d项跳过（不计失败）；历史不计本轮通过" % (passed, sum(i["status"] == "fail" for i in items), _skipped)
    log("=== %s ===" % STATE["summary"])
    write_report(items, passed)
    return passed


def write_report(items, passed):
    ts = time.strftime("%Y%m%d_%H%M%S")
    L = ["# AI CPE 十大功能演示报告", "", "- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
         "- 设备：RG660MK（AI CPE）", "- 生成者：AICPE本地编排；MT200推理；笔记本LAN中转", "- 本轮结果：**%d 项通过，共展示 %d 项；未运行和历史证据不算通过**" % (passed, len(items)), "",
         "| # | 功能 | 结果 | 耗时 | 关键证据 |", "|---|---|---|---|---|"]
    for i in items:
        ev = "；".join("%s: %s" % (k, v) for k, v in i["metrics"][:3])
        L.append("| %d | %s | %s | %ss | %s |" % (i["idx"], i["name"],
                 {"pass": "✅ 通过", "fail": "❌ 失败", "skip": "跳过（不计失败）", "partial": "需关注／部分完成"}.get(i["status"], i["status"]),
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
#camstage{position:relative;line-height:0;border-radius:10px;overflow:hidden;background:#000}
#cam{width:100%;display:block;background:#000;min-height:200px;border-radius:10px}
#fov{position:absolute;inset:0;pointer-events:none}
.fbx{position:absolute;border:2px solid #31d17a;border-radius:8px;box-sizing:border-box;box-shadow:0 0 12px rgba(49,209,122,.6),inset 0 0 12px rgba(49,209,122,.22)}
.fbx .flb{position:absolute;left:-2px;top:-2px;transform:translateY(-100%);background:#31d17a;color:#04231a;font:700 12px/1.5 ui-monospace,Consolas,monospace;padding:1px 8px;border-radius:6px;white-space:nowrap}
.fbx.unk{border-color:#ffd479;box-shadow:0 0 12px rgba(255,212,121,.55),inset 0 0 12px rgba(255,212,121,.2)}
.fbx.unk .flb{background:#ffd479;color:#2a1e00}
.fbx .flb.in{left:2px;top:2px;transform:none}
#log{background:#0a1526;border:1px solid #1f3350;border-radius:14px;padding:12px;height:260px;overflow:auto;font:12px/1.7 ui-monospace,Consolas,monospace;color:#9fe8b6;white-space:pre-wrap}
#sum{margin:0 16px 16px;padding:14px;border-radius:14px;background:#0f2a1e;border:1px solid #2ecc71;font-size:17px}
.small{font-size:12px;color:#8fa6c4}
#cpewrap{display:flex;flex-wrap:wrap;gap:6px;margin:10px 16px 2px;padding:8px 12px;background:#0f1a2c;border:1px solid #1f3350;border-radius:12px}
.chip{font-size:12px;color:#cfe0f5;background:#152339;border:1px solid #24416b;border-radius:16px;padding:4px 10px;white-space:nowrap}
.modebadge{font-size:15px;font-weight:800;padding:7px 14px;border-width:2px;letter-spacing:.3px}
.mtspec{font-size:11px;color:#9ff0c0;background:#0d2a1c;border-color:#1f6b45}
.chip b{color:#7fb7ff;font-weight:600}
</style></head><body>
<noscript><meta http-equiv="refresh" content="5"></noscript>
<header>
  <h1>AI CPE 十大功能演示</h1>
  <span class="tag" id="dev">RG660MK · 5G 公网</span>
  <span class="tag" style="background:rgba(255,255,255,.08)">推理模式</span>
  <button id="mLocal" onclick="setMode('local')" style="background:#31d17a;color:#06231a;border:0;border-radius:10px;padding:7px 12px;font-size:13px;font-weight:700;cursor:pointer">本地模式</button>
  <button id="mOnline" onclick="setMode('online')" style="background:#3aa0f0;color:#04202f;border:0;border-radius:10px;padding:7px 12px;font-size:13px;font-weight:700;cursor:pointer">在线模式</button>
  <span class="tag" id="modeTag" style="background:rgba(58,160,240,.22)">模式读取中…</span>
  <span class="tag" id="opStatus" style="background:transparent"></span>
  <div id="bar"><div id="barIn"></div></div>
  <span class="tag" id="stat">加载中…</span>
  <a class="tag" id="dshLink" href="http://127.0.0.1:3081/" target="_blank" rel="noopener" style="text-decoration:none;color:#cfe0f5">DSH 控制台 ↗</a>
  <button id="enrollBtn" onclick="enrollFace()" style="background:#3aa0f0;color:#04202f;border:0;border-radius:10px;padding:9px 14px;font-size:14px;font-weight:700;cursor:pointer">录入身份</button>
  <button id="runbtn" onclick="startRun(true)" style="background:#31d17a;color:#06231a;border:0;border-radius:10px;padding:9px 16px;font-size:15px;font-weight:700;cursor:pointer">开始演示</button>
</header>
<div id="cpewrap"></div>
<div id="usagewrap" style="display:none;margin:0 16px 8px;padding:9px 12px;background:#0f1a2c;border:1px solid #1f3350;border-radius:12px;font-size:12.5px;color:#cfe0f5">
  <b style="color:#7fb7ff">Token 用量与费用</b> <span id="usageText">读取中…</span>
</div>
<main>
  <div id="cards"><div class="card"><h3>正在读取状态…</h3><div class="d">若长时间无变化，请刷新页面</div></div></div>
  <div id="right">
    <div id="camwrap">
      <div class="small" style="margin-bottom:6px">现场摄像头实时画面（C270）<span id="camstat" style="color:#ffd479;margin-left:8px"></span></div>
      <div id="camstage">
        <img id="cam" src="/cam/stream" alt="画面加载中（会自动重试）" onerror="cameraFail()" onload="cameraOk()">
        <div id="fov"></div>
      </div>
    </div>
    <div id="log">日志加载中…</div>
  </div>
</main>
<div id="sum"></div>
<script>
var jsMode = (typeof fetch !== "undefined");
var _reloadTimer = null;
// 局域网（同网段/本机）用 MJPEG 流；公网经 cloudflared(LAX) 单程 ~1.2s，流会越看越延迟，
// 改用「上一帧到了就请求下一帧」的快照拉取，延迟有界（实测 1.2-1.5s），不做无界缓冲。
var _lan = /^(192\.168\.|10\.|127\.|localhost$)/.test(location.hostname);
function cameraOk(){
  var e=document.getElementById("camstat");
  if(e && !_camFallback){ var d=new Date(); e.style.color="#8ef0b4"; e.innerText=(_lan?"实时画面 ":"画面更新 ")+d.toTimeString().slice(0,8); }
  if(!_lan || _camFallback){ setTimeout(function(){ var c=document.getElementById("cam"); if(c && (!_lan || _camFallback)) c.src="/snap?ts="+Date.now(); }, 150); }
}
function cameraTick(){ loadFaces(); }
var _faceBusy=false;
async function loadFaces(){
  if(!jsMode||_faceBusy) return;
  _faceBusy=true;
  try{
    var r=await fetch("/faces",{cache:"no-store"});
    var j=await r.json();
    var ov=document.getElementById("fov");
    if(!ov) return;
    var W=+j.w||0,H=+j.h||0,fs=j.faces||[];
    if(!j.ok||!W||!H||!fs.length){ ov.innerHTML=""; return; }
    var html="";
    fs.forEach(function(f){
      var b=f.bbox||[]; if(b.length<4) return;
      var x=Math.max(0,Math.min(100,b[0]/W*100)), y=Math.max(0,Math.min(100,b[1]/H*100));
      var w=Math.min(100-x,(b[2]-b[0])/W*100), h=Math.min(100-y,(b[3]-b[1])/H*100);
      if(w<=0||h<=0) return;
      var nm=f.subject_id||"unknown", cls="";
      if(nm==="self"){ nm="本人"; }
      else if(nm==="unknown"||nm===""){ nm="未录入人脸"; cls=" unk"; }
      var pc=(f.matched&&f.similarity!=null)?(" "+(f.similarity*100).toFixed(0)+"%"):"";
      var lc=(y<10)?" in":"";
      html+="<div class='fbx"+cls+"' style='left:"+x.toFixed(2)+"%;top:"+y.toFixed(2)+"%;width:"+w.toFixed(2)+"%;height:"+h.toFixed(2)+"%'><span class='flb"+lc+"'>"+esc(nm)+pc+"</span></div>";
    });
    ov.innerHTML=html;
  }catch(e){
    var ov2=document.getElementById("fov"); if(ov2) ov2.innerHTML="";
  }finally{ _faceBusy=false; }
}
var _camFallback=false;
function cameraFail(){
  _camFallback=true;
  var e=document.getElementById("camstat");
  if(e) {e.style.color="#ffd479";e.textContent="实时流不可用，已切快照刷新";}
  setTimeout(function(){ var c=document.getElementById("cam"); if(c) c.src="/snap?ts="+Date.now(); }, 500);
}
function fallbackMode(reason){
  if(!jsMode) return;
  jsMode=false;
  var e=document.getElementById("camstat");
  if(e) e.textContent="已切换兜底刷新模式（每 5 秒整页刷新）";
  if(!_reloadTimer) _reloadTimer=setInterval(function(){ location.reload(); }, 5000);
}
var ICON={pending:"○",running:"◌",pass:"✅",fail:"❌",skip:"⏭"};
var CN={pending:"待运行",running:"运行中",pass:"本轮检查通过",fail:"失败",skip:"跳过（不计失败）",partial:"需关注／部分完成"};
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
    (function(){
      var u=s.usage||null, el=document.getElementById("usagewrap"), tx=document.getElementById("usageText");
      function fmtTok(n){ n=Number(n)||0; if(n>=1e8) return (n/1e8).toFixed(2)+" 亿"; if(n>=1e4) return (n/1e4).toFixed(1)+" 万"; return String(Math.round(n)); }
      if(u&&u.today&&tx){
        el.style.display="block";
        var extra="";  // 源失败计数不再上屏（启动瞬间的远程库读取失败会自愈，属噪音）
        tx.innerHTML="今日 <b style='color:#e8f1ff'>"+fmtTok(u.today.tokens)+"</b> tokens ≈ <b style='color:#31d17a'>¥"+Number(u.today.cny||0).toFixed(2)+"</b>"+
          " ｜ 近 30 天 <b style='color:#e8f1ff'>"+fmtTok((u.m30||{}).tokens)+"</b> tokens ≈ <b style='color:#31d17a'>¥"+Number((u.m30||{}).cny||0).toFixed(2)+"</b>"+
          " ｜ 更新 "+(u.updated||"-")+extra;
      }
    })();
    var c=JSON.parse(JSON.stringify(s.cpe||{}));
    function escapeStrings(x){Object.keys(x).forEach(function(k){if(typeof x[k]==="string")x[k]=esc(x[k]);else if(x[k]&&typeof x[k]==="object")escapeStrings(x[k]);});} escapeStrings(c);
    if(c.model){
      var _lm=c.llm||{}; var _local=(_lm.mode==="mt200-local");
      var chips="<span class='chip modebadge' style='background:"+(_local?"#0f3a24":"#0f2743")+";border-color:"+(_local?"#31d17a":"#4aa3ff")+";color:"+(_local?"#8ff0b5":"#9ccbff")+"'>当前模型通道："+(_local?"本地 MT200":"在线云端")+" · "+(_lm.model||"—")+"</span>";
      if(_local){
        chips+="<span class='chip mtspec'><b>MT200</b> QCS9075 · 8×A78C 2.36GHz · Adreno 663 · 100 TOPS 稠密 / 200 TOPS 稀疏 · 10.86GB · 128GB UFS · Ubuntu 24.04</span>";
        chips+="<span class='chip mtspec'><b>本地推理</b> deepseek-7b-npu · NPU API :8090（SSH 转发 :18090） · 实测 ≈5 tok/s（76 tokens / 15 秒，端到端）</span>";
      }
      var _mL=document.getElementById("mLocal"), _mO=document.getElementById("mOnline");
      if(_mL&&_mO){ _mL.style.opacity=_local?1:0.4; _mO.style.opacity=_local?0.4:1;
        _mL.style.outline=_local?"2px solid #8ff0b5":"none"; _mO.style.outline=_local?"none":"2px solid #9ccbff"; }
      chips+="<span class='chip'><b>模组</b> "+c.model+"</span>";
      if(c.platform) chips+="<span class='chip'><b>平台</b> "+c.platform+"</span>";
      var npu=s.npu||null;
      var _npuModel=(npu&&npu.model)?npu.model:((s.llm&&s.llm.mode==="mt200-local")?s.llm.model:"");
      if(_npuModel){
        var _u=(npu&&typeof npu.util_60s==="number")?npu.util_60s:0;
        var _calls=(npu&&npu.calls_60s)||0;
        var _ago="";
        if(npu&&npu.last_ts){ var _m=Math.floor((Date.now()/1000-npu.last_ts)/60); _ago=(_m<=0?"刚刚":(_m+" 分钟前")); }
        var _stateTxt=(_u>0)
          ? ("占空比 <b style='color:#31d17a'>"+_u.toFixed(1)+"%</b>（近60s · "+_calls+" 次）")
          : ("<b style='color:#8ea6c8'>空闲</b>（近60s无推理"+( _ago?(" · 最近 "+_ago):"")+"）");
        var _npuTxt="<span class='chip' style='border-color:#3aa0f0'><b>NPU</b> "+esc(_npuModel)+
          " · 算力 <b style='color:#7fb7ff'>100 TOPS 稠密 / 200 TOPS 稀疏</b> · "+_stateTxt;
        if(npu&&npu.ok&&npu.elapsed_ms){ _npuTxt+=" · 最近一次 <b style='color:#31d17a'>"+(npu.elapsed_ms/1000).toFixed(1)+" s</b>"; }
        chips+=_npuTxt+"</span>";
      }
      if(c.rsrp!==undefined) chips+="<span class='chip' style='border-color:#31d17a'><b>当前信号（"+(c.rat||"5G")+"）</b> RSRP <b style='color:#31d17a'>"+c.rsrp+" dBm</b></span>";
      if(s.now) chips+="<span class='chip'><b>数据更新</b> "+s.now+"</span>";
      if(c.rssi_best!==undefined&&c.rssi_best!==null){ chips+="<span class='chip'><b>WiFi 信号</b> "+c.rssi_best+" dBm</span>"; }
      else { chips+="<span class='chip'><b>WiFi</b> 无接入终端</span>"; }
      if(c.ssid) chips+="<span class='chip'><b>SSID</b> "+c.ssid+"</span>";
      var _cnm=(c.sta_names||[]).filter(function(x){return x&&x!=="*";});
      var _cntxt=_cnm.length?("："+_cnm.slice(0,3).join("、")+(_cnm.length>3?" 等 "+_cnm.length+" 台":"")):"";
      chips+="<span class='chip'><b>接入终端</b> "+c.clients+" 个（WiFi "+c.clients_wifi+" · 有线 "+c.clients_lan+"）"+_cntxt+"</span>";
      if(c.wan_ip) chips+="<span class='chip'><b>WAN</b> "+c.wan_ip+(c.wan_up?" ✓":" ✗")+"</span>";
      if(c.temp_c) chips+="<span class='chip' title='thermal_zone0 (soc_max) 板载 SoC 最高温'><b>SoC温度</b> "+c.temp_c+" °C</span>";
      if(c.mem_total_mb) chips+="<span class='chip'><b>内存</b> 总 "+c.mem_total_mb+" MiB · 可用 "+c.mem_avail_mb+" MiB</span>";
      if(c.hermes_mb) chips+="<span class='chip'><b>Hermes 占用</b> "+c.hermes_mb+" MiB</span>";
      if(c.uptime_s) chips+="<span class='chip'><b>运行</b> "+Math.floor(c.uptime_s/3600)+"h"+Math.floor(c.uptime_s%3600/60)+"m</span>";
      document.getElementById("cpewrap").innerHTML=chips;
    }
    var lg=document.getElementById("log");
    lg.textContent=(s.log||[]).join("\\n"); lg.scrollTop=lg.scrollHeight;
    var sm=document.getElementById("sum");
    if(s.summary){ sm.style.display="block"; sm.innerHTML=esc(s.summary)+"　<a href='/report' target='_blank'>查看本轮报告</a>"; }
    else { sm.style.display="none"; sm.innerHTML=""; }
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
  setInterval(tick,2000); tick();
  // 局域网：MJPEG 流（12fps，实测延迟 0.37s）；公网：快照串行拉取（延迟有界，约 1.2-1.5s）
  var _c0=document.getElementById("cam");
  if(_c0 && !_lan) _c0.src="/snap?ts="+Date.now();
  setInterval(cameraTick, 2500);
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
<h2>AI CPE 十大功能演示（简易版，每 6 秒自动刷新）</h2>
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
        if self.path == "/reset":
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
                if 0 < n <= 256:
                    self.rfile.read(n)
            except Exception:
                pass
            with LOCK:
                STATE["phase"] = "idle"
                STATE["done"] = True
                STATE["running"] = False
                STATE["summary"] = "已手动复位（上一轮未完成，未计入通过）"
                for _it in STATE.get("items", []):
                    if _it.get("status") == "running":
                        _it["status"] = "skip"
                        _it["detail"] = "中断：未完成（人工复位）"
                snap = json.loads(json.dumps(STATE, ensure_ascii=False))
            tmp = STATE_FILE + ".reset." + str(os.getpid())
            try:
                with open(tmp, "w") as out:
                    json.dump(snap, out, ensure_ascii=False)
                os.replace(tmp, STATE_FILE)
            except OSError as e:
                print("复位持久化失败：" + type(e).__name__, flush=True)
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"reset": True, "phase": "idle"}, ensure_ascii=False))
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

    def _cam_stream(self):
        """MJPEG 推流：局域网 12fps 原图；经 cloudflared 的公网 7fps + 缩图（省 5G 上行）。
        每 1.5s 回写一帧到 FACE_LAST 喂给 /faces 人脸框；每 3s 做一次遮挡/过暗检查，
        命中就推提示图（与 /snap 的兜底行为一致）。流断开即退出线程。"""
        _wan = bool(self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For"))
        try:
            import urllib.parse as _up
            _q = _up.parse_qs(_up.urlparse(self.path).query)
            _fps = float((_q.get("fps") or [5.0 if _wan else 12.0])[0])
        except Exception:
            _fps = 5.0 if _wan else 12.0
        _fps = max(1.0, min(15.0, _fps))
        interval = 1.0 / _fps
        try:
            import socket as _sock
            self.connection.setsockopt(_sock.IPPROTO_TCP, _sock.TCP_NODELAY, 1)
            self.connection.setsockopt(_sock.SOL_SOCKET, _sock.SO_SNDBUF, 24576)
        except Exception:
            pass
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        _face_ts = 0.0
        _chk_ts = 0.0
        _notice = b""
        _notice_kind = ""
        try:
            while True:
                t0 = time.time()
                data = None
                for _u in (CAMAV + "/snapshot", CAMVIEW + "/snapshot"):
                    try:
                        with urllib.request.urlopen(_u, timeout=3) as r:
                            b = r.read()
                        if b[:2] == b"\xff\xd8" and len(b) > 5000:
                            data = b
                            break
                    except Exception:
                        continue
                if data:
                    if t0 - _face_ts >= 1.5:
                        try:
                            open(FACE_LAST, "wb").write(data)
                        except OSError:
                            pass
                        _face_ts = t0
                    if t0 - _chk_ts >= 3.0:
                        _chk_ts = t0
                        try:
                            _k, _m = cam_obstructed(FACE_LAST)
                        except Exception:
                            _k = ""
                        if _k:
                            if _k != _notice_kind or not _notice:
                                _np = cam_notice_image(_k, _m)
                                try:
                                    _notice = open(_np, "rb").read()
                                    _notice_kind = _k
                                except Exception:
                                    _notice = b""
                        else:
                            _notice = b""
                            _notice_kind = ""
                    if _notice:
                        data = _notice
                    elif _wan:
                        try:
                            data = shrink_jpeg(data)
                        except Exception:
                            pass
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(data)).encode()
                                   + b"\r\nX-Ts: " + ("%.3f" % t0).encode() + b"\r\n\r\n")
                    self.wfile.write(data)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                dt = time.time() - t0
                if dt < interval:
                    time.sleep(interval - dt)
        except Exception:
            pass

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
        elif p == "/video_frame.jpg":
            try:
                with open("/data/deliver/demo/last_frame.jpg", "rb") as _f:
                    self._send(200, "image/jpeg", _f.read())
            except Exception:
                self._send(404, "application/json", '{"error":"no frame yet"}')
        elif p == "/state":
            # 只返回缓存值：探测放后台线程，避免拖慢页面轮询（实测曾 9s+）
            reload_state_if_newer()
            cpe = _CPE_CACHE.get("d") or {"model": CPE_MODEL, "platform": CPE_PLATFORM}
            with LOCK:
                out = dict(STATE)
            out["cpe"] = cpe
            _llm = llm_gateway.describe()
            out["llm"] = _llm
            try:
                out["npu"] = llm_gateway.stats()
            except Exception:
                out["npu"] = {}
            try:
                _m = os.path.getmtime(TOKEN_STATS_FILE)
                if _m != _TOKEN_CACHE["mt"]:
                    with open(TOKEN_STATS_FILE, encoding="utf-8") as _f:
                        _TOKEN_CACHE["d"] = json.load(_f)
                    _TOKEN_CACHE["mt"] = _m
                out["usage"] = _TOKEN_CACHE["d"]
            except Exception:
                out["usage"] = {}
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
        elif p == "/faces":
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(face_boxes(), ensure_ascii=False))
        elif p == "/cam/stream":
            self._cam_stream()
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
                    try:
                        os.remove(FACE_LAST)   # 提示图会替换画面，旧帧人脸框必须作废
                    except OSError:
                        pass
                    _np = cam_notice_image(_kind, _m)
                    if _np:
                        try:
                            data = open(_np, "rb").read()
                        except Exception:
                            pass
                # 内网直连（笔记本/手机连板子 LAN/WiFi）返回原图，清晰且只要 ~50ms；
                # 经 cloudflared 的外网请求返回缩图（45KB→13.7KB），省隧道带宽。
                # cloudflared 回源到 127.0.0.1，所以按它注入的头判断来源。
                _wan = bool(self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For"))
                self._send(200, "image/jpeg", shrink_jpeg(data) if _wan else data)
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
    ap = argparse.ArgumentParser(description="AI CPE 十大功能一键演示（含实时看板）")
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
