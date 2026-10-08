"""本地只读 HTTP 接口（设计文档 12 状态查询）。

仅绑定 127.0.0.1；只读，不接受任何控制指令（控制只能通过配置文件/进程管理）。
端点：
  GET /health          → 存活 + 模式 + 状态机状态
  GET /status          → 完整状态快照（state.json 同构 + 计数）
  GET /decisions?n=50  → 最近 n 条决策（读 decision.jsonl 尾部）
  GET /cells           → 小区历史画像摘要
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def _tail_jsonl(path: str, n: int) -> list:
    """读 JSONL 末尾 n 行（不加载全文件进内存的行数上限保护）。"""
    if not path or not os.path.exists(path):
        return []
    try:
        size = os.path.getsize(path)
        block = min(size, max(65536, n * 2048))
        with open(path, "rb") as f:
            f.seek(size - block)
            data = f.read().decode("utf-8", "replace")
        lines = [ln for ln in data.splitlines() if ln.strip()]
        if size > block:
            lines = lines[1:]           # 丢弃可能截断的首行
        out = []
        for ln in lines[-n:]:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out
    except OSError:
        return []


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-net-manager/1.0"

        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/health":
                    self._send({"ok": True, "mode": service.mode,
                                "state": service.machine.state,
                                "uptime_s": round(service.uptime_s(), 1)})
                elif u.path == "/status":
                    self._send(service.snapshot())
                elif u.path == "/decisions":
                    n = min(500, int((q.get("n") or ["50"])[0]))
                    self._send({"decisions": _tail_jsonl(service.decision_path, n)})
                elif u.path == "/cells":
                    self._send({"cells": service.history.summary()
                                if hasattr(service.history, "summary") else []})
                else:
                    self._send({"error": "not_found"}, 404)
            except Exception as e:                      # 只读接口不让异常杀服务
                self._send({"error": str(e)}, 500)

        def log_message(self, *a):                       # 静默（日志走 JSONL）
            pass

    return Handler


class StatusAPI:
    def __init__(self, service, host: str = "127.0.0.1", port: int = 8787):
        self.service = service
        self.host, self.port = host, port
        self.httpd = None
        self._thread = None

    def start(self) -> bool:
        try:
            self.httpd = ThreadingHTTPServer((self.host, self.port), make_handler(self.service))
        except OSError:
            return False
        self._thread = threading.Thread(target=self.httpd.serve_forever,
                                        kwargs={"poll_interval": 0.5}, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
