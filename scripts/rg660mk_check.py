#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RG660MK AI CPE 全功能巡检脚本  v2
==================================================
SSH 登录设备(默认 192.168.1.1:22)采集只读状态并巡检设备上"实际部署"的功能，
覆盖：网络基础 + 智能音箱(语音助手/KWS/VAD/whisper/TTS) + 视觉AI(姿态/人脸/摄像头)
      + 智能家居(MQTT桥/HA/Matter/Tuya灯泡) + Hermes 代理 + 定时任务健康。

标出问题(✓/△/✗)，并对"低风险常见项"自动修复(默认开)：
  · procd 服务掉线 → /etc/init.d/<svc> restart
  · 姿态检测 cron 不在 → 补回 crontab 条目
其余高风险项(驻网失败、摄像头缺失、HA 主机不可达等)只标红报警，不自动改。

用法：
  python3 rg660mk_check.py                 # 交互巡检 + 自动修复(默认开)
  python3 rg660mk_check.py --no-pause      # 无人值守(定时任务用)，不暂停
  python3 rg660mk_check.py --no-fix        # 只巡检不自动修复
  python3 rg660mk_check.py --feishu        # 巡检后尝试发飞书摘要(需 lark-cli 已连接)
  python3 rg660mk_check.py --host 192.168.1.1

