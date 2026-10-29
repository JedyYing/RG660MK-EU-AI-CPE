#!/usr/bin/env python3
"""Run fixed llm_compare cases against an OpenAI-compatible endpoint.

Raw records are appended as JSONL after every request. Existing records can be
skipped with --resume; failures are recorded and do not stop later cases.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "cases_100.jsonl"
DEFAULT_MODELS = HERE / "models.yaml"
DEFAULT_OUT = HERE / "raw_results.jsonl"
THERMAL_WAIT_C = 85.0
THERMAL_ABORT_C = 90.0


class ThermalAbort(RuntimeError):
    """Raised when the monitored host reaches the hard temperature limit."""


def load_cases(spec: str) -> list[dict[str, Any]]:
    candidate = Path(spec)
    source = candidate if candidate.is_file() else DEFAULT_CASES
    cases = []
    with source.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_no}: invalid JSON: {exc}") from exc
            cases.append(case)
    if source == DEFAULT_CASES and spec != str(DEFAULT_CASES):
        wanted = {item.strip() for item in spec.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted or case["id"][0] in wanted]
        missing = sorted(item for item in wanted if not any(
            case["id"] == item or case["id"][0] == item for case in cases
        ))
        if missing:
            raise ValueError(f"unknown case selectors: {', '.join(missing)}")
    if not cases:
        raise ValueError("no cases selected")
    return cases


def load_model(name: str) -> dict[str, Any]:
    with DEFAULT_MODELS.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    models = document.get("models", {})
    if name not in models:
        raise ValueError(f"unknown model {name!r}; choose from {', '.join(models)}")
    config = dict(models[name])
    key_env = config.get("keyEnv")
    if key_env and not os.environ.get(key_env):
        raise ValueError(f"model {name!r} requires environment variable {key_env}; refusing to start")
    if not config.get("enabled", True):
        raise ValueError(f"model {name!r} is disabled in {DEFAULT_MODELS}; refusing to start")
    config["name"] = name
    return config


def read_completed(path: Path) -> set[tuple[str, str, int]]:
    completed: set[tuple[str, str, int]] = set()
    if not path.exists():
        return completed
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                completed.add((str(row["model"]), str(row["case"]), int(row["repetition"])))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return completed


def read_temperature(host: str) -> float:
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
               "cat /sys/class/thermal/thermal_zone*/temp"]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"temperature check failed for {host}: {detail or 'ssh failed'}")
    values = []
    for token in proc.stdout.split():
        try:
            value = float(token)
        except ValueError:
            continue
        values.append(value / 1000.0 if value > 1000 else value)
    if not values:
        raise RuntimeError(f"temperature check returned no numeric thermal zones for {host}")
    return max(values)


def thermal_gate(host: str | None) -> float | None:
    if not host:
        return None
    while True:
        temperature = read_temperature(host)
        if temperature >= THERMAL_ABORT_C:
            raise ThermalAbort(f"temperature {temperature:.1f}C reached {THERMAL_ABORT_C:.0f}C abort limit")
        if temperature < THERMAL_WAIT_C:
            return temperature
        print(f"temperature {temperature:.1f}C >= {THERMAL_WAIT_C:.0f}C; waiting 15s", file=sys.stderr)
        time.sleep(15)


def make_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "text/event-stream"}
    key_env = config.get("keyEnv")
    if key_env:
        headers["Authorization"] = "Bearer " + os.environ[key_env]
    return headers


def extract_json_response(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    choice = choices[0]
    message = choice.get("message") or {}
    return str(message.get("content") or choice.get("text") or "")


def request_once(config: dict[str, Any], payload: dict[str, Any]) -> tuple[dict[str, Any], float, float | None]:
    url = str(config["endpoint"]).rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=make_headers(config),
        method="POST",
    )
    started = time.monotonic()
    first_token_at = None
    with urllib.request.urlopen(request, timeout=600) as response:
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/event-stream" not in content_type:
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            wall = time.monotonic() - started
            return {"text": extract_json_response(data), "raw": data}, wall, wall

        chunks = []
        usage: dict[str, Any] = {}
        finish_reason = None
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if data_text == "[DONE]":
                break
            try:
                event = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            if event.get("usage"):
                usage = event["usage"]
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content is None:
                content = choice.get("text")
            if content:
                if first_token_at is None:
                    first_token_at = time.monotonic()
                chunks.append(str(content))
        wall = time.monotonic() - started
        ttft = first_token_at - started if first_token_at is not None else None
        return {
            "text": "".join(chunks),
            "finish_reason": finish_reason,
            "usage": usage,
            "stream": True,
        }, wall, ttft


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def error_detail(exc: BaseException) -> dict[str, Any]:
    detail: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail["http_status"] = exc.code
            detail["body"] = exc.read().decode("utf-8", errors="replace")[:4096]
        except OSError:
            pass
    return detail


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fixed MT200 LLM comparison cases")
    parser.add_argument("--model", required=True, help="model key from models.yaml")
    parser.add_argument("--cases", default=str(DEFAULT_CASES),
                        help="JSONL path, comma-separated IDs, or category letters")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="append-only raw JSONL output")
    parser.add_argument("--resume", action="store_true", help="skip model/case/repetition keys already recorded")
    parser.add_argument("--limit", type=int, help="maximum selected cases to run")
    parser.add_argument("--temperature-host", help="SSH host used for the 85C wait / 90C abort gate")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_model(args.model)
        cases = load_cases(args.cases)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.limit is not None:
        if args.limit < 0:
            print("configuration error: --limit must be non-negative", file=sys.stderr)
            return 2
        cases = cases[:args.limit]

    out = Path(args.out)
    if out.exists() and not args.resume:
        print(f"output exists: {out}; use --resume or choose another --out", file=sys.stderr)
        return 2
    completed = read_completed(out) if args.resume else set()
    failures = 0
    for case in cases:
        repetitions = int(case.get("repetitions", 1))
        for repetition in range(1, repetitions + 1):
            key = (args.model, str(case["id"]), repetition)
            if key in completed:
                continue
            requested_max = int(case.get("max_tokens", 256))
            actual_max = min(requested_max, int(config.get("maxTokens", requested_max)))
            payload = {
                "model": config["model"],
                "messages": [{"role": "user", "content": case["prompt"]}],
                "temperature": 0,
                "max_tokens": actual_max,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            record: dict[str, Any] = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": args.model,
                "model_id": config["model"],
                "case": case["id"],
                "dimension": case["dimension"],
                "repetition": repetition,
                "temperature": 0,
                "max_tokens": actual_max,
                "requested_max_tokens": requested_max,
                "request": payload,
                "response": None,
                "error": None,
                "wall_s": None,
                "ttft_s": None,
                "usage": {},
                "temperature_c": None,
            }
            try:
                record["temperature_c"] = thermal_gate(args.temperature_host)
                response, wall, ttft = request_once(config, payload)
                record["response"] = response
                record["wall_s"] = round(wall, 6)
                record["ttft_s"] = round(ttft, 6) if ttft is not None else None
                record["usage"] = response.get("usage") or response.get("raw", {}).get("usage") or {}
            except ThermalAbort as exc:
                record["error"] = error_detail(exc)
                append_record(out, record)
                print(json.dumps(record, ensure_ascii=False), flush=True)
                print(f"thermal abort: {exc}", file=sys.stderr)
                return 3
            except Exception as exc:  # one failed request must not stop the run
                failures += 1
                record["error"] = error_detail(exc)
            append_record(out, record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
