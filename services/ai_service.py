#!/usr/bin/env python3
"""Constrained loopback vision service for RG660MK-EU.

The service intentionally contains no accelerator SDK dependency. A statically
configured runner process owns HailoRT/model integration and exchanges one JSON
object over stdin/stdout. This keeps Hermes and the HTTP process independent of
USB drivers and vendor ABI changes.
"""

from __future__ import annotations

import argparse
import fcntl
import hmac
import json
import math
import os
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("configuration root must be a JSON object")
    return value


def require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ServiceError("INVALID_ARGUMENT", f"{name} must be an object")
    return value


def normalize(vector: Any) -> list[float]:
    if not isinstance(vector, list) or not vector:
        raise ServiceError("RUNTIME_UNAVAILABLE", "runner returned an invalid face embedding", 503)
    try:
        values = [float(item) for item in vector]
    except (TypeError, ValueError) as exc:
        raise ServiceError("RUNTIME_UNAVAILABLE", "face embedding contains a non-number", 503) from exc
    if not all(math.isfinite(item) for item in values):
        raise ServiceError("RUNTIME_UNAVAILABLE", "face embedding contains a non-finite value", 503)
    magnitude = math.sqrt(sum(item * item for item in values))
    if magnitude <= 1e-12:
        raise ServiceError("RUNTIME_UNAVAILABLE", "face embedding has zero magnitude", 503)
    return [item / magnitude for item in values]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    return sum(a * b for a, b in zip(left, right))


def validate_media_path(path_value: Any, media_roots: list[str], max_bytes: int) -> str:
    if not isinstance(path_value, str) or not os.path.isabs(path_value):
        raise ServiceError("INVALID_ARGUMENT", "input.path must be an absolute path")
    try:
        resolved = os.path.realpath(path_value)
        allowed = any(os.path.commonpath((resolved, root)) == root for root in media_roots)
    except ValueError as exc:
        raise ServiceError("INVALID_ARGUMENT", "input.path is outside configured media roots") from exc
    if not allowed:
        raise ServiceError("INVALID_ARGUMENT", "input.path is outside configured media roots")
    try:
        stat = os.stat(resolved)
    except OSError as exc:
        raise ServiceError("INVALID_ARGUMENT", f"input file is unavailable: {exc}") from exc
    if not os.path.isfile(resolved):
        raise ServiceError("INVALID_ARGUMENT", "input.path must reference a regular file")
    if stat.st_size <= 0 or stat.st_size > max_bytes:
        raise ServiceError("RESOURCE_LIMIT", f"input file must be between 1 and {max_bytes} bytes", 413)
    return resolved


def stage_media_path(source: str, staging_dir: str, max_bytes: int) -> str:
    """Copy a validated input into a private immutable path before invoking the runner."""
    os.makedirs(staging_dir, mode=0o700, exist_ok=True)
    os.chmod(staging_dir, 0o700)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, flags)
    except OSError as exc:
        raise ServiceError("INVALID_ARGUMENT", f"cannot securely open input file: {exc}") from exc
    target_fd = -1
    target_path = ""
    try:
        source_stat = os.fstat(source_fd)
        if (source_stat.st_mode & 0o170000) != 0o100000 or not 0 < source_stat.st_size <= max_bytes:
            raise ServiceError("RESOURCE_LIMIT", "input changed after validation", 413)
        suffix = os.path.splitext(source)[1][:16]
        target_fd, target_path = tempfile.mkstemp(prefix="inference-", suffix=suffix, dir=staging_dir)
        os.fchmod(target_fd, 0o400)
        copied = 0
        while True:
            chunk = os.read(source_fd, min(65536, max_bytes - copied + 1))
            if not chunk:
                break
            copied += len(chunk)
            if copied > max_bytes:
                raise ServiceError("RESOURCE_LIMIT", "input exceeded size limit while staging", 413)
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
        os.close(target_fd)
        target_fd = -1
        return target_path
    except BaseException:
        if target_fd >= 0:
            os.close(target_fd)
        if target_path:
            try:
                os.unlink(target_path)
            except OSError:
                pass
        raise
    finally:
        os.close(source_fd)


