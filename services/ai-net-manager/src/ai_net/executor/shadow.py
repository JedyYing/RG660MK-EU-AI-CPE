"""Shadow 执行器：只记录"本该执行什么"，绝不写 Modem（设计 8.2 L0）。"""
from __future__ import annotations


class ShadowExecutor:
    level = "L0"

    def __init__(self):
        self.would_do: list = []

    def lock_cell(self, rat, arfcn, pci) -> dict:
        self.would_do.append({"action": "lock_cell",
                              "params": {"rat": rat, "arfcn": arfcn, "pci": pci}})
        return {"ok": True, "detail": "shadow: would lock rat=%s arfcn=%s pci=%s"
                % (rat, arfcn, pci)}

    def unlock(self) -> dict:
        self.would_do.append({"action": "unlock_cell"})
        return {"ok": True, "detail": "shadow: would unlock"}

    def verify_lock(self, expect: dict) -> dict:
        return {"ok": True, "detail": "shadow: no modem state"}
