# -*- coding: utf-8 -*-
"""本地 HTTP API（设计 §12.1）。纯 stdlib http.server。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def start_api(service, host="0.0.0.0", port=8123):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-net/0.1"

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = self.path.split("?", 1)[0]
            if p == "/v1/status":
                return self._send(200, service.get_status())
            if p == "/v1/radio":
                return self._send(200, service.get_radio())
            if p == "/v1/candidates":
                return self._send(200, service.get_candidates())
            if p == "/v1/decision":
                return self._send(200, service.get_decision())
            if p == "/":
                return self._send(200, {"name": "ai-net-manager", "mode": service.mode,
                                        "endpoints": ["GET /v1/status", "GET /v1/radio",
                                                      "GET /v1/candidates", "GET /v1/decision",
                                                      "POST /v1/mode", "POST /v1/reload-model"]})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            ln = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(ln) if ln else b"{}"
            try:
                req = json.loads(raw or b"{}")
            except Exception:
                req = {}
            if self.path == "/v1/mode":
                return self._send(200, service.set_mode(req.get("mode")))
            if self.path == "/v1/reload-model":
                return self._send(200, service.reload_model())
            return self._send(404, {"error": "not found"})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=srv.serve_forever, name="api", daemon=True)
    t.start()
    return srv
