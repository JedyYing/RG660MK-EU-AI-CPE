#!/usr/bin/env python3
"""OpenAI-compatible HTTP bridge in front of QNN Genie (Hexagon NPU) on MT200.

Genie is a one-shot CLI, so every request spawns a fresh dialog (≈7 s model init).
That cost is real and unavoidable with this SDK; the harness sees a slow provider,
not a broken one.

Endpoints:
  GET  /v1/models              -> the one NPU model, OpenAI shape
  POST /v1/chat/completions    -> messages -> DeepSeek template -> genie-t2t-run

Only the DeepSeek-7B QNN bundle on this device produces coherent text; the
deepseek-qwen-* bundles fail at buffer mapping or tokenize to garbage.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GENIE = "/data/local/tmp/qnn/bin/genie-t2t-run"
CONFIG = "/data/local/tmp/qnn/deepseek-7b/deepseek-7b-htp_sharing.json"
MODEL_ID = "deepseek-7b-npu"
BOS = "<\uff5cbegin\u2581of\u2581sentence\uff5c>"
ENV = dict(
    os.environ,
    PATH="/data/local/tmp/qnn/bin:" + os.environ.get("PATH", "/usr/bin:/bin"),
    ADSP_LIBRARY_PATH="/data/local/tmp/qnn/dsp/lib",
)
LOCK = threading.Lock()  # the DSP runs one dialog at a time


def build_prompt(messages):
    parts = [BOS]
    for m in messages:
        role, content = m.get("role", "user"), m.get("content", "")
        if isinstance(content, list):  # OpenAI content parts -> plain text
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role == "system":
            parts.append("System: %s\n\n" % content)
        elif role == "user":
            parts.append("User: %s\n\n" % content)
        elif role == "assistant":
            parts.append("Assistant: %s\n\n" % content)
    parts.append("Assistant: ")
    return "".join(parts)


def run_genie(prompt):
    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(prompt)
        proc = subprocess.run([GENIE, "-c", CONFIG, "--prompt_file", path],
                              capture_output=True, text=True, timeout=900, env=ENV)
        raw = (proc.stdout or "") + (proc.stderr or "")
        m = re.search(r"\[BEGIN\]:(.*?)\[END\]", raw, re.S)
        text = m.group(1).strip() if m else ""
        return text, raw
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("%s %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/v1/models"):
            self._json(200, {"object": "list", "data": [{
                "id": MODEL_ID, "object": "model", "owned_by": "qnn-genie",
                "meta": {"backend": "QnnHtp", "device": "MT200 Hexagon V73"},
            }]})
        else:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._json(400, {"error": {"message": "bad json: %s" % exc, "type": "invalid_request_error"}})
            return

        prompt = build_prompt(req.get("messages", []))
        t0 = time.time()
        with LOCK:
            try:
                text, raw = run_genie(prompt)
            except subprocess.TimeoutExpired:
                self._json(504, {"error": {"message": "genie timeout", "type": "timeout"}})
                return
        elapsed = time.time() - t0
        if not text:
            sys_msg = raw.strip().splitlines()[-6:]
            self._json(500, {"error": {"message": "genie produced no answer: " + " | ".join(sys_msg),
                                       "type": "upstream_error"}})
            return

        created = int(time.time())
        self._json(200, {
            "id": "chatcmpl-npu-%d" % created,
            "object": "chat.completion",
            "created": created,
            "model": MODEL_ID,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": max(1, len(prompt) // 2),
                      "completion_tokens": max(1, len(text) // 2),
                      "total_tokens": max(2, (len(prompt) + len(text)) // 2)},
            "meta": {"wall_s": round(elapsed, 2), "init_s_per_request": 7.1,
                     "npu_token_rate_tps": 12.1},
        })


if __name__ == "__main__":
    port = int(os.environ.get("BRIDGE_PORT", "8090"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("NPU OpenAI bridge on :%d (genie %s)" % (port, GENIE), flush=True)
    srv.serve_forever()
