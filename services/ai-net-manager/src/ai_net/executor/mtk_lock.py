"""L3 执行器：MTK AT+EMMCHLCK（RG660MK-EU 唯一实测可用的锁小区命令，2026-10-08 验证）。

设计约束（13 章）：
- 结构化参数 + 合法域校验（modem.build_lock_command 内置），禁止拼接任意 AT 字符串；
- 命令经 ATTransport（互斥 + 守卫），失败计数交给 service 决定自动降级；
- 锁定跨重启保留 —— 撤销唯一正确方式是 AT+EMMCHLCK=0，绝不用重启"解锁"；
- 执行后立刻回读 AT+EMMCHLCK? 状态；功能性验证（服务小区是否一致）由服务的
  VERIFY 窗口完成（避免在决策线程里长阻塞）。
"""
from __future__ import annotations

from ai_net.collectors import modem


class MtkLockExecutor:
    level = "L3"

    def __init__(self, transport, raw_logger=None):
        self.t = transport
        self.raw_logger = raw_logger
        self.failures = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    def lock_cell(self, rat, arfcn, pci) -> dict:
        try:
            cmd = modem.build_lock_command(rat, arfcn, pci)
        except ValueError as e:
            self.failures += 1
            self.last_error = str(e)
            return {"ok": False, "detail": "invalid_params: %s" % e}
        r = self.t.send(cmd)
        self._log("lock", cmd, r)
        if not r.ok:
            self.failures += 1
            self.last_error = r.error
            return {"ok": False, "detail": "at_error: %s" % (r.error or r.raw[:120])}
        state = self._state()
        if state != 1:
            self.failures += 1
            self.last_error = "state=%s" % state
            return {"ok": False, "detail": "lock_not_active(state=%s)" % state}
        self.failures = 0
        self.last_error = None
        return {"ok": True, "detail": "locked rat=%d arfcn=%d pci=%d"
                % (int(rat), int(arfcn), int(pci))}

    def unlock(self) -> dict:
        cmd = modem.build_unlock_command()
        r = self.t.send(cmd)
        self._log("unlock", cmd, r)
        if not r.ok:
            self.failures += 1
            self.last_error = r.error
            return {"ok": False, "detail": "at_error: %s" % (r.error or r.raw[:120])}
        state = self._state()
        if state != 0:
            self.failures += 1
            self.last_error = "state=%s" % state
            return {"ok": False, "detail": "still_locked(state=%s)" % state}
        self.failures = 0
        return {"ok": True, "detail": "unlocked"}

    # ------------------------------------------------------------------
    def verify_lock(self, expect: dict) -> dict:
        """功能验证：锁状态 + 服务小区是否等于期望（供 VERIFY 窗口调用）。"""
        state = self._state()
        serving = self.read_serving()
        ok = (state == 1 and serving.get("arfcn") == expect.get("arfcn")
              and serving.get("pci") == expect.get("pci"))
        return {"ok": ok, "detail": "state=%s serving=%s" % (state, serving)}

    def read_serving(self) -> dict:
        r = self.t.send(modem.CMD_ECELLMEAS_QUERY)
        if not r.ok:
            return {}
        rows = modem.parse_ecellmeas_lines(r.lines)
        if not rows:
            return {}
        return {"rat": rows[0].get("rat"), "arfcn": rows[0].get("arfcn"),
                "pci": rows[0].get("pci"), "cid": rows[0].get("cid")}

    def _state(self):
        r = self.t.send(modem.CMD_EMMCHLCK_QUERY)
        return modem.parse_lock_state(r.lines) if r.ok else None

    def _log(self, kind: str, cmd: str, r) -> None:
        if self.raw_logger:
            try:
                self.raw_logger.write({"kind": "executor:" + kind, "cmd": cmd,
                                       "ok": r.ok, "error": r.error,
                                       "raw": r.raw[:1200]})
            except Exception:
                pass
