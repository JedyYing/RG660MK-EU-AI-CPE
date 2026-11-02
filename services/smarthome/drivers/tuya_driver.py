#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""涂鸦云执行器驱动 —— 真实灯具控制（当 Matter 无配网窗口 / 无 BLE 时唯一可用路径）。

凭据：TUYA_ACCESS_ID / TUYA_ACCESS_SECRET
      （环境变量 或 /data/hermes/.hermes/.env，由 bulb_control 统一读取）
设备：config.json 的 tuya.device_id，缺省用 bulb_control.DEVICE_ID
接口与其它驱动一致：set_power(on/off/toggle, brightness) / get_state()
"""
import json
import os
import sys
import time

sys.path.insert(0, "/data/ai_cpe")
STATE = "/data/ai_cpe/services/smarthome/state.json"


class TuyaDriver:
    name = "tuya"

    def __init__(self, device_id=None, state_file=STATE, timeout=15):
        self.state_file = state_file
        self.timeout = int(timeout or 15)
        try:
            import bulb_control as bc
            self.bc = bc
        except Exception as exc:  # 驱动模块缺失 → 视为不可用
            self.bc = None
            self._err = "bulb_control 不可用: %s" % type(exc).__name__
            self.device_id = device_id
            self._creds = False
            return
        self.device_id = device_id or bc.DEVICE_ID
        self._creds = bool(bc.ACCESS_ID and bc.ACCESS_SECRET)
        self._err = "" if self._creds else "缺少 TUYA_ACCESS_ID / TUYA_ACCESS_SECRET"

    # ---------------- 内部 ----------------
    def available(self):
        return bool(self._creds)

    def _save(self, st):
        st["ts"] = int(time.time() * 1000)
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
        except OSError:
            pass

    def _fields(self):
        if not self._creds:
            raise RuntimeError(self._err)
        tok = self.bc.get_token()
        if not tok:
            raise RuntimeError("涂鸦 token 获取失败（检查 Access ID/Secret 与数据中心）")
        r = self.bc.http("GET", "v1.0/devices/%s/status" % self.device_id, token=tok)
        if not isinstance(r, dict) or not r.get("success"):
            raise RuntimeError("涂鸦状态查询失败: %s" % json.dumps(r, ensure_ascii=False)[:140])
        return {d.get("code"): d.get("value") for d in (r.get("result") or [])}

    # ---------------- 对外 ----------------
    def get_state(self):
        f = self._fields()
        sw = f.get("switch_led")
        if sw is None:
            raise RuntimeError("涂鸦返回里没有 switch_led（设备不在线或产品码不符）")
        st = {"power": "on" if bool(sw) else "off",
              "brightness": f.get("bright_value_v2") or f.get("bright_value") or 100,
              "driver": self.name,
              "online": True,
              "ts": int(time.time() * 1000)}
        self._save(st)
        return st

    def set_power(self, action, brightness=None):
        cur = self.get_state()["power"]
        if action == "toggle":
            want = "off" if cur == "on" else "on"
        elif action in ("on", "off"):
            want = action
        else:
            raise ValueError("action 必须是 on/off/toggle")
        tok = self.bc.get_token()
        if not tok:
            raise RuntimeError("涂鸦 token 获取失败")
        body = json.dumps({"commands": [{"code": "switch_led", "value": want == "on"}]},
                          separators=(",", ":"))
        r = self.bc.http("POST", "v1.0/devices/%s/commands" % self.device_id, token=tok,
                         body=body, ctype="Content-type:application/json")
        if not isinstance(r, dict) or not r.get("success"):
            raise RuntimeError("涂鸦开/关指令失败: %s" % json.dumps(r, ensure_ascii=False)[:140])
        # 等云端状态收敛，拿到真实回读再返回（不接受“已发送”当成功）
        deadline = time.time() + self.timeout
        last = None
        while time.time() < deadline:
            try:
                st = self.get_state()
            except RuntimeError:
                time.sleep(0.6)
                continue
            last = st
            if st["power"] == want:
                return st
            time.sleep(0.6)
        raise RuntimeError("涂鸦指令已发送但状态未收敛（%s→%s，最后回读=%s）"
                           % (cur, want, (last or {}).get("power")))