class Gallery:
    """Thread-safe, atomic embedding store. Raw face images are never persisted."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.path = str(config.get("gallery_path", ""))
        self.threshold = float(config.get("similarity_threshold", 0.52))
        self.max_identities = int(config.get("max_identities", 100))
        self.max_templates = int(config.get("max_templates_per_identity", 5))
        self._lock = threading.RLock()
        if not self.path or not os.path.isabs(self.path):
            raise ValueError("face_recognition.gallery_path must be absolute")
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("face_recognition.similarity_threshold must be between 0 and 1")
        self.lock_path = self.path + ".lock"

    @contextmanager
    def _file_lock(self, exclusive: bool) -> Iterator[None]:
        parent = os.path.dirname(self.path)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_unlocked(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {"version": 1, "dimensions": None, "identities": {}}
        with open(self.path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ServiceError("RUNTIME_UNAVAILABLE", "face gallery has an unsupported format", 503)
        identities = value.get("identities")
        if not isinstance(identities, dict):
            raise ServiceError("RUNTIME_UNAVAILABLE", "face gallery identities are invalid", 503)
        return value

    def count(self) -> int:
        with self._lock, self._file_lock(False):
            return len(self._read_unlocked()["identities"])

    def names(self) -> list[str]:
        with self._lock, self._file_lock(False):
            return sorted(self._read_unlocked()["identities"])

    def match(self, embedding: Any) -> tuple[str | None, float]:
        query = normalize(embedding)
        with self._lock, self._file_lock(False):
            data = self._read_unlocked()
        dimensions = data.get("dimensions")
        if dimensions is not None and dimensions != len(query):
            raise ServiceError("RUNTIME_UNAVAILABLE", "face embedding dimension does not match gallery", 503)
        best_name: str | None = None
        best_score = -1.0
        for name, templates in data["identities"].items():
            if not isinstance(templates, list):
                continue
            for template in templates:
                try:
                    score = cosine(query, normalize(template))
                except ServiceError:
                    continue
                if score > best_score:
                    best_name, best_score = str(name), score
        if best_score < self.threshold:
            return None, max(-1.0, best_score)
        return best_name, best_score

    def add(self, subject_id: str, embedding: Any) -> int:
        if not subject_id or len(subject_id) > 64 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in subject_id):
            raise ServiceError("INVALID_ARGUMENT", "subject_id must match [A-Za-z0-9_-]{1,64}")
        vector = normalize(embedding)
        with self._lock, self._file_lock(True):
            data = self._read_unlocked()
            identities = data["identities"]
            if subject_id not in identities and len(identities) >= self.max_identities:
                raise ServiceError("RESOURCE_LIMIT", "face gallery identity limit reached")
            dimensions = data.get("dimensions")
            if dimensions is not None and dimensions != len(vector):
                raise ServiceError("INVALID_ARGUMENT", "embedding dimension does not match gallery")
            data["dimensions"] = len(vector)
            templates = identities.setdefault(subject_id, [])
            if not isinstance(templates, list):
                raise ServiceError("RUNTIME_UNAVAILABLE", "face gallery subject is invalid", 503)
            templates.append(vector)
            del templates[:-self.max_templates]
            self._write_unlocked(data)
            return len(templates)

    def remove(self, subject_id: str) -> bool:
        with self._lock, self._file_lock(True):
            data = self._read_unlocked()
            existed = data["identities"].pop(subject_id, None) is not None
            if existed:
                if not data["identities"]:
                    data["dimensions"] = None
                self._write_unlocked(data)
            return existed

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        parent = os.path.dirname(self.path)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".gallery-", suffix=".json", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


class CommandRunner:
    REQUIRED_MODELS = {
        "detect": ("detect",),
        "face": ("face_detect", "face_embed"),
        "pose": ("pose",),
    }

    def __init__(self, config: dict[str, Any], models: dict[str, Any]) -> None:
        self.config = config
        self.models = models
        command = config.get("command", [])
        if not isinstance(command, list) or not all(isinstance(item, str) and item for item in command):
            raise ValueError("runtime.command must be a non-empty string array")
        self.command = command
        self.max_output_bytes = int(config.get("max_output_bytes", 2 * 1024 * 1024))

    @property
    def enabled(self) -> bool:
        return self.config.get("enabled") is True

    def model_paths(self, operation: str) -> dict[str, str]:
        selected: dict[str, str] = {}
        for name in self.REQUIRED_MODELS[operation]:
            entry = self.models.get(name)
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not path:
                raise ServiceError("MODEL_UNAVAILABLE", f"model {name} is not configured", 503)
            if not os.path.isfile(path):
                raise ServiceError("MODEL_UNAVAILABLE", f"model {name} is unavailable", 503)
            selected[name] = path
        return selected

    def health(self) -> dict[str, Any]:
        executable = self.command[0] if self.command else ""
        executable_ok = bool(executable) and os.path.isfile(executable) and os.access(executable, os.X_OK)
        model_state: dict[str, bool] = {}
        for name, entry in self.models.items():
            path = entry.get("path") if isinstance(entry, dict) else None
            model_state[name] = isinstance(path, str) and os.path.isfile(path) and os.access(path, os.R_OK)
        required = {name for names in self.REQUIRED_MODELS.values() for name in names}
        models_ready = all(model_state.get(name, False) for name in required)
        return {
            "backend": self.config.get("backend", "command"),
            "enabled": self.enabled,
            "runner_available": executable_ok,
            "models": model_state,
            "ready": self.enabled and executable_ok and models_ready,
            "reason": self.config.get("reason") if not self.enabled else None,
        }

    @staticmethod
    def _terminate_group(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(0.2)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

    def _execute(self, request: dict[str, Any], timeout_s: float) -> tuple[int, str, str]:
        deadline = time.monotonic() + timeout_s
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise ServiceError("RUNTIME_UNAVAILABLE", f"cannot execute vision runner: {exc}", 503) from exc
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        input_buffer = memoryview(json.dumps(request, separators=(",", ":")).encode("utf-8"))
        selector = selectors.DefaultSelector()
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_group(process)
                    raise ServiceError("TIMEOUT", "vision runner exceeded request timeout", 504)
                for key, _mask in selector.select(min(remaining, 0.1)):
                    if key.data == "stdin":
                        try:
                            written = os.write(key.fileobj.fileno(), input_buffer)
                        except (BrokenPipeError, OSError) as exc:
                            self._terminate_group(process)
                            raise ServiceError("RUNTIME_UNAVAILABLE", "vision runner closed its input unexpectedly", 503) from exc
                        input_buffer = input_buffer[written:]
                        if not input_buffer:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    buffers[key.data].extend(chunk)
                    if len(buffers["stdout"]) + len(buffers["stderr"]) > self.max_output_bytes:
                        self._terminate_group(process)
                        raise ServiceError("RESOURCE_LIMIT", "vision runner output exceeded the configured limit", 503)
            return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            self._terminate_group(process)
            raise ServiceError("TIMEOUT", "vision runner did not exit after closing output", 504) from exc
        finally:
            selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
        return (
            return_code,
            buffers["stdout"].decode("utf-8", errors="replace"),
            buffers["stderr"].decode("utf-8", errors="replace"),
        )

    def infer(self, operation: str, input_path: str, timeout_s: float, options: dict[str, Any]) -> dict[str, Any]:
        if operation not in self.REQUIRED_MODELS:
            raise ServiceError("INVALID_ARGUMENT", f"unsupported runner operation: {operation}")
        if not self.enabled:
            raise ServiceError("RUNTIME_UNAVAILABLE", str(self.config.get("reason", "runtime is disabled")), 503)
        health = self.health()
        if not health["runner_available"]:
            raise ServiceError("RUNTIME_UNAVAILABLE", "configured vision runner is not executable", 503)
        request = {
            "version": 1,
            "operation": operation,
            "input": {"path": input_path},
            "models": self.model_paths(operation),
            "options": options,
        }
        return_code, stdout, _stderr = self._execute(request, timeout_s)
        if return_code != 0:
            raise ServiceError("RUNTIME_UNAVAILABLE", f"vision runner exited with code {return_code}", 503)
        try:
            response = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ServiceError("RUNTIME_UNAVAILABLE", "vision runner returned invalid JSON", 503) from exc
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise ServiceError("RUNTIME_UNAVAILABLE", "vision runner response must contain boolean ok", 503)
        if not response["ok"]:
            error = response.get("error", {})
            code = str(error.get("code", "RUNTIME_UNAVAILABLE")) if isinstance(error, dict) else "RUNTIME_UNAVAILABLE"
            message = str(error.get("message", "vision runner failed")) if isinstance(error, dict) else "vision runner failed"
            raise ServiceError(code, message, 503)
        result = response.get("result")
        if not isinstance(result, dict):
            raise ServiceError("RUNTIME_UNAVAILABLE", "vision runner result must be an object", 503)
        return result


class PostureClassifier:
    # COCO17: shoulder, hip, knee, ankle pairs.
    SIDES = ((5, 11, 13, 15), (6, 12, 14, 16))

    def __init__(self, config: dict[str, Any]) -> None:
        self.keypoint_confidence = float(config.get("keypoint_confidence", 0.35))
        self.sitting_hip = tuple(float(v) for v in config.get("sitting_hip_angle_deg", [55, 135]))
        self.sitting_knee = tuple(float(v) for v in config.get("sitting_knee_angle_deg", [45, 135]))
        self.standing_min = float(config.get("standing_min_angle_deg", 145))

    @staticmethod
    def _point(keypoints: list[Any], index: int, minimum: float) -> tuple[float, float] | None:
        if index >= len(keypoints):
            return None
        value = keypoints[index]
        if isinstance(value, dict):
            raw = (value.get("x"), value.get("y"), value.get("score", value.get("confidence", 0)))
        elif isinstance(value, list) and len(value) >= 3:
            raw = (value[0], value[1], value[2])
        else:
            return None
        try:
            x, y, confidence = float(raw[0]), float(raw[1]), float(raw[2])
        except (TypeError, ValueError):
            return None
        return (x, y) if confidence >= minimum and math.isfinite(x) and math.isfinite(y) else None

    @staticmethod
    def _angle(first: tuple[float, float], vertex: tuple[float, float], last: tuple[float, float]) -> float | None:
        left = (first[0] - vertex[0], first[1] - vertex[1])
        right = (last[0] - vertex[0], last[1] - vertex[1])
        denominator = math.hypot(*left) * math.hypot(*right)
        if denominator <= 1e-9:
            return None
        value = max(-1.0, min(1.0, (left[0] * right[0] + left[1] * right[1]) / denominator))
        return math.degrees(math.acos(value))

    def classify(self, keypoints_value: Any) -> dict[str, Any]:
        if not isinstance(keypoints_value, list):
            return {"label": "unknown", "reason": "missing_keypoints"}
        side_angles: list[tuple[float, float]] = []
        for shoulder_i, hip_i, knee_i, ankle_i in self.SIDES:
            points = [self._point(keypoints_value, index, self.keypoint_confidence) for index in (shoulder_i, hip_i, knee_i, ankle_i)]
            if all(points):
                shoulder, hip, knee, ankle = points
                hip_angle = self._angle(shoulder, hip, knee)  # type: ignore[arg-type]
                knee_angle = self._angle(hip, knee, ankle)  # type: ignore[arg-type]
                if hip_angle is not None and knee_angle is not None:
                    side_angles.append((hip_angle, knee_angle))
        if not side_angles:
            return {"label": "unknown", "reason": "insufficient_visible_joints"}
        side_labels: list[str] = []
        for hip_angle, knee_angle in side_angles:
            if self.sitting_hip[0] <= hip_angle <= self.sitting_hip[1] and self.sitting_knee[0] <= knee_angle <= self.sitting_knee[1]:
                side_labels.append("sitting")
            elif hip_angle >= self.standing_min and knee_angle >= self.standing_min:
                side_labels.append("standing")
            else:
                side_labels.append("unknown")
        hip_angle = sum(pair[0] for pair in side_angles) / len(side_angles)
        knee_angle = sum(pair[1] for pair in side_angles) / len(side_angles)
        details = {
            "hip_angle_deg": round(hip_angle, 1),
            "knee_angle_deg": round(knee_angle, 1),
            "visible_sides": len(side_angles),
            "side_labels": side_labels,
        }
        known = {label for label in side_labels if label != "unknown"}
        if len(known) > 1:
            return {"label": "unknown", "reason": "left_right_disagreement", **details}
        if "sitting" in known:
            return {"label": "sitting", "confidence": "rule_match", **details}
        if "standing" in known and all(label == "standing" for label in side_labels):
            return {"label": "standing", "confidence": "rule_match", **details}
        return {"label": "unknown", "reason": "angles_outside_calibrated_ranges", **details}


class VisionApplication:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.started = time.monotonic()
        self.media_roots = [os.path.realpath(str(item)) for item in config.get("media_roots", [])]
        if not self.media_roots or not all(os.path.isabs(item) for item in self.media_roots):
            raise ValueError("media_roots must contain absolute paths")
        runtime = require_dict(config.get("runtime"), "runtime")
        models = require_dict(config.get("models"), "models")
        self.runner = CommandRunner(runtime, models)
        self.gallery = Gallery(require_dict(config.get("face_recognition"), "face_recognition"))
        self.posture = PostureClassifier(require_dict(config.get("posture"), "posture"))
        limits = require_dict(config.get("resource_limits"), "resource_limits")
        self.semaphore = threading.BoundedSemaphore(int(limits.get("max_parallel_inference", 1)))
        self.max_input_bytes = int(limits.get("max_input_file_bytes", 20 * 1024 * 1024))
        self.staging_dir = os.path.realpath(str(limits.get("staging_dir", os.path.join(os.path.dirname(self.gallery.path), "staging"))))
        self.request_timeout_ms = int(config.get("request_timeout_ms", 15000))
        token_path = config.get("auth_token_file")
        self.auth_token: str | None = None
        if token_path:
            if not isinstance(token_path, str) or not os.path.isabs(token_path):
                raise ValueError("auth_token_file must be an absolute path")
            with open(token_path, "r", encoding="utf-8") as stream:
                self.auth_token = stream.read().strip()
            if len(self.auth_token) < 32:
                raise ValueError("AI Service authentication token must contain at least 32 characters")
        self.metrics = Counter()
        self.metrics_lock = threading.Lock()

    def health(self) -> dict[str, Any]:
        runtime = self.runner.health()
        return {
            "service": "rg660mk-vision-ai",
            "status": "ready" if runtime["ready"] else "degraded",
            "runtime": runtime,
            "gallery": {"identities": self.gallery.count(), "raw_images_stored": False},
            "capabilities": {"object_detection": True, "face_recognition": True, "posture_detection": True},
        }

    def metric_snapshot(self) -> dict[str, Any]:
        with self.metrics_lock:
            counters = dict(self.metrics)
        return {"uptime_s": round(time.monotonic() - self.started, 3), "requests": counters}

    def infer(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id") or uuid.uuid4())
        input_value = require_dict(payload.get("input"), "input")
        path = validate_media_path(input_value.get("path"), self.media_roots, self.max_input_bytes)
        options = require_dict(payload.get("options", {}), "options")
        try:
            requested_timeout = int(payload.get("timeout_ms", self.request_timeout_ms))
        except (TypeError, ValueError) as exc:
            raise ServiceError("INVALID_ARGUMENT", "timeout_ms must be an integer") from exc
        timeout_ms = min(max(100, requested_timeout), self.request_timeout_ms)
        if not self.semaphore.acquire(blocking=False):
            raise ServiceError("RESOURCE_LIMIT", "another inference is already running", 429)
        started = time.monotonic()
        staged_path: str | None = None
        try:
            staged_path = stage_media_path(path, self.staging_dir, self.max_input_bytes)
            result = self.runner.infer(operation, staged_path, timeout_ms / 1000.0, options)
            if operation == "face":
                result = self._identify_faces(result)
            elif operation == "pose":
                result = self._classify_postures(result)
            latency_ms = round((time.monotonic() - started) * 1000, 3)
            with self.metrics_lock:
                self.metrics[f"{operation}_ok"] += 1
            return {"ok": True, "request_id": request_id, "result": result, "latency_ms": latency_ms}
        except ServiceError:
            with self.metrics_lock:
                self.metrics[f"{operation}_error"] += 1
            raise
        finally:
            if staged_path:
                try:
                    os.unlink(staged_path)
                except OSError:
                    pass
            self.semaphore.release()

    def _identify_faces(self, result: dict[str, Any]) -> dict[str, Any]:
        faces = result.get("faces")
        if not isinstance(faces, list):
            raise ServiceError("RUNTIME_UNAVAILABLE", "face runner result must contain faces array", 503)
        identified: list[dict[str, Any]] = []
        for face in faces:
            if not isinstance(face, dict):
                raise ServiceError("RUNTIME_UNAVAILABLE", "face runner item must be an object", 503)
            item = face
            name, similarity = self.gallery.match(item.get("embedding"))
            public = {key: value for key, value in item.items() if key != "embedding"}
            public["subject_id"] = name or "unknown"
            public["similarity"] = round(similarity, 6)
            public["matched"] = name is not None
            identified.append(public)
        return {"faces": identified, "count": len(identified)}

    def _classify_postures(self, result: dict[str, Any]) -> dict[str, Any]:
        persons = result.get("persons")
        if not isinstance(persons, list):
            raise ServiceError("RUNTIME_UNAVAILABLE", "pose runner result must contain persons array", 503)
        output: list[dict[str, Any]] = []
        for person in persons:
            if not isinstance(person, dict):
                raise ServiceError("RUNTIME_UNAVAILABLE", "pose runner item must be an object", 503)
            item = person
            public = dict(item)
            public["posture"] = self.posture.classify(item.get("keypoints"))
            output.append(public)
        return {"persons": output, "count": len(output)}


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], max_clients: int) -> None:
        self.client_slots = threading.BoundedSemaphore(max_clients)
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.client_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.client_slots.release()


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "RG660MKVision/1.0"

    @property
    def application(self) -> VisionApplication:
        return self.server.application  # type: ignore[attr-defined,no-any-return]

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(max(1.0, self.application.request_timeout_ms / 1000.0))

    def _authorized(self) -> bool:
        expected = self.application.auth_token
        if expected is None:
            return True
        supplied = self.headers.get("X-AI-Service-Token", "")
        if supplied and hmac.compare_digest(supplied, expected):
            return True
        self._send(401, {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "valid local service token required"}})
        return False

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} [{self.log_date_time_string()}] {fmt % args}", flush=True)

    def _send(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict[str, Any]:
        maximum = int(self.application.config.get("max_request_bytes", 2 * 1024 * 1024))
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ServiceError("INVALID_ARGUMENT", "invalid Content-Length") from exc
        if length <= 0 or length > maximum:
            raise ServiceError("RESOURCE_LIMIT", f"request body must be between 1 and {maximum} bytes", 413)
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError("INVALID_ARGUMENT", "request body must be valid UTF-8 JSON") from exc
        return require_dict(value, "request")

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        try:
            if self.path == "/health":
                self._send(200, {"ok": True, "result": self.application.health()})
            elif self.path == "/metrics":
                self._send(200, {"ok": True, "result": self.application.metric_snapshot()})
            else:
                self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "endpoint not found"}})
        except ServiceError as exc:
            self._send(exc.status, {"ok": False, "error": {"code": exc.code, "message": exc.message}})
        except Exception:
            self._send(500, {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": "internal service error"}})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        routes = {"/vision/detect": "detect", "/vision/face": "face", "/vision/posture": "pose"}
        unsupported = {"/audio/asr", "/audio/kws", "/tts"}
        if self.path in unsupported:
            self._send(503, {"ok": False, "error": {"code": "MODEL_UNAVAILABLE", "message": "audio capability is not installed"}})
            return
        operation = routes.get(self.path)
        if operation is None:
            self._send(404, {"ok": False, "error": {"code": "NOT_FOUND", "message": "endpoint not found"}})
            return
        request_id: str | None = None
        try:
            payload = self._payload()
            request_id = str(payload.get("request_id") or uuid.uuid4())
            payload["request_id"] = request_id
            self._send(200, self.application.infer(operation, payload))
        except ServiceError as exc:
            self._send(exc.status, {"ok": False, "request_id": request_id, "error": {"code": exc.code, "message": exc.message}})
        except Exception:
            self._send(500, {"ok": False, "request_id": request_id, "error": {"code": "INTERNAL_ERROR", "message": "internal service error"}})


def main() -> int:
    parser = argparse.ArgumentParser(description="RG660MK-EU constrained local vision service")
    parser.add_argument("--config", default=os.environ.get("AI_SERVICE_CONFIG", "/data/ai_cpe/demo/config/ai-service.json"))
    args = parser.parse_args()
    config = load_config(args.config)
    listen = str(config.get("listen", "127.0.0.1"))
    if listen != "127.0.0.1":
        raise SystemExit("refusing non-IPv4-loopback listen address")
    application = VisionApplication(config)
    limits = require_dict(config.get("resource_limits"), "resource_limits")
    server = BoundedThreadingHTTPServer(
        (listen, int(config.get("port", 8765))),
        ApiHandler,
        max_clients=int(limits.get("max_http_clients", 4)),
    )
    server.application = application  # type: ignore[attr-defined]

    def stop(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"rg660mk-vision-ai listening on {listen}:{config.get('port', 8765)}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
