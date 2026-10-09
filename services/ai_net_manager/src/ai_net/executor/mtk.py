# -*- coding: utf-8 -*-
"""MTK 执行器（Phase 2 预留；默认禁用）。
动作白名单；参数结构化；失败计数超限自动降级（设计 §7.3/§13）。"""
import time

from ..collectors.at_transport import mipc

ALLOWED_ACTIONS = {"radio_cycle"}


class MTKExecutor:
    def __init__(self, executor_cfg, safety_cfg):
        self.enabled = bool((executor_cfg or {}).get("enabled", False))
        self.max_fail = int((safety_cfg or {}).get("max_consecutive_action_failures", 2))
        self.auto_disable = bool((safety_cfg or {}).get("auto_disable_on_modem_error", True))
        self.fail_count = 0

    def execute(self, action):
        if not self.enabled:
            raise RuntimeError("executor disabled by config (executor.enabled=false)")
        if action not in ALLOWED_ACTIONS:
            raise ValueError("action not in allowlist: %s" % action)
        if action == "radio_cycle":
            rc1, _ = mipc(["--nw_radio_state_set", "0"], 15)
            time.sleep(5)
            rc2, _ = mipc(["--nw_radio_state_set", "1"], 15)
            ok = (rc1 == 0 and rc2 == 0)
            self.fail_count = 0 if ok else self.fail_count + 1
            if self.auto_disable and self.fail_count >= self.max_fail:
                self.enabled = False
                return {"executed": False, "rc_off": rc1, "rc_on": rc2,
                        "note": "auto-disabled after %d failures" % self.fail_count}
            return {"executed": ok, "rc_off": rc1, "rc_on": rc2}
        return {"executed": False}
