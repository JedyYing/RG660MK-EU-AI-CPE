#!/usr/bin/env python3
"""OpenAI-compatible HTTP bridge for the MT200 QNN Genie NPU model."""

import json
import math
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
BOS = "<｜begin▁of▁sentence｜>"
PROMPT_TOKEN_BUDGET = 700
ENV = dict(
    os.environ,
    PATH="/data/local/tmp/qnn/bin:" + os.environ.get("PATH", "/usr/bin:/bin"),
    ADSP_LIBRARY_PATH="/data/local/tmp/qnn/dsp/lib",
)
LOCK = threading.Lock()


def estimate_tokens(text):
    """Conservatively estimate tokens without adding a tokenizer dependency."""
    tokens = 0
    ascii_run = 0
    for char in text:
        if char.isascii() and (char.isalnum() or char.isspace()):
            ascii_run += 1
            continue
        if ascii_run:
            tokens += math.ceil(ascii_run / 4)
            ascii_run = 0
        tokens += 1
    if ascii_run:
        tokens += math.ceil(ascii_run / 4)
    return max(1, tokens)


def render_message(message):
    role = message.get("role", "user")
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if role == "system":
        return role, f"System: {content}\n\n"
    if role == "user":
        return role, f"User: {content}\n\n"
    if role == "assistant":
        return role, f"Assistant: {content}\n\n"
    return role, ""


def trim_messages(messages):
    """Reset over-budget history while retaining the system prompt and latest input."""
    rendered = [render_message(message) for message in messages]
    system_parts = [part for role, part in rendered if role == "system" and part]
    history_parts = [part for role, part in rendered if role != "system" and part]
    system_prompt = BOS + "".join(system_parts)
    full_prompt = system_prompt + "".join(history_parts) + "Assistant: "

    if estimate_tokens(full_prompt) <= PROMPT_TOKEN_BUDGET or not history_parts:
        return full_prompt, 0

    prompt = system_prompt + history_parts[-1] + "Assistant: "
    return prompt, len(history_parts) - 1


def run_genie(prompt):
    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(prompt)
        process = subprocess.run(
            [GENIE, "-c", CONFIG, "--prompt_file", path],
            capture_output=True,
            text=True,
            timeout=900,
            env=ENV,
        )
        raw = (process.stdout or "") + (process.stderr or "")
        match = re.search(r"\[BEGIN\]:(.*?)\[END\]", raw, re.S)
        return (match.group(1).strip() if match else ""), raw
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"{time.strftime('%H:%M:%S')} {fmt % args}", flush=True)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, chunks):
        body = "".join(
            f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks
        ) + "data: [DONE]\n\n"
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()
        self.close_connection = True

    def do_GET(self):
        if self.path.rstrip("/").endswith("/v1/models"):
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MODEL_ID,
                            "object": "model",
                            "owned_by": "qnn-genie",
                            "meta": {
                                "backend": "QnnHtp",
                                "device": "MT200 Hexagon V73",
                                "context_window": 1024,
                                "quantization": "W4A16",
                            },
                        }
                    ],
                },
            )
        else:
            self._json(
                404,
                {"error": {"message": "not found", "type": "invalid_request_error"}},
            )

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(
                404,
                {"error": {"message": "not found", "type": "invalid_request_error"}},
            )
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._json(
                400,
                {"error": {"message": f"bad json: {exc}", "type": "invalid_request_error"}},
            )
            return

        prompt, dropped_messages = trim_messages(request.get("messages", []))
        started = time.time()
        with LOCK:
            try:
                text, raw = run_genie(prompt)
            except subprocess.TimeoutExpired:
                self._json(
                    504,
                    {"error": {"message": "genie timeout", "type": "timeout"}},
                )
                return
        elapsed = time.time() - started
        print(
            f"request prompt_chars={len(prompt)} prompt_tokens={estimate_tokens(prompt)} "
            f"dropped_messages={dropped_messages} response_chars={len(text)} "
            f"wall_s={elapsed:.2f} stream={bool(request.get('stream'))}",
            flush=True,
        )

        if not text:
            diagnostic = raw.strip().splitlines()[-6:]
            self._json(
                500,
                {
                    "error": {
                        "message": "genie produced no answer: " + " | ".join(diagnostic),
                        "type": "upstream_error",
                    }
                },
            )
            return

        created = int(time.time())
        completion_id = f"chatcmpl-npu-{created}"
        prompt_tokens = estimate_tokens(prompt)
        completion_tokens = estimate_tokens(text)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        if request.get("stream"):
            self._sse(
                [
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": MODEL_ID,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": text},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": MODEL_ID,
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": "stop"}
                        ],
                        "usage": usage,
                    },
                ]
            )
            return

        self._json(
            200,
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                    }
                ],
                "usage": usage,
                "meta": {
                    "wall_s": round(elapsed, 2),
                    "init_s_per_request": 7.1,
                    "npu_token_rate_tps": 12.1,
                    "dropped_messages": dropped_messages,
                },
            },
        )


if __name__ == "__main__":
    bridge_host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    bridge_port = int(os.environ.get("BRIDGE_PORT", "8090"))
    server = ThreadingHTTPServer((bridge_host, bridge_port), Handler)
    print(f"NPU OpenAI bridge on {bridge_host}:{bridge_port} (genie {GENIE})", flush=True)
    server.serve_forever()
