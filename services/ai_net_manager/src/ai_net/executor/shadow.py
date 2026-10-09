# -*- coding: utf-8 -*-
"""Shadow 执行器：只记录建议，不做任何控制（设计 §8.2 L0）。"""


class ShadowExecutor:
    mode = "shadow"

    def propose(self, action, params=None):
        return {"executed": False, "proposed": action, "params": params or {},
                "note": "shadow: recorded only"}
