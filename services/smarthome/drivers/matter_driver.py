#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Matter（chip-tool）执行器驱动 —— 用本机 Matter 控制器直接控真灯，零涂鸦、零云。

前提：仓库 artifacts/matter-v1.6.0.0-rg660/chip-tool 已放到 CHIP_TOOL（本设备交叉编译版，静态链接），
且在 config.json 里配置了已配网（commissioned）的 node_id。
"""
import json, os, re, shutil, subprocess, time

CHIP_TOOL = os.environ.get("CHIP_TOOL", "/data/ai_cpe/matter/bin/chip-tool")
KVS_DIR = os.environ.get("CHIP_KVS_DIR", "/data/ai_cpe/matter/kvs")
# 本 chip-tool 构建(1.6.0.0)的持久化 = 一组 ini 文件（chip_tool_config.ini 为 fabric 主存储）；
# /tmp 是 tmpfs 重启即丢 → 每次调用前后与 /data 副本互搬（2026-09-28 实测修正，原按 /tmp/chip_kvs 搬运是错的）
KVS_FILES = ("chip_tool_config.ini", "chip_tool_config.alpha.ini",
             "chip_config.ini", "chip_factory.ini", "chip_counters.ini")
STATE = "/data/ai_cpe/services/smarthome/state.json"


class MatterDriver:
    name = "matter"

    def __init__(self, node_id=None, endpoint=1, timeout=12, state_file=STATE):
        self.node_id = node_id
        self.endpoint = int(endpoint or 1)
        self.timeout = timeout
        self.state_file = state_file
        os.makedirs(KVS_DIR, exist_ok=True)

    # ---------------- 内部 ----------------
    def _chip(self, *args, timeout=None):
        # 本构件不认 --KVS（放在任何位置都报 "Optional argument does not exist"）：
        # 改用 chip-tool 默认 KVS 路径（/tmp/chip_kvs），由我们负责持久化（见 _restore/_persist）
        self._restore()
        cmd = [CHIP_TOOL] + [str(a) for a in args]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or self.timeout, cwd="/data/ai_cpe/matter")
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, "chip-tool 超时（%ss）" % (timeout or self.timeout)
        except OSError as e:
            return 127, "chip-tool 不可用: %s" % e

    # ---- 持久化（/tmp 是 tmpfs，重启即丢；旧验收报告里控制器状态就是这样丢的）----
    # 2026-09-28 实测：fabric 实体在 /tmp/chip_tool_config.ini（+附属 ini），按 KVS_FILES 互搬
    def _restore(self):
        for n in KVS_FILES:
            src = os.path.join(KVS_DIR, n)
            if os.path.exists(src) and os.path.getsize(src) > 0:
                try:
                    shutil.copyfile(src, os.path.join("/tmp", n))
                except OSError:
                    pass

    def _persist(self):
        try:
            os.makedirs(KVS_DIR, exist_ok=True)
        except OSError:
            pass
        for n in KVS_FILES:
            src = os.path.join("/tmp", n)
            if os.path.exists(src):
                try:
                    shutil.copyfile(src, os.path.join(KVS_DIR, n))
                except OSError:
                    pass

    def available(self):
        return os.path.exists(CHIP_TOOL) and bool(self.node_id)

    # ---------------- 接口（与其它驱动一致） ----------------
    def set_power(self, action, brightness=None):
        if not self.node_id:
            raise RuntimeError("未配置 Matter node_id（先配网：services/smarthome/matter_commission.sh）")
        act = {"on": "on", "off": "off", "toggle": "toggle"}.get(action)
        if not act:
            raise ValueError("action 必须是 on/off/toggle")
        rc, out = self._chip("onoff", act, self.node_id, self.endpoint)
        self._persist()
        # 回读校验：个别灯珠物理执行/回读会滞后于命令回执（2026-09-28 实测竞态），
        # 最多 3 次回读直至与预期一致（on/off 类命令）
        want = {"on": "on", "off": "off"}.get(act)
        st = None
        for _ in range(3):
            time.sleep(1.0)
            st = self.get_state()
            if want is None or st.get("power") == want:
                break
        if st is None:
            st = self.get_state()
        st["last_cmd"] = act
        st["last_rc"] = rc
        st["last_out"] = out.strip()[-160:] if rc != 0 else "ok"
        st["driver"] = self.name
        self._save(st)
        return st

    def get_state(self):
        st = {"power": "unknown", "brightness": None, "driver": self.name, "ts": int(time.time() * 1000),
              "node_id": self.node_id, "endpoint": self.endpoint}
        if not self.available():
            st["error"] = "Matter 未配置/控制器缺失"
            return st
        rc, out = self._chip("onoff", "read", "on-off", self.node_id, self.endpoint)
        # 必须精确解析 "OnOff: TRUE/FALSE" 行：整段输出含样板行 "SuppressResponse = true"，
        # 旧的整串子串匹配会永远误判为 on（2026-09-28 实测修正）
        m = re.search(r"OnOff:\s*(TRUE|FALSE)", out, re.I)
        if rc == 0 and m:
            st["power"] = "on" if m.group(1).upper() == "TRUE" else "off"
            st["raw"] = m.group(0).strip()[:120]
        else:
            st["error"] = "读取失败(rc=%d): %s" % (rc, out.strip()[-120:])
        return st

    def _save(self, st):
        try:
            old = {}
            if os.path.exists(self.state_file):
                old = json.load(open(self.state_file, encoding="utf-8"))
            old.update(st)
            open(self.state_file, "w", encoding="utf-8").write(json.dumps(old, ensure_ascii=False))
        except OSError:
            pass
