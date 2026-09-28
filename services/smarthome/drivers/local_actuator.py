#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地执行器驱动（替代涂鸦云）：把「灯泡」落到本机可验证的物理/逻辑输出上。

- BoardLedDriver ：写 /sys/class/leds/<name>/brightness（默认 5g_evb_voice，可用配置改）
- VirtualDriver  ：只维护状态文件（无 LED 时；用于纯逻辑验证）
两者接口一致：set_power(on/off/toggle) / get_state() -> {"power":..,"brightness":..,"driver":..}
"""
import json, os, time

STATE = "/data/ai_cpe/services/smarthome/state.json"


class VirtualDriver:
    name = "virtual"

    def __init__(self, led=None, state_file=STATE):
        self.state_file = state_file
        self.state = {"power": "off", "brightness": 100, "driver": self.name, "ts": 0}
        self._load()

    def _load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                d = json.load(f)
            for k in ("power", "brightness"):
                if k in d:
                    self.state[k] = d[k]
        except (OSError, ValueError):
            pass

    def _save(self):
        self.state["ts"] = int(time.time() * 1000)
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)

    def set_power(self, action, brightness=None):
        cur = self.state["power"]
        new = "on" if action == "toggle" and cur == "off" else ("off" if action == "toggle" else action)
        if new not in ("on", "off"):
            raise ValueError("action 必须是 on/off/toggle")
        self.state["power"] = new
        if brightness is not None:
            self.state["brightness"] = int(brightness)
        self._apply_hook()
        self._save()
        return self.get_state()

    def _apply_hook(self):
        pass

    def get_state(self):
        return dict(self.state)


class BoardLedDriver(VirtualDriver):
    """用板载 LED 作为“本地灯泡”的可观测量（真实物理输出，便于现场演示/自动化验证）。"""
    name = "board-led"

    def __init__(self, led=None, state_file=STATE):
        self.led = led or os.environ.get("AICPE_LED", "5g_evb_voice")
        self.path = "/sys/class/leds/%s/brightness" % self.led
        self.available = os.path.exists(self.path)
        super().__init__(led=self.led, state_file=state_file)
        self.state["driver"] = self.name if self.available else "virtual(fallback)"
        if not self.available:
            self.led = None

    def _apply_hook(self):
        if not self.led:
            return
        try:
            with open(self.path, "w") as f:
                f.write("1" if self.state["power"] == "on" else "0")
        except OSError as e:
            self.state["driver"] = "virtual(led-write-failed:%s)" % e
