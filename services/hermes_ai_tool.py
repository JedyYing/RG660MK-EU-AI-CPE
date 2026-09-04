#!/usr/bin/env python3
"""Hermes-facing client for the local AI Service contract.

This client has no third-party dependencies and never loads accelerator drivers.
It is safe to install before the NPU runtime, but calls remain unavailable until
an AI Service is listening on the configured loopback endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

ENDPOINTS = {
    "health": ("GET", "/health"),
    "detect": ("POST", "/vision/detect"),
    "face": ("POST", "/vision/face"),
    "posture": ("POST", "/vision/posture"),
    "asr": ("POST", "/audio/asr"),
    "kws": ("POST", "/audio/kws"),
    "tts": ("POST", "/tts"),
    "metrics": ("GET", "/metrics"),
}


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AI_SERVICE_URL must be an absolute HTTP(S) URL")
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not loopback:
        if os.environ.get("HERMES_AI_ALLOW_REMOTE") != "1":
            raise ValueError("remote AI Service is disabled; set HERMES_AI_ALLOW_REMOTE=1 explicitly")
        if parsed.scheme != "https":
            raise ValueError("remote AI Service requires HTTPS")
    return value.rstrip("/")


def request_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token_path = os.environ.get("AI_SERVICE_TOKEN_FILE", "/data/ai_cpe/demo/data/api-token")
    try:
        with open(token_path, "r", encoding="utf-8") as stream:
            token = stream.read().strip()
    except FileNotFoundError:
        token = ""
    if token:
        headers["X-AI-Service-Token"] = token
    return headers


def parse_payload(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    return value


def request(action: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    method, path = ENDPOINTS[action]
    base_url = validate_base_url(os.environ.get("AI_SERVICE_URL", "http://127.0.0.1:8765"))
    payload.setdefault("request_id", str(uuid.uuid4()))
    request_id = str(payload["request_id"])
    payload["request_id"] = request_id
    payload.setdefault("timeout_ms", int(timeout_s * 1000))
    body = None if method == "GET" else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url + path,
        data=body,
        method=method,
        headers=request_headers(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read(2 * 1024 * 1024)
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("AI Service response is not a JSON object")
            if not isinstance(result.get("ok"), bool):
                raise ValueError("AI Service response must contain boolean field 'ok'")
            result.setdefault("request_id", request_id)
            return result
    except urllib.error.HTTPError as exc:
        detail = exc.read(8192).decode("utf-8", errors="replace")
        try:
            result = json.loads(detail)
            if isinstance(result, dict) and isinstance(result.get("ok"), bool):
                result.setdefault("request_id", request_id)
                return result
        except json.JSONDecodeError:
            pass
        return {
            "ok": False,
            "request_id": request_id,
            "error": {"code": f"HTTP_{exc.code}", "message": detail or str(exc)},
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "request_id": request_id,
            "error": {"code": "AI_SERVICE_UNAVAILABLE", "message": str(exc)},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the local AI Service from Hermes")
    parser.add_argument("action", choices=sorted(ENDPOINTS))
    parser.add_argument("--payload", default="{}", help="JSON object passed to the endpoint")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    try:
        if not 0.1 <= args.timeout <= 300:
            raise ValueError("timeout must be between 0.1 and 300 seconds")
        result = request(args.action, parse_payload(args.payload), args.timeout)
    except (ValueError, json.JSONDecodeError) as exc:
        emit({"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}})
        return 2
    emit(result)
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
