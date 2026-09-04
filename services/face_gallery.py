#!/usr/bin/env python3
"""Local-only administration for the face embedding gallery."""

from __future__ import annotations

import argparse
import json
import os

from ai_service import CommandRunner, Gallery, ServiceError, load_config, require_dict, validate_media_path


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the local face embedding gallery")
    parser.add_argument("--config", default=os.environ.get("AI_SERVICE_CONFIG", "/data/ai_cpe/demo/config/ai-service.json"))
    subparsers = parser.add_subparsers(dest="action", required=True)
    enroll = subparsers.add_parser("enroll", help="extract one face embedding and store it")
    enroll.add_argument("subject_id")
    enroll.add_argument("image")
    remove = subparsers.add_parser("remove", help="remove all templates for one subject")
    remove.add_argument("subject_id")
    subparsers.add_parser("list", help="list enrolled subject identifiers")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        gallery = Gallery(require_dict(config.get("face_recognition"), "face_recognition"))
        if args.action == "list":
            names = gallery.names()
            emit({"ok": True, "result": {"subjects": names, "count": len(names)}})
            return 0
        if args.action == "remove":
            removed = gallery.remove(args.subject_id)
            emit({"ok": True, "result": {"subject_id": args.subject_id, "removed": removed}})
            return 0

        limits = require_dict(config.get("resource_limits"), "resource_limits")
        roots = [os.path.realpath(str(item)) for item in config.get("media_roots", [])]
        image = validate_media_path(args.image, roots, int(limits.get("max_input_file_bytes", 20 * 1024 * 1024)))
        runner = CommandRunner(require_dict(config.get("runtime"), "runtime"), require_dict(config.get("models"), "models"))
        result = runner.infer("face", image, int(config.get("request_timeout_ms", 15000)) / 1000.0, {"return_embedding": True})
        faces = result.get("faces")
        if not isinstance(faces, list) or len(faces) != 1:
            raise ServiceError("INVALID_ARGUMENT", f"enrollment image must contain exactly one face; detected {len(faces) if isinstance(faces, list) else 0}")
        templates = gallery.add(args.subject_id, require_dict(faces[0], "face").get("embedding"))
        emit({"ok": True, "result": {"subject_id": args.subject_id, "templates": templates, "raw_image_stored": False}})
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ServiceError) as exc:
        code = exc.code if isinstance(exc, ServiceError) else "INVALID_ARGUMENT"
        message = exc.message if isinstance(exc, ServiceError) else str(exc)
        emit({"ok": False, "error": {"code": code, "message": message}})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
