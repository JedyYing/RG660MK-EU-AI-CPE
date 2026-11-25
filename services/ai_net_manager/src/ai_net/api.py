# -*- coding: utf-8 -*-
"""本地 HTTP API（设计 §12.1）。纯 stdlib http.server。

安全约定（2026-11-19 安全测试整改）：
  · 默认只绑定回环地址 127.0.0.1（由配置 api.host 决定，不再默认 0.0.0.0）；
  · 所有接口（含根路径与只读遥测）都必须携带令牌：
        Authorization: Bearer <令牌>    或    Authorization: Basic base64(任意用户名:令牌)
  · 令牌从配置 api.auth_token_file 读取，默认 /data/ai_cpe/secrets/ops_token，
    文件须存在、非空，且权限为 0600（不允许组/其他用户访问）；缺失或过宽则拒绝启动；
  · 令牌比较使用 hmac.compare_digest（常量时间）。
"""
import base64
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DEFAULT_TOKEN_FILE = "/data/ai_cpe/secrets/ops_token"
AUDIT_LOG = os.environ.get("AI_NET_AUDIT_LOG", "/data/ai_cpe/logs/ai_net_api.log")


def _audit(line):
    """写审计日志：来源 IP、方法、路径、结果码、UA。写失败不影响服务。"""
    try:
        d = os.path.dirname(AUDIT_LOG)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


def load_token(path):
    """读取 API 令牌。文件须存在、非空，且不允许组或其他用户有任何权限。"""
    if not path or not os.path.isfile(path):
        raise RuntimeError("API 令牌文件不存在: %s" % path)
    if os.stat(path).st_mode & 0o077:
        raise RuntimeError("API 令牌文件权限过宽（须 chmod 600）: %s" % path)
    with open(path, encoding="utf-8") as f:
        tok = f.read().strip()
    if not tok:
        raise RuntimeError("API 令牌文件为空: %s" % path)
    return tok


def _auth_ok(authz, token):
    """校验 Bearer 或 Basic（密码即令牌）。常量时间比较。"""
    if not authz:
        return False
    if authz.startswith("Bearer "):
        got = authz[7:].strip()
    elif authz.startswith("Basic "):
        try:
            raw = base64.b64decode(authz[6:].strip()).decode("utf-8", "replace")
        except Exception:
            return False
        got = raw.partition(":")[2]
    else:
        return False
    return hmac.compare_digest(got.encode("utf-8"), token.encode("utf-8"))


def start_api(service, host="127.0.0.1", port=8123, token=None):
    if not token:
        raise RuntimeError("未提供 API 令牌，拒绝启动管理接口")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-net/0.1"

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _deny(self):
            self.close_connection = True          # 未读取的请求体不复用连接
            body = json.dumps({"error": "unauthorized"}).encode("utf-8")
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="ai-net"')
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            return _auth_ok(self.headers.get("Authorization"), token)

        def do_GET(self):
            if not self._authorized():
                return self._deny()
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
            if not self._authorized():
                return self._deny()
            # 写接口只接受 JSON：强制浏览器走 CORS 预检，阻断 text/plain 简单请求跨站触发。
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return self._send(415, {"error": "unsupported media type"})
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self._send(403, {"error": "cross origin denied"})
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

        def log_request(self, code="-", size="-"):
            _audit("%s %s %s %s ua=%r" % (self.client_address[0], self.command,
                                          self.path, code,
                                          (self.headers.get("User-Agent") or "")[:60]))

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=srv.serve_forever, name="api", daemon=True)
    t.start()
    return srv
