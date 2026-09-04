#!/usr/bin/env python3
"""Restricted Hermes-facing wrapper for the RG660MK-EU Matter controller.

The wrapper invokes the official Matter SDK chip-tool without a shell, serializes
access to its fabric storage, and exposes only a small allowlist of operations.
It does not configure CPE networking, Bluetooth, Thread, or firewall rules.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = "/data/ai_cpe/demo/matter/config/controller.json"
DEFAULTS: dict[str, Any] = {
    "binary": "/data/ai_cpe/demo/matter/bin/chip-tool",
    "storage_directory": "/data/ai_cpe/demo/matter/credentials",
    "network_interface": "br-lan",
    "default_endpoint": 1,
    "command_timeout_seconds": 120,
    "commissioning_timeout_seconds": 180,
    "max_output_bytes": 131072,
}


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def parse_payload(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("payload must be a JSON object")
    return value


def load_config() -> dict[str, Any]:
    path = Path(os.environ.get("MATTER_CONTROLLER_CONFIG", DEFAULT_CONFIG))
    config = dict(DEFAULTS)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValueError("Matter controller config must be a JSON object")
    config.update(loaded)

    binary = Path(str(config["binary"]))
    storage = Path(str(config["storage_directory"]))
    if not binary.is_absolute() or not storage.is_absolute():
        raise ValueError("binary and storage_directory must be absolute paths")
    if str(storage) in {"/", "/tmp"}:
        raise ValueError("storage_directory must be a private persistent directory")
    config["default_endpoint"] = bounded_int(config["default_endpoint"], "default_endpoint", 0, 65535)
    config["command_timeout_seconds"] = bounded_int(
        config["command_timeout_seconds"], "command_timeout_seconds", 1, 600
    )
    config["commissioning_timeout_seconds"] = bounded_int(
        config["commissioning_timeout_seconds"], "commissioning_timeout_seconds", 1, 900
    )
    config["max_output_bytes"] = bounded_int(config["max_output_bytes"], "max_output_bytes", 4096, 1048576)
    return config


def bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return result


def node_id(payload: dict[str, Any]) -> int:
    return bounded_int(payload.get("node_id"), "node_id", 1, 0xFFFFFFFFFFFFFFFE)


def endpoint_id(payload: dict[str, Any], config: dict[str, Any]) -> int:
    return bounded_int(payload.get("endpoint", config["default_endpoint"]), "endpoint", 0, 65535)


def redact(text: str, secrets: list[str], maximum: int) -> tuple[str, bool]:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<REDACTED>")
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= maximum:
        return text, False
    clipped = encoded[:maximum].decode("utf-8", errors="replace")
    return clipped + "\n<OUTPUT_TRUNCATED>", True


def ensure_storage(config: dict[str, Any]) -> Path:
    storage = Path(str(config["storage_directory"]))
    storage.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(storage, 0o700)
    return storage


def execute_chip(
    config: dict[str, Any],
    arguments: list[str],
    timeout: int,
    secrets: list[str] | None = None,
) -> dict[str, Any]:
    binary = Path(str(config["binary"]))
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return {
            "ok": False,
            "error": {"code": "MATTER_BINARY_UNAVAILABLE", "message": f"chip-tool is not executable: {binary}"},
        }

    storage = ensure_storage(config)
    lock_path = storage / "controller.lock"
    command = [str(binary), *arguments, "--storage-directory", str(storage)]
    environment = os.environ.copy()
    environment.update({"HOME": str(storage), "TMPDIR": str(storage), "LC_ALL": "C"})
    started = time.monotonic()

    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = process.communicate()
                safe_stdout, stdout_truncated = redact(stdout, secrets or [], int(config["max_output_bytes"]))
                safe_stderr, stderr_truncated = redact(stderr, secrets or [], int(config["max_output_bytes"]))
                return {
                    "ok": False,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "stdout": safe_stdout,
                    "stderr": safe_stderr,
                    "output_truncated": stdout_truncated or stderr_truncated,
                    "error": {"code": "MATTER_TIMEOUT", "message": f"chip-tool exceeded {timeout} seconds"},
                }
        except OSError as exc:
            return {"ok": False, "error": {"code": "MATTER_EXEC_FAILED", "message": str(exc)}}

    safe_stdout, stdout_truncated = redact(stdout, secrets or [], int(config["max_output_bytes"]))
    safe_stderr, stderr_truncated = redact(stderr, secrets or [], int(config["max_output_bytes"]))
    result: dict[str, Any] = {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "stdout": safe_stdout,
        "stderr": safe_stderr,
        "output_truncated": stdout_truncated or stderr_truncated,
    }
    if process.returncode != 0:
        result["error"] = {"code": "MATTER_COMMAND_FAILED", "message": "chip-tool reported command failure"}
    return result


def status(config: dict[str, Any]) -> dict[str, Any]:
    interface = str(config.get("network_interface", "br-lan"))
    interface_path = Path("/sys/class/net") / interface
    binary = Path(str(config["binary"]))
    storage = Path(str(config["storage_directory"]))
    details: dict[str, Any] = {
        "binary": str(binary),
        "binary_executable": binary.is_file() and os.access(binary, os.X_OK),
        "storage_directory": str(storage),
        "network_interface": interface,
        "interface_present": interface_path.exists(),
    }
    try:
        details["interface_state"] = (interface_path / "operstate").read_text(encoding="utf-8").strip()
    except OSError:
        details["interface_state"] = "unavailable"
    if not details["binary_executable"]:
        return {"ok": False, "result": details, "error": {"code": "MATTER_BINARY_UNAVAILABLE", "message": "deploy chip-tool first"}}

    probe = execute_chip(config, [], 10)
    # chip-tool intentionally returns a usage error without a command. Reaching
    # its command parser still proves that the target executable can start.
    runtime_ok = probe.get("error", {}).get("code") not in {"MATTER_EXEC_FAILED", "MATTER_TIMEOUT"}
    details["runtime_compatible"] = runtime_ok
    details["probe_returncode"] = probe.get("returncode")
    if not runtime_ok:
        return {"ok": False, "result": details, "error": probe.get("error")}
    return {"ok": bool(details["interface_present"]), "result": details}


def dispatch(action: str, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if action == "status":
        return status(config)

    if action == "commission_onnetwork":
        remote_node = node_id(payload)
        setup_pin = bounded_int(payload.get("setup_pin"), "setup_pin", 1, 134217727)
        discriminator_value = payload.get("discriminator")
        if discriminator_value is None:
            arguments = ["pairing", "onnetwork", str(remote_node), str(setup_pin)]
        else:
            discriminator = bounded_int(discriminator_value, "discriminator", 0, 4095)
            arguments = ["pairing", "onnetwork-long", str(remote_node), str(setup_pin), str(discriminator)]
        return execute_chip(
            config,
            arguments,
            int(config["commissioning_timeout_seconds"]),
            secrets=[str(setup_pin)],
        )

    remote_node = node_id(payload)
    if action == "unpair":
        arguments = ["pairing", "unpair", str(remote_node)]
    else:
        endpoint = endpoint_id(payload, config)
        command_name = {
            "on": "on",
            "off": "off",
            "toggle": "toggle",
        }.get(action)
        if command_name:
            arguments = ["onoff", command_name, str(remote_node), str(endpoint)]
        elif action == "read_onoff":
            arguments = ["onoff", "read", "on-off", str(remote_node), str(endpoint)]
        else:
            raise ValueError(f"unsupported action: {action}")
    return execute_chip(config, arguments, int(config["command_timeout_seconds"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Control allowlisted Matter functions from Hermes")
    parser.add_argument(
        "action",
        choices=["status", "commission_onnetwork", "on", "off", "toggle", "read_onoff", "unpair"],
    )
    parser.add_argument("--payload", default="{}", help="JSON object containing node_id and action parameters")
    args = parser.parse_args()
    try:
        result = dispatch(args.action, parse_payload(args.payload), load_config())
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        result = {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
    emit(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
