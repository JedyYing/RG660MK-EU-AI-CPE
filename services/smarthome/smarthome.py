#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能灯控制门面（MQTT 控制面，无涂鸦）——与旧 bulb_control.py 接口兼容：
    python3 smarthome.py on|off|toggle|status
输出 JSON，含 transport=mqtt 与 latency_ms，便于自动化校验。
"""
import json, os, sys, time
sys.path.insert(0, "/data/ai_cpe/services/smarthome")
from mqtt_lib import Client                                # noqa: E402

BROKER = os.environ.get("MQTT_BROKER", "127.0.0.1")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
T_SET, T_STATE = "rg660mk/bulb/set", "rg660mk/bulb/state"


def _one_shot(action, timeout=30.0):
    """发指令→等状态回执；status 只读。
    超时必须 > Matter/chip-tool 单次执行时间：空载实测 4~12s，**演示负载下实测 19.2s**，
    故取 30s（原 16s 在演示中必然超时→返回旧值误判，2026-09-28 二次修正）。"""
    state = {}
    def _on_msg(topic, payload):                      # 必须注册回调，否则收不到状态
        if topic == T_STATE:
            try:
                state.update(json.loads(payload.decode()))
            except ValueError:
                pass
    cli = Client(BROKER, PORT, "aicpe-cli-%d" % (os.getpid() % 100000), on_message=_on_msg).connect(timeout=3)
    cli.subscribe(T_STATE)
    deadline = time.time() + timeout
    if action == "status":
        while time.time() < deadline and not state:
            time.sleep(0.05)
        cli.close()
        return state
    # 先记下指令前状态时间戳，之后只认“更新的状态”，避免 retained 旧值误判
    while time.time() < deadline and not state:
        time.sleep(0.05)
    ts_before = state.get("ts", 0)
    t0 = time.time()
    cli.publish(T_SET, json.dumps({"state": action}), retain=False)
    while time.time() < deadline and state.get("ts", 0) <= ts_before:
        time.sleep(0.02)
    ms = (time.time() - t0) * 1000
    cli.close()
    return state if state else {"power": "unknown", "error": "timeout/no-state", "latency_ms": ms}


def _send_only(action):
    """只发指令、不等物理确认（语音/看板「秒回」用）。
    指令经本地 broker 交给 smarthome agent（Matter 真灯）异步执行（约 12~19s）；
    物理结果可随时用 `smarthome.py status` 查询。"""
    cli = Client(BROKER, PORT, "aicpe-cli-%d" % (os.getpid() % 100000)).connect(timeout=3)
    cli.publish(T_SET, json.dumps({"state": action}), retain=False)
    time.sleep(0.2)                                   # 让 broker/agent 排空一拍
    cli.close()
    return True


def main():
    argv = sys.argv[1:]
    nowait = "--nowait" in argv[1:]
    action = (argv[0] if argv else "status").lower()
    if action not in ("on", "off", "toggle", "status"):
        print(json.dumps({"success": False, "error": "用法: smarthome.py on|off|toggle|status [--nowait]"})); return 2
    if nowait and action in ("on", "off", "toggle"):
        t0 = time.time()
        try:
            _send_only(action)
            print(json.dumps({"success": True, "sent": True, "transport": "mqtt",
                              "broker": "%s:%d" % (BROKER, PORT),
                              "latency_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False))
            return 0
        except Exception as e:
            print(json.dumps({"success": False, "error": "send failed: %s" % str(e)[:100]}))
            return 1
    t0 = time.time()
    st = _one_shot(action)
    latency = st.pop("latency_ms", (time.time() - t0) * 1000)
    ok = st.get("power") in ("on", "off")
    out = {"success": ok, "transport": "mqtt", "broker": "%s:%d" % (BROKER, PORT),
           "latency_ms": round(latency, 1),
           "result": [{"code": "switch_led", "value": st.get("power") == "on"},
                      {"code": "brightness", "value": st.get("brightness")},
                      {"code": "driver", "value": st.get("driver")}] + ([{"code": "raw", "value": st}] if not ok else [])}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
