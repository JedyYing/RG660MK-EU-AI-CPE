# -*- coding: utf-8 -*-
"""JSONL writer（设计 §15.2：单条 flush-safe，异常不阻塞采集线程）+ status.json 原子写。"""
import json
import os
import threading
import time


class JsonlWriter:
    def __init__(self, path, rotate_mb=20):
        self.path = path
        self.rotate_bytes = int(rotate_mb * 1024 * 1024)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def write(self, obj):
        line = json.dumps(obj, ensure_ascii=False, default=str)
        with self._lock:
            try:
                if os.path.exists(self.path) and os.path.getsize(self.path) > self.rotate_bytes:
                    bak = self.path + ".1"
                    try:
                        os.replace(self.path, bak)
                    except OSError:
                        pass
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass   # 落盘异常不阻塞采集


class StatusWriter:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def write(self, obj):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass
