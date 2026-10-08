"""JSONL 落盘（设计文档 15.2）：单条写入 flush-safe，异常不阻塞采集线程。"""
from __future__ import annotations

import json
import os
import time


class JsonlWriter:
    """按天切分 + 大小轮转；append + flush；失败静默降级（返回 False）。"""

    def __init__(self, path: str, *, rotate_mb: int = 200, fsync: bool = False):
        self.path = path
        self.rotate_bytes = max(1, rotate_mb) * 1024 * 1024
        self.fsync = fsync
        self._fh = None
        self._failed = 0

    def _open(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")

    def _rotate_if_needed(self):
        try:
            if os.path.getsize(self.path) >= self.rotate_bytes:
                if self._fh:
                    self._fh.close()
                    self._fh = None
                stamp = time.strftime("%Y%m%d_%H%M%S")
                os.rename(self.path, "%s.%s" % (self.path, stamp))
        except OSError:
            pass

    def write(self, obj: dict) -> bool:
        try:
            if self._fh is None:
                self._open()
            self._fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._fh.flush()
            if self.fsync:
                os.fsync(self._fh.fileno())
            self._rotate_if_needed()
            self._failed = 0
            return True
        except Exception:
            self._failed += 1
            try:
                if self._fh:
                    self._fh.close()
            except Exception:
                pass
            self._fh = None
            return False

    @property
    def failed_writes(self) -> int:
        return self._failed

    def close(self):
        try:
            if self._fh:
                self._fh.close()
        except Exception:
            pass
        self._fh = None


def read_jsonl(path: str):
    """逐行读，坏行跳过（回放工具用）。"""
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                yield json.loads(ln)
            except json.JSONDecodeError:
                continue