依赖：python3 + paramiko。缺失时：sudo apt install python3-paramiko
"""

import json
import os
import subprocess
import sys
import time
import warnings

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
warnings.filterwarnings("ignore")

try:
    import paramiko
except ImportError:
    print("缺少 paramiko，请先安装： sudo apt install python3-paramiko")
    sys.exit(1)

# ---------------- 设备连接参数 ----------------
HOST = "192.168.1.1"
PORT = 22
USER = "root"
PASS = "oelinux123"

# HA / MQTT 宿主机（不在 CPE 上，另一台）
HA_HOST = "192.168.1.244"
HA_PORT = 8123
MQTT_PORT = 1883

# CPE 上的摄像头实时预览 Web 页（http://<HOST>:8090/）
CAM_PREVIEW_PORT = 8090

# CPE 上的 procd 服务（实测已 enabled 常驻）
PROCD_SERVICES = [
    ("voice_assistant", "语音助手"),
    ("mqtt_bridge",     "MQTT 桥接"),
    ("matter",          "Matter"),
    ("hermes",          "Hermes 代理"),
    ("ai_service",      "AI 服务"),
    ("speech_daemon",   "语音守护"),
]

# 姿态检测 cron（实测 /etc/crontabs/root 里每小时跑一次）
POSTURE_CRON = "0 * * * * /usr/bin/python3 /data/ai_cpe/hermes/home/posture_check.py >> /data/ai_cpe/hermes/home/photos/posture.log 2>&1"
POSTURE_LOG = "/data/ai_cpe/hermes/home/photos/posture.log"
# 每小时抓帧归档目录：本机视觉链路(姿态/人脸)不走 V4L2 /dev/video，而是用户态 MJPEG
# 抓帧工具直读 USB；姿态任务是否健康看这里的归档照片新鲜度，不看告警日志。
POSTURE_ARCHIVE = "/data/ai_cpe/hermes/home/photos/archive"
# C270 用户态抓帧工具（姿态/人脸的真实取帧路径，成功输出 PHOTO_OK）
C270_SNAPSHOT = "/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot"

# ---------------- 输出颜色 ----------------
_TTY = sys.stdout.isatty()
def _c(s, code): return code + s + "\033[0m" if _TTY else s
def green(s):  return _c(s, "\033[32m")
def red(s):    return _c(s, "\033[31m")
def yellow(s): return _c(s, "\033[33m")
def cyan(s):   return _c(s, "\033[36m")
def bold(s):   return _c(s, "\033[1m")

RESULTS = []   # (status, label, detail)
FIXES = []     # 已执行/尝试的自动修复记录

def add(status, label, detail=""):
    RESULTS.append((status, label, detail))

def sh(ssh, cmd, timeout=30):
    """在设备上执行命令，返回 (output, exit_code)。"""
    try:
        si, so, se = ssh.exec_command(cmd, timeout=timeout)
        out = so.read().decode("utf-8", "replace")
        err = se.read().decode("utf-8", "replace")
        try:
            rc = so.channel.recv_exit_status()
        except Exception:
            rc = -1
        return (out + err).strip(), rc
    except Exception:
        return "", -1

def local_ping(host):
    try:
        r = subprocess.run(["ping", "-c", "2", "-W", "2", host],
                           capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False

def local_port_open(host, port, timeout=3):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except Exception:
        return False
    finally:
        s.close()

def local_http_get(host, port, path="/", timeout=6):
    """本机发起一次 HTTP GET，返回 (状态码 or None, 错误说明)。
    只用标准库,不依赖 curl/requests。状态码非 None 即视为服务可达。"""
    import http.client
    conn = None
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path)
        resp = conn.getresponse()
        resp.read(2048)
        return resp.status, ""
    except Exception as e:
        return None, str(e)
    finally:
        if conn is not None:
            try: conn.close()
            except Exception: pass


def section(title): print("\n" + bold("▍ " + title))
def kv(k, v):       print("   %-14s %s" % (k, v))

def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(HOST, port=PORT, username=USER, password=PASS, timeout=10,
                  allow_agent=False, look_for_keys=False, banner_timeout=15)
        return c
    except Exception as e:
        print(red("  ✗ SSH 登录失败: %s" % e))
        return None


def main():
    global HOST
    no_pause = "--no-pause" in sys.argv
    do_fix   = "--no-fix" not in sys.argv          # 默认开启自动修复
    do_feishu = "--feishu" in sys.argv
    if "--host" in sys.argv:
        i = sys.argv.index("--host")
        if i + 1 < len(sys.argv):
            HOST = sys.argv[i + 1]

    print(bold("=" * 60))
    print(bold("   RG660MK AI CPE 全功能巡检  v2"))
    print(bold("   %s  @ %s:%s   自动修复:%s" % (HOST, USER, PORT, "开" if do_fix else "关")))
    print(bold("=" * 60))

    # ============ 1. 连接与设备身份 ============
    section("1. 连接与设备身份")
    if local_ping(HOST):
        add("ok", "链路连通", "主机可 ping 通 %s" % HOST)
        print(green("  ✓ 主机可 ping 通 %s" % HOST))
    else:
        add("fail", "链路连通", "主机 ping 不通 %s" % HOST)
        print(red("  ✗ 主机 ping 不通 %s，请检查网线/USB 网卡" % HOST))
        _finish(no_pause, do_feishu)
        return

    ssh = connect()
    if ssh is None:
        add("fail", "SSH 登录", "无法登录 %s@%s" % (USER, HOST))
        _finish(no_pause, do_feishu)
        return
    add("ok", "SSH 登录", "root 登录成功")
    print(green("  ✓ SSH 登录成功"))

    out, _ = sh(ssh, "cat /etc/openwrt_release 2>/dev/null; cat /tmp/sysinfo/model 2>/dev/null")
    desc = ""
    for line in out.splitlines():
        if line.startswith("DISTRIB_DESCRIPTION"):
            desc = line.split("=", 1)[1].strip("'\"")
    kv("固件", desc or "-")
    kv("内核", sh(ssh, "uname -r")[0])
    up, _ = sh(ssh, "cat /proc/uptime")
    try:
        kv("运行时长", "%.1f 小时" % (float(up.split()[0]) / 3600))
    except Exception:
        kv("运行时长", up)

    # ============ 2. 系统资源 ============
    section("2. 系统资源")
    load, _ = sh(ssh, "cat /proc/loadavg")
    topout, _ = sh(ssh, "top -bn1 2>/dev/null | head -3")
    idle_pct = None
    for line in topout.splitlines():
        toks = line.split()
        if "idle" in toks:
            i = toks.index("idle")
            if i > 0:
                try: idle_pct = float(toks[i - 1].rstrip("%"))
                except Exception: pass
            break
    if load:
        p = load.split()
        kv("负载", "1m %s  5m %s  15m %s" % (p[0], p[1], p[2]))
        try:
            l1 = float(p[0])
            if idle_pct is not None:
                # MediaTek 模组有常驻 D 状态内核看门狗线程，负载读数虚高；用 CPU 空闲率判断。
                if idle_pct < 25:
                    add("warn", "负载", "CPU 实际繁忙(空闲 %.0f%%)，1m 负载 %.1f" % (idle_pct, l1))
                else:
                    add("ok", "负载", "CPU 空闲 %.0f%%，负载为内核看门狗线程所致，属正常" % idle_pct)
            else:
                add("ok", "负载", "1m 负载 %.1f" % l1)
        except Exception:
            pass
    mem, _ = sh(ssh, "grep -E 'MemTotal|MemAvailable' /proc/meminfo")
    total = avail = 0
    for line in mem.splitlines():
        if "MemTotal" in line: total = int(line.split()[1])
        elif "MemAvailable" in line: avail = int(line.split()[1])
    if total:
        used = total - avail
        pct = used * 100 // total
        kv("内存", "已用 %d%% (%d/%d MB)" % (pct, used // 1024, total // 1024))
        add("warn" if pct > 90 else "ok", "内存", "已用 %d%%" % pct)

    # /data 磁盘（AI 模型/照片都落在 /data，满了会拖垮语音/视觉）
    dfout, _ = sh(ssh, "df /data 2>/dev/null | tail -1")
    dp = dfout.split()
    if len(dp) >= 5:
        usep = dp[4].rstrip("%")
        kv("/data 磁盘", "已用 %s%% (%s 可用)" % (usep, dp[3]))
        try:
            add("warn" if int(usep) > 85 else "ok", "/data 磁盘", "已用 %s%%" % usep)
        except Exception:
            pass

    tmp, _ = sh(ssh, "for z in /sys/class/thermal/thermal_zone*; do "
                     "t=$(cat $z/type 2>/dev/null); v=$(cat $z/temp 2>/dev/null); "
                     "[ -n \"$v\" ] && echo \"$t $v\"; done")
    maxc = 0.0
    for line in tmp.splitlines():
        p = line.split()
        if len(p) >= 2 and p[-1].lstrip("-").isdigit():
            maxc = max(maxc, int(p[-1]) / 1000.0)
    if maxc:
        kv("温度", "最高 %.1f °C" % maxc)
        add("warn" if maxc > 80 else "ok", "温度", "%.1f °C" % maxc)

    # ============ 3. SIM / 蜂窝 WAN ============
    section("3. SIM 卡与蜂窝 WAN")
    sim, _ = sh(ssh, "uci get network.data.sim 2>/dev/null")
    kv("SIM 槽", "SIM%s" % sim if sim else "-")
    wout, _ = sh(ssh, "ifstatus wan 2>/dev/null")
    try:
        w = json.loads(wout)
        up_state = w.get("up", False)
        v4 = " ".join(a.get("address", "") for a in w.get("ipv4-address", []))
        v6 = " ".join(a.get("address", "") for a in w.get("ipv6-address", []))
        data = w.get("data", {})
        kv("状态", "UP" if up_state else "DOWN")
        kv("协议", w.get("proto", "-"))
        kv("IPv4", v4 or "-")
        kv("APN", data.get("nw_apn", data.get("apn", "-")))
        if up_state:
            add("ok", "驻网/拨号", "WAN up: %s" % (v4 or v6 or "有地址"))
            print(green("  ✓ 已注册并建立数据会话"))
        else:
            add("fail", "驻网/拨号", "WAN 未 up")
            print(red("  ✗ WAN 未建立数据会话"))
    except Exception:
        add("fail", "驻网/拨号", "ifstatus wan 解析失败")
        kv("ifstatus wan", wout[:120] or "(空)")

    # ============ 4. 出口数据链路 ============
    section("4. 出口数据链路")
    pout, prc = sh(ssh, "ping -c 3 -W 2 8.8.8.8 2>&1")
    if prc == 0:
        add("ok", "出口连通", "设备 ping 8.8.8.8 通")
        print(green("  ✓ 设备可 ping 通 8.8.8.8"))
        for line in pout.splitlines():
            if "min/avg/max" in line:
                kv("延迟", line.split("=", 1)[1].strip())
    else:
        add("fail", "出口连通", "设备 ping 8.8.8.8 不通")
        print(red("  ✗ 设备 ping 8.8.8.8 不通"))

    # ============ 5. AI 服务(procd) ============
    section("5. AI 服务健康(智能音箱 / 家居 / Matter / Hermes)")
    for svc, cn in PROCD_SERVICES:
        r, _ = sh(ssh, "/etc/init.d/%s running 2>/dev/null && echo RUN || echo DOWN" % svc)
        if "RUN" in r:
            add("ok", cn, "%s 运行中" % svc)
            print(green("  ✓ %-10s (%s) 运行中" % (cn, svc)))
        else:
            # 低风险：procd 服务掉线，自动 restart
            if do_fix:
                sh(ssh, "/etc/init.d/%s enabled 2>/dev/null || /etc/init.d/%s enable" % (svc, svc))
                sh(ssh, "/etc/init.d/%s restart 2>&1" % svc, timeout=40)
                time.sleep(3)
                r2, _ = sh(ssh, "/etc/init.d/%s running 2>/dev/null && echo RUN || echo DOWN" % svc)
                if "RUN" in r2:
                    add("warn", cn, "%s 曾掉线，已自动重启恢复" % svc)
                    FIXES.append("重启 %s(%s) → 已恢复" % (svc, cn))
                    print(yellow("  △ %-10s (%s) 掉线 → 已自动重启恢复" % (cn, svc)))
                else:
                    add("fail", cn, "%s 掉线，自动重启后仍未起来" % svc)
                    FIXES.append("重启 %s(%s) → 失败，需人工" % (svc, cn))
                    print(red("  ✗ %-10s (%s) 掉线，自动重启失败，请人工排查" % (cn, svc)))
            else:
                add("fail", cn, "%s 掉线(--no-fix 未修复)" % svc)
                print(red("  ✗ %-10s (%s) 掉线" % (cn, svc)))

    # 语音助手关键子进程(录音链)确认
    proc, _ = sh(ssh, "ps w | grep -E 'voice_assistant.py|arecord' | grep -v grep | wc -l")
    kv("语音链进程", ("%s 个(voice_assistant/arecord)" % proc.strip()) if proc.strip().isdigit() else "-")

    # ============ 6. 视觉 AI / 姿态检测定时任务 ============
    section("6. 视觉 AI 与姿态检测")
    # 摄像头判据（按本机真实架构，而非 V4L2 惯例）：
    #   本机视觉链路(姿态/人脸)不依赖内核 /dev/video 节点，而是用户态 MJPEG 抓帧工具
    #   (rg660mk_c270_snapshot) 直读 USB。因此判摄像头死活要看“能不能抓到帧”，
    #   而不是“有没有 /dev/video”——后者对这套架构恒为假，会误报 FAIL。
    #   a) /dev/video* 存在            → 正常(标准 UVC 路径)
    #   b) 无节点，但抓帧工具能出 PHOTO_OK → 正常(用户态直读，本机真实工作路径)
    #   c) 无节点、抓帧也失败，但 USB 上有 C270 → 驱动/取帧异常，给出 UVC 诊断
    #   d) USB 上根本没有摄像头        → 真没插，提示插回 C270
    vid, _ = sh(ssh, "ls /dev/video* 2>/dev/null | head -3")
    if vid:
        kv("摄像头", vid.replace("\n", " "))
        add("ok", "摄像头", "%s 可用(V4L2 节点)" % vid.replace("\n", " "))
        print(green("  ✓ 摄像头设备节点就绪: %s" % vid.replace("\n", " ")))
    else:
        # 无 /dev/video 属本机常态。C270 为 USB 独占设备:常驻的 camview(8090)一直占着它。
        # 因此判摄像头死活优先走 camview 的 /snapshot HTTP 接口——这既能真实反映视觉链路
        # 是否出帧,又不会去直开 USB 跟 camview 抢占(直开会触发重枚举、把 camview 弄卡死,
        # 还会让本检查项瞬时误报"抓帧失败")。仅当 camview 不可用时,才回退到直开 USB 抓帧。
        snap_bytes = 0
        s2, e2 = local_http_get(HOST, CAM_PREVIEW_PORT, "/snapshot")
        if s2 == 200:
            # 再取一次拿字节数(local_http_get 只读前 2KB,这里单独量一下有效性)
            try:
                import http.client
                conn = http.client.HTTPConnection(HOST, CAM_PREVIEW_PORT, timeout=8)
                conn.request("GET", "/snapshot")
                r = conn.getresponse()
                snap_bytes = len(r.read())
                conn.close()
            except Exception:
                snap_bytes = 0
        if snap_bytes > 1000:
            kv("摄像头", "camview /snapshot 出帧 %d 字节(用户态 MJPEG,不抢占)" % snap_bytes)
            add("ok", "摄像头", "C270 经 camview 取帧正常(/snapshot 返回 %d 字节 JPEG);本机视觉链路走用户态 MJPEG,属正常" % snap_bytes)
            print(green("  ✓ 摄像头取帧正常(camview /snapshot %d 字节,不与预览抢 USB)" % snap_bytes))
        else:
            # camview 取不到帧,回退到独立抓帧工具复核(此时 camview 多半也异常,不构成抢占)
            snap, _ = sh(ssh, "[ -x %s ] && %s 2>&1 | tail -5 || echo NOSNAP" % (C270_SNAPSHOT, C270_SNAPSHOT), timeout=90)
            if "PHOTO_OK" in snap:
                kv("摄像头", "C270 直采抓帧正常(camview /snapshot 暂未出帧)")
                add("ok", "摄像头", "C270 用户态直采抓帧成功(PHOTO_OK);camview 预览此刻未出帧,看门狗会自愈")
                print(green("  ✓ C270 直采抓帧正常(camview 预览稍后由看门狗恢复)"))
            else:
                usb, _ = sh(ssh, "cat /sys/bus/usb/devices/*/product 2>/dev/null")
                has_cam = any(k in usb.lower() for k in ("c270", "webcam", "camera", "uvc"))
                if has_cam:
                    kv("摄像头", "C270 在 USB 上，但 camview 与直采均取帧失败")
                    add("fail", "摄像头",
                        "C270 已连接但 camview /snapshot 与用户态抓帧工具均未取到帧；"
                        "请查 camview/抓帧工具是否被占用、USB 供电或摄像头连接")
                    print(red("  ✗ C270 在位但取帧失败,视觉链路不可用,需人工排查"))
                else:
                    kv("摄像头", "USB 上未检测到摄像头")
                    add("warn", "摄像头", "USB 总线上未见摄像头，请插回 C270")
                    print(yellow("  △ USB 上没有摄像头，请插回 C270"))

    # 摄像头实时预览 Web 页（camview @ http://<HOST>:8090/）健康检查
    #   camview 是 procd 托管的常驻服务,直采 USB 摄像头并输出 MJPEG 预览。
    #   已知故障:摄像头一旦重新枚举(掉线/重插/USB 复位),camview 仍攥着旧的
    #   已失效句柄(/proc/<pid>/fd 里指向 "/dev/bus/usb/... (deleted)"),此时
    #   HTTP 主页还能开(返回 200),但 /stream 视频流卡死、取不到帧——用户体感即
    #   "8090 打不开"。判据三层:HTTP 可达 + 进程在 + USB 句柄未失效。
    #   注意:设备上已有 camview 看门狗(cron 每 5 分钟)在自愈同一问题。巡检不再
    #   自己抢着重启(否则会与看门狗撞车,在进程切换空窗里误报"重启失败"),而是
    #   带宽限复查最多 3 次——任一次通过即正常;持续异常才判故障并触发一次兜底重启。
    url = "http://%s:%d/" % (HOST, CAM_PREVIEW_PORT)

    def _probe_preview():
        st, er = local_http_get(HOST, CAM_PREVIEW_PORT, "/")
        ok_http = st is not None and 200 <= st < 500
        fd, _ = sh(ssh, "p=$(pgrep -f '/data/camview/camview' | head -1); "
                        "[ -n \"$p\" ] && ls -l /proc/$p/fd 2>/dev/null | grep -i usb || echo NOPROC")
        return ok_http, ("NOPROC" not in fd), ("(deleted)" not in fd), st, er

    ok_http = ok_run = ok_fd = False
    status = err = None
    for attempt in range(3):
        ok_http, ok_run, ok_fd, status, err = _probe_preview()
        if ok_http and ok_run and ok_fd:
            break
        if attempt < 2:
            time.sleep(4)  # 宽限:可能正撞看门狗重启空窗,等几秒复查

    if ok_http and ok_run and ok_fd:
        kv("预览页", "%s → HTTP %d,USB 句柄有效" % (url, status))
        add("ok", "摄像头预览页", "camview %s 可访问(HTTP %d),视频流句柄正常" % (url, status))
        print(green("  ✓ 摄像头预览页正常: %s (HTTP %d)" % (url, status)))
    else:
        why = []
        if not ok_http:
            why.append("HTTP %s" % (status if status is not None else (err or "无响应")))
        if not ok_run:
            why.append("camview 进程未运行")
        if not ok_fd:
            why.append("USB 句柄失效(摄像头重枚举后未重连,视频流会卡死)")
        reason = ";".join(why)
        if do_fix:
            # 复查 3 次仍异常 → 触发一次兜底重启(看门狗 5 分钟才跑一次,巡检立即救一把)
            sh(ssh, "/etc/init.d/camview enabled 2>/dev/null || /etc/init.d/camview enable")
            sh(ssh, "/etc/init.d/camview restart 2>&1", timeout=40)
            time.sleep(6)
            r_http, r_run, r_fd, status2, _ = _probe_preview()
            if r_http and r_run and r_fd:
                kv("预览页", "%s → 曾异常(%s),已重启恢复" % (url, reason))
                add("warn", "摄像头预览页", "camview 曾异常(%s),已自动重启恢复(HTTP %d)" % (reason, status2))
                FIXES.append("重启 camview(8090 预览) → 已恢复")
                print(yellow("  △ 摄像头预览页异常(%s) → 已自动重启恢复" % reason))
            else:
                kv("预览页", "%s → 持续异常,重启后仍未恢复" % url)
                add("fail", "摄像头预览页", "camview %s 持续异常(%s),复查3次+重启后仍未恢复,需人工排查(USB 供电/摄像头连接)" % (url, reason))
                FIXES.append("重启 camview(8090 预览) → 失败,需人工")
                print(red("  ✗ 摄像头预览页持续异常,重启后仍未恢复,需人工排查"))
        else:
            kv("预览页", "%s → 打不开(%s)" % (url, reason))
            add("fail", "摄像头预览页", "camview %s 打不开(%s)(--no-fix 未修复)" % (url, reason))
            print(red("  ✗ 摄像头预览页打不开: %s (%s)" % (url, reason)))

    # camview 看门狗健康确认(每 5 分钟自愈 8090 卡死的守护,部署在 /data/camview/)
    #   根因:C270 USB 独占,姿态检测每小时直开 USB 抓帧会与 camview 抢占,
    #   camview 攥失效句柄卡死;看门狗每 5 分钟检测并自动重启,把卡死窗口压到 5 分钟内。
    #   本项只确认看门狗本身还在岗(脚本在 + cron 在),防止部署被误删后无声失效。
    wd_ok, _ = sh(ssh, "[ -x /data/camview/camview_watchdog.sh ] && echo Y || echo N")
    wd_cron, _ = sh(ssh, "grep -q camview_watchdog /etc/crontabs/root && echo Y || echo N")
    if "Y" in wd_ok and "Y" in wd_cron:
        add("ok", "预览看门狗", "camview_watchdog 已部署(每5分钟自愈8090卡死)")
        print(green("  ✓ camview 看门狗在岗(脚本+cron 就绪,每5分钟自愈)"))
    else:
        miss = []
        if "Y" not in wd_ok: miss.append("守护脚本缺失")
        if "Y" not in wd_cron: miss.append("cron 条目缺失")
        add("warn", "预览看门狗", "camview 看门狗未就位(%s);8090 卡死将只能靠每日巡检重启" % "、".join(miss))
        print(yellow("  △ camview 看门狗未就位(%s),建议重新部署" % "、".join(miss)))

    # 姿态检测 cron 是否存在
    cron, _ = sh(ssh, "cat /etc/crontabs/root 2>/dev/null")
    if "posture_check.py" in cron:
        add("ok", "姿态检测任务", "posture cron 已配置(每小时)")
        print(green("  ✓ 姿态检测定时任务已配置"))
    else:
        if do_fix:
            # 低风险：补回 cron 条目并重载 cron
            sh(ssh, "printf '%%s\\n' \"%s\" >> /etc/crontabs/root" % POSTURE_CRON)
            sh(ssh, "/etc/init.d/cron restart 2>&1")
            add("warn", "姿态检测任务", "cron 缺失，已自动补回")
            FIXES.append("补回 posture 姿态检测 cron 条目")
            print(yellow("  △ 姿态检测 cron 缺失 → 已自动补回"))
        else:
            add("fail", "姿态检测任务", "posture cron 缺失")
            print(red("  ✗ 姿态检测定时任务缺失"))

    # 姿态任务新鲜度：看每小时归档照片，而非告警日志。
    # posture_check.py 只在“检测到有人且坐姿异常”时才写 posture.log，无人/坐姿正常
    # 时不写——用日志新鲜度判健康会把“一切正常、没人需要提醒”误报成任务停摆。
    # 归档照片(archive/hourly_*.jpg)每小时都落，才是抓帧+推理管线是否在跑的真实信号。
    ph, _ = sh(ssh, "ls -t %s/hourly_*.jpg 2>/dev/null | head -1" % POSTURE_ARCHIVE)
    ph = ph.strip().splitlines()[0] if ph.strip() else ""
    if ph:
        fr, _ = sh(ssh, "date -r %s +%%s 2>/dev/null; date +%%s" % ph)
        nums = fr.split()
        if len(nums) == 2:
            try:
                age_h = (int(nums[1]) - int(nums[0])) / 3600.0
                kv("姿态任务", "最近抓帧归档 %.1f 小时前" % age_h)
                add("ok" if age_h < 2.0 else "warn", "姿态任务",
                    "抓帧管线正常，最近归档 %.1f 小时前(每小时一次)" % age_h)
            except Exception:
                kv("姿态任务", "归档存在: %s" % ph)
                add("ok", "姿态任务", "抓帧归档存在")
        else:
            kv("姿态任务", "归档存在: %s" % ph)
            add("ok", "姿态任务", "抓帧归档存在")
    else:
        kv("姿态任务", "无归档照片(可能从未运行或抓帧失败)")
        add("warn", "姿态任务", "archive 下无 hourly_*.jpg，抓帧管线可能未运行")

    # 告警日志仅作参考（有内容=最近有坐姿提醒），不作健康判据
    lg, _ = sh(ssh, "[ -f %s ] && date -r %s +%%s 2>/dev/null; date +%%s" % (POSTURE_LOG, POSTURE_LOG))
    lines = lg.split()
    if len(lines) == 2:
        try:
            age_h = (int(lines[1]) - int(lines[0])) / 3600.0
            kv("最近坐姿提醒", "%.1f 小时前(无提醒=坐姿正常，不代表故障)" % age_h)
        except Exception:
            pass

    # ============ 7. 智能家居 后端可达性 ============
    section("7. 智能家居后端(HA / MQTT @ %s)" % HA_HOST)
    if local_ping(HA_HOST):
        # MQTT
        if local_port_open(HA_HOST, MQTT_PORT):
            add("ok", "MQTT Broker", "%s:%d 可达" % (HA_HOST, MQTT_PORT))
            print(green("  ✓ MQTT %s:%d 可达" % (HA_HOST, MQTT_PORT)))
        else:
            add("fail", "MQTT Broker", "%s:%d 不可达(灯泡/HA 联动会失效)" % (HA_HOST, MQTT_PORT))
            print(red("  ✗ MQTT %s:%d 不可达" % (HA_HOST, MQTT_PORT)))
        # HA
        if local_port_open(HA_HOST, HA_PORT):
            add("ok", "Home Assistant", "%s:%d 可达" % (HA_HOST, HA_PORT))
            print(green("  ✓ Home Assistant %s:%d 可达" % (HA_HOST, HA_PORT)))
        else:
            add("fail", "Home Assistant", "%s:%d 不可达" % (HA_HOST, HA_PORT))
            print(red("  ✗ Home Assistant %s:%d 不可达" % (HA_HOST, HA_PORT)))
    else:
        add("warn", "家居后端", "HA 宿主机 %s ping 不通(可能关机)" % HA_HOST)
        print(yellow("  △ HA/MQTT 宿主机 %s 不可达，智能家居联动暂不可用" % HA_HOST))

    # Tuya 灯泡状态(通过设备上脚本，只读)
    bulb, _ = sh(ssh, "python3 /data/ai_cpe/bulb_control.py status 2>&1 | tail -c 300", timeout=25)
    if bulb:
        low = bulb.lower()
        if '"on"' in low or "true" in low or "online" in low or "'on'" in low:
            kv("Tuya 灯泡", "在线")
            add("ok", "Tuya 灯泡", "状态可读")
        else:
            kv("Tuya 灯泡", bulb.replace("\n", " ")[:80])
            add("warn", "Tuya 灯泡", "状态异常或离线")

    # ============ 8. 已接入客户端 ============
    section("8. 已接入客户端 (LAN)")
    lout, _ = sh(ssh, "cat /tmp/dhcp.leases 2>/dev/null")
    if lout:
        n = 0
        for line in lout.splitlines():
            p = line.split()
            if len(p) >= 3:
                n += 1
                kv(p[2], "MAC=%s  主机=%s" % (p[1], p[3] if len(p) > 3 else "-"))
        add("ok", "LAN 客户端", "%d 台接入" % n)
    else:
        kv("DHCP 租约", "(空)")

    ssh.close()
    _finish(no_pause, do_feishu)


def _summary_text():
    ok = sum(1 for s, _, _ in RESULTS if s == "ok")
    warn = sum(1 for s, _, _ in RESULTS if s == "warn")
    fail = sum(1 for s, _, _ in RESULTS if s == "fail")
    total = ok + warn + fail
    if fail == 0 and warn == 0:
        verdict = "基本功能正常 ✅"
    elif fail == 0:
        verdict = "基本可用，有需关注项 ⚠️"
    else:
        verdict = "存在异常需排查 ❌"
    problems = [f"{l}: {d}" for s, l, d in RESULTS if s in ("warn", "fail")]
    return ok, warn, fail, total, verdict, problems


def _find_lark_cli():
    """定位 lark-cli。它被打包在 QRIBuddy 应用内,不一定在 PATH 里,
    且 AppImage 挂载点(/tmp/.mount_*)每次启动会变——后台跑巡检时尤其如此。
    顺序:PATH → 应用挂载点 glob → 环境变量兜底。找不到返回 None。"""
    import shutil
    import glob as _glob
    p = shutil.which("lark-cli")
    if p:
        return p
    patterns = [
        "/tmp/.mount_QRIBud*/resources/app.asar.unpacked/node_modules/@larksuite/cli/bin/lark-cli",
        "/tmp/.mount_*/resources/app.asar.unpacked/node_modules/@larksuite/cli/bin/lark-cli",
    ]
    env_bin = os.environ.get("QRIBUDDY_LARK_CLI")
    if env_bin:
        patterns.insert(0, env_bin)
    for pat in patterns:
        for h in sorted(_glob.glob(pat)):
            if os.path.isfile(h) and os.access(h, os.X_OK):
                return h
    return None


def _send_feishu(summary_line, problems):
    """尝试通过 lark-cli 给自己发一条巡检摘要。飞书未连接则跳过并提示。"""
    lark = _find_lark_cli()
    if not lark:
        print(yellow("  ⚠ 未找到 lark-cli(已在 PATH 与应用挂载点中查找)，跳过飞书推送(报告已落盘)"))
        return
    try:
        st = subprocess.run([lark, "auth", "status", "--json"],
                            capture_output=True, text=True, timeout=15)
        user = json.loads(st.stdout or "{}").get("identities", {}).get("user", {})
        avail = user.get("available")
        open_id = user.get("openId")
    except Exception:
        avail = None
        open_id = None
    if not avail:
        print(yellow("  ⚠ 飞书未连接(设置→飞书 点连接后生效)，本次仅落盘未推送"))
        return
    if not open_id:
        print(yellow("  ⚠ 飞书已连接但取不到用户 open_id，本次仅落盘未推送"))
        return
    body = "【RG660MK 巡检】%s\n%s" % (time.strftime("%m-%d %H:%M"), summary_line)
    if problems:
        body += "\n需关注:\n- " + "\n- ".join(problems[:8])
    else:
        body += "\n全部正常，无需处理。"
    # 给自己发私聊:+messages-send + 自己的 open_id(旧的 +send-to-me 子命令不存在)
    r = subprocess.run([lark, "im", "+messages-send", "--user-id", open_id, "--text", body],
                       capture_output=True, text=True, timeout=25)
    if r.returncode == 0 and '"ok":true' in (r.stdout.replace(" ", "")):
        print(green("  ✓ 飞书摘要已发送"))
    else:
        print(yellow("  ⚠ 飞书推送未成功(报告已落盘)：%s" % (r.stderr or r.stdout)[:120]))


def _finish(no_pause, do_feishu):
    print("\n" + bold("=" * 60))
    print(bold("   巡检汇总"))
    print(bold("=" * 60))
    for s, label, detail in RESULTS:
        if s == "ok":
            print(green("   [✓] %-14s %s" % (label, detail)))
        elif s == "warn":
            print(yellow("   [△] %-14s %s" % (label, detail)))
        else:
            print(red("   [✗] %-14s %s" % (label, detail)))
    print("-" * 60)
    ok, warn, fail, total, verdict, problems = _summary_text()
    colorfn = green if (fail == 0 and warn == 0) else (yellow if fail == 0 else red)
    print(colorfn("  结论：%s" % verdict))
    print("  正常 %d / 关注 %d / 异常 %d  （共 %d 项）" % (ok, warn, fail, total))
    if FIXES:
        print(bold("\n  本次自动修复:"))
        for f in FIXES:
            print("   · " + f)

    # 落盘报告
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        report_path = os.path.join(SCRIPT_DIR, "rg660mk_巡检报告_%s.txt" % ts)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("RG660MK AI CPE 巡检报告  %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("设备 %s:%s\n\n" % (HOST, PORT))
            for s, label, detail in RESULTS:
                mark = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}.get(s, "[?]  ")
                f.write("%s %-14s %s\n" % (mark, label, detail))
            f.write("\n结论: %s   正常%d/关注%d/异常%d\n" % (verdict, ok, warn, fail))
            if FIXES:
                f.write("\n自动修复:\n")
                for x in FIXES:
                    f.write("  - %s\n" % x)
        print("\n  报告已保存：%s" % report_path)
    except Exception as e:
        print(red("  报告落盘失败: %s" % e))

    # 飞书推送
    if do_feishu:
        _send_feishu("%s（正常%d/关注%d/异常%d）" % (verdict, ok, warn, fail), problems)

    if not no_pause and sys.stdin.isatty():
        try:
            input("\n按回车键退出...")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断")
    except Exception as e:
        print(red("运行出错: %s" % e))
        import traceback
        traceback.print_exc()
        if sys.stdin.isatty() and "--no-pause" not in sys.argv:
            try:
                input("\n按回车键退出...")
            except Exception:
                pass
