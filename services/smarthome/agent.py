#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI CPE 智能灯代理（MQTT 控制面，零涂鸦依赖）。

订阅 rg660mk/bulb/set → 驱动本地执行器（板载 LED / 虚拟） → 发布 rg660mk/bulb/state
并保留 Home Assistant MQTT Discovery 能力。
"""
import argparse, json, os, sys, time

sys.path.insert(0, "/data/ai_cpe/services/smarthome")
from mqtt_lib import Client                                  # noqa: E402

T_SET, T_STATE, T_AVAIL = "rg660mk/bulb/set", "rg660mk/bulb/state", "rg660mk/bulb/available"
T_DISC = "homeassistant/light/rg660mk_bulb/config"
from drivers.factory import get_driver                        # noqa: E402
DRV = get_driver()   # 配置决定：Matter 真灯 / 板载 LED / 虚拟
HOLDER = {}


def discovery_payload():
    return {"name": "RG660MK 本地灯", "unique_id": "rg660mk_bulb_local", "schema": "json",
            "command_topic": T_SET, "state_topic": T_STATE, "availability_topic": T_AVAIL,
            "brightness": True, "brightness_scale": 100,
            "device": {"identifiers": ["rg660mk"], "name": "RG660MK AI CPE", "model": "RG660MK", "manufacturer": "AI CPE"}}


def publish_state():
    st = DRV.get_state()
    cli = HOLDER.get("cli")
    if cli:
        cli.publish(T_STATE, json.dumps(st, ensure_ascii=False), retain=True)
    return st


def on_msg(topic, payload):                                   # ← 每次收到命令
    if topic != T_SET:
        return
    try:
        cmd = json.loads(payload.decode() or "{}")
    except ValueError:
        cmd = {"state": payload.decode().strip()}
    act = cmd.get("state") or cmd.get("power") or ("toggle" if cmd.get("toggle") else None)
    if str(act).lower() in ("on", "true", "1"):
        act = "on"
    elif str(act).lower() in ("off", "false", "0"):
        act = "off"
    if act not in ("on", "off", "toggle"):
        print("[agent] 忽略无法识别的指令: %r" % (cmd,), flush=True)
        return
    t0 = time.time()
    st = DRV.set_power(act, brightness=cmd.get("brightness"))
    publish_state()
    print("[agent] 执行 %s → %s（%.1f ms，driver=%s）" % (act, st["power"], (time.time() - t0) * 1000, st.get("driver")), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default=os.environ.get("MQTT_BROKER", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    a = ap.parse_args()
    while True:
        try:
            cli = Client(a.broker, a.port, "rg660mk-bulb-agent", on_message=on_msg, keepalive=30).connect()
            HOLDER["cli"] = cli
            cli.publish(T_DISC, json.dumps(discovery_payload(), ensure_ascii=False), retain=True)
            cli.publish(T_AVAIL, "online", retain=True)
            st = publish_state()
            print("[agent] connected %s:%d | driver=%s | state=%s" % (a.broker, a.port, st.get("driver"), st.get("power")), flush=True)
            cli.subscribe(T_SET)
            print("[agent] subscribed %s" % T_SET, flush=True)
            while True:
                time.sleep(5)
        except Exception as e:
            print("[agent] 连接失败(%s)，3s 后重试" % e, flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
