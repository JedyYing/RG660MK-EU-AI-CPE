#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI CPE 智能灯代理（MQTT 控制面，零涂鸦依赖）。

V4.0 可靠性整改：
- 保留 rg660mk/bulb/set 兼容入口，新增 rg660mk/bulb/request 与 /response；
- 每个请求带 request_id，响应必须匹配 request_id，retained 不能冒充实时响应；
- 分层状态：agent / mqtt / wifi / ip / mdns / case / matter；
- SIGTERM/SIGINT 干净退出；跨进程 flock 防止重复 agent；MQTT 断开自动重连并重新订阅。
"""
import argparse, fcntl, json, os, re, signal, subprocess, sys, time

sys.path.insert(0, "/data/ai_cpe/services/smarthome")
from mqtt_lib import Client                                  # noqa: E402
from drivers.factory import get_driver                        # noqa: E402

T_SET, T_REQ, T_STATE, T_RESP = ("rg660mk/bulb/set", "rg660mk/bulb/request",
                                 "rg660mk/bulb/state", "rg660mk/bulb/response")
T_AVAIL, T_HEALTH = "rg660mk/bulb/available", "rg660mk/bulb/agent_health"
T_DISC = "homeassistant/light/rg660mk_bulb/config"
LOCK_FILE = "/var/run/smarthome-agent.lock"
START_TS = time.time()
STOP = False
DRV = get_driver()
HOLDER = {}
LAST_REQ = {}


def log(msg):
    print("[agent] %s" % msg, flush=True)


def _sig(signum, frame):
    global STOP
    STOP = True
    log("收到信号 %s，准备退出" % signum)


def _acquire_lock():
    f = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("已有 agent 持有锁 %s，退出" % LOCK_FILE)
        return None
    f.seek(0); f.truncate(); f.write(str(os.getpid())); f.flush()
    return f


def _run(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as e:
        return 127, str(e)


def _wifi_ip():
    rc, out = _run("iw dev ra0 station dump 2>/dev/null", 3)
    assoc = "d8:fc:92:b8:b1:41" in out
    rc, _ = _run("ping -c1 -W1 192.168.1.134 >/dev/null 2>&1", 3)
    return ("associated" if assoc else "disconnected"), ("reachable" if rc == 0 else "unreachable")


def _matter_layers(st):
    """从驱动结果/错误文本做分层分类；不把未知硬说成成功。"""
    err = str(st.get("error") or "")
    low = err.lower()
    if st.get("power") in ("on", "off") and not err:
        return "ok", "established", "confirmed"
    if "mdns" in low or "addressresolve" in low or "discovery" in low or "地址解析" in err:
        return "failed", "not_reached", "not_reached"
    if "case" in low or "securechannel" in low or "session" in low:
        return "ok", "failed", "not_reached"
    if "回读" in err or "state_mismatch" in low or "未收敛" in err:
        return "ok", "established", "state_mismatch"
    if "超时" in err or "timeout" in low or "rc=124" in low:
        return "timeout", "unknown", "timeout"
    return "unknown", "unknown", "unknown"


def _fields(request_id, st, result, error_code="", error_detail=""):
    wifi, ip = _wifi_ip()
    mdns, case, matter = _matter_layers(st)
    return {
        "request_id": request_id,
        "timestamp": int(time.time() * 1000),
        "device_id": "rg660mk-bulb",
        "node_id": getattr(DRV, "node_id", None),
        "agent_status": "running",
        "mqtt_status": "connected" if HOLDER.get("connected") else "disconnected",
        "wifi_status": wifi,
        "ip_status": ip,
        "mdns_status": mdns,
        "case_status": case,
        "matter_status": matter,
        "operation_result": result,
        "error_code": error_code,
        "error_detail": error_detail[:400],
        "power": st.get("power", "unknown"),
        "state_cache_age_s": st.get("cache_age_s"),
        "brightness": st.get("brightness"),
        "driver": st.get("driver") or getattr(DRV, "name", "?"),
        "live": True,
        "last_known": False,
    }


def _publish(payload, retain_state=False):
    cli = HOLDER.get("cli")
    body = json.dumps(payload, ensure_ascii=False)
    if cli and HOLDER.get("connected"):
        try:
            cli.publish(T_RESP, body, retain=False)
            if retain_state:
                cli.publish(T_STATE, body, retain=True)
        except Exception as e:
            log("publish 失败: %s" % e)


def discovery_payload():
    return {"name": "RG660MK 本地灯", "unique_id": "rg660mk_bulb_local", "schema": "json",
            "command_topic": T_SET, "state_topic": T_STATE, "availability_topic": T_AVAIL,
            "brightness": True, "brightness_scale": 100,
            "device": {"identifiers": ["rg660mk"], "name": "RG660MK AI CPE",
                       "model": "RG660MK", "manufacturer": "AI CPE"}}


def _handle(cmd, request_id):
    act = cmd.get("state") or cmd.get("power") or cmd.get("command")
    if cmd.get("toggle"):
        act = "toggle"
    if str(act).lower() in ("on", "true", "1"):
        act = "on"
    elif str(act).lower() in ("off", "false", "0"):
        act = "off"
    if act == "health":
        return _fields(request_id, {"power": "unknown", "driver": getattr(DRV, "name", "?")},
                       "health_ok")
    if act not in ("on", "off", "toggle", "status"):
        return _fields(request_id, {"power": "unknown", "driver": getattr(DRV, "name", "?")},
                       "rejected", "BAD_COMMAND", "无法识别的命令: %r" % (cmd,))
    try:
        if act == "status":
            st = DRV.get_state(force=False)  # 短缓存；若无缓存驱动会真实读取
            result = "status_ok" if st.get("power") in ("on", "off") and not st.get("error") else "status_failed"
        else:
            st = DRV.set_power(act, brightness=cmd.get("brightness"))
            want = {"on": "on", "off": "off"}.get(act)
            ok = (not st.get("error")) and (st.get("power") in ("on", "off")) and (want is None or st.get("power") == want)
            result = "executed" if ok else "state_mismatch"
        code = "" if result in ("status_ok", "executed", "health_ok") else "MATTER_" + result.upper()
        detail = str(st.get("error") or "")[:400]
        return _fields(request_id, st, result, code, detail)
    except Exception as e:
        return _fields(request_id, {"power": "unknown", "driver": getattr(DRV, "name", "?")},
                       "exception", "AGENT_EXCEPTION", str(e))


def on_msg(topic, payload):
    if topic not in (T_SET, T_REQ):
        return
    try:
        cmd = json.loads(payload.decode() or "{}")
    except ValueError:
        cmd = {"state": payload.decode().strip()}
    request_id = str(cmd.get("request_id") or "legacy-%d-%d" % (int(time.time() * 1000), os.getpid()))
    # 同一 request_id 30 秒内重复投递：只回放响应，不重复执行
    old = LAST_REQ.get(request_id)
    if old and time.time() - old[0] < 30:
        _publish(old[1], retain_state=False)
        return
    if STOP:
        return
    t0 = time.time()
    try:
        resp = _handle(cmd, request_id)
    except Exception as e:
        resp = _fields(request_id, {"power": "unknown", "driver": getattr(DRV, "name", "?")},
                       "exception", "AGENT_EXCEPTION", str(e))
    LAST_REQ[request_id] = (time.time(), resp)
    if len(LAST_REQ) > 200:
        for k in sorted(LAST_REQ, key=lambda x: LAST_REQ[x][0])[:100]:
            LAST_REQ.pop(k, None)
    _publish(resp, retain_state=True)
    log("$ %s -> %s（%.1f ms，matter=%s）" % (request_id, resp.get("operation_result"),
                                             (time.time() - t0) * 1000, resp.get("matter_status")))


def main():
    global STOP
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default=os.environ.get("MQTT_BROKER", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    a = ap.parse_args()
    lf = _acquire_lock()
    if lf is None:
        return 1
    while not STOP:
        cli = None
        try:
            cli = Client(a.broker, a.port, "rg660mk-bulb-agent", on_message=on_msg, keepalive=30).connect()
            HOLDER["cli"] = cli; HOLDER["connected"] = True
            cli.publish(T_DISC, json.dumps(discovery_payload(), ensure_ascii=False), retain=True)
            cli.publish(T_AVAIL, "online", retain=True)
            cli.subscribe(T_SET); cli.subscribe(T_REQ)
            log("connected %s:%d | driver=%s" % (a.broker, a.port, getattr(DRV, "name", "?")))
            st = DRV.get_state()
            _publish(_fields("startup", st, "status_ok" if st.get("power") in ("on", "off") else "status_failed",
                             "" if st.get("power") in ("on", "off") else "STARTUP_NO_STATE",
                             str(st.get("error") or "")), retain_state=False)
            last_beat = 0.0
            while not STOP:
                if not cli.is_alive():
                    raise ConnectionError("MQTT loop stopped")
                if time.time() - last_beat > 20:
                    beat = {"agent_status": "running", "mqtt_status": "connected",
                            "pid": os.getpid(), "uptime_s": round(time.time() - START_TS, 1),
                            "subscriptions": [T_SET, T_REQ], "timestamp": int(time.time() * 1000)}
                    cli.publish(T_HEALTH, json.dumps(beat, ensure_ascii=False), retain=False)
                    last_beat = time.time()
                time.sleep(2)
        except Exception as e:
            log("连接/运行失败(%s)，3s 后重试" % e)
            HOLDER["connected"] = False
        finally:
            HOLDER["connected"] = False
            if cli:
                try:
                    cli.publish(T_AVAIL, "offline", retain=True)
                    cli.close()
                except Exception:
                    pass
        if not STOP:
            time.sleep(3)
    log("退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
