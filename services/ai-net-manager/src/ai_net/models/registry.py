"""模型注册表：SHA256 + schema_version 校验后原子加载（设计 13）。"""
from __future__ import annotations

import hashlib
import json
import os

REQUIRED_KEYS = ("schema_version", "feature_order", "model_type")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def body_sha256(doc: dict) -> str:
    """模型体哈希：除 sha256 字段自身外的规范化 JSON（训练工具写入，端侧校验）。"""
    body = {k: v for k, v in doc.items() if k != "sha256"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_model(path: str, expect_schema: int = 1) -> dict:
    """加载并校验模型；任何不一致抛 ValueError（调用方保持旧模型）。"""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    for k in REQUIRED_KEYS:
        if k not in doc:
            raise ValueError("model missing key: %s" % k)
    if doc["schema_version"] != expect_schema:
        raise ValueError("schema mismatch: %s != %s" % (doc["schema_version"], expect_schema))
    if doc.get("sha256"):
        if doc.get("sha256_scope", "file") == "body":
            got = body_sha256(doc)
        else:
            got = sha256_file(path)
        if doc["sha256"] != got:
            raise ValueError("sha256 mismatch: %s != %s" % (doc["sha256"], got))
    return doc


class ModelRegistry:
    def __init__(self):
        self.traffic = None
        self.qoe = None
        self.errors: list = []

    def load(self, traffic_path: str | None, qoe_path: str | None) -> None:
        for attr, path in (("traffic", traffic_path), ("qoe", qoe_path)):
            if not path:
                continue
            if not os.path.exists(path):
                self.errors.append("%s: not found (%s)" % (attr, path))
                continue
            try:
                setattr(self, attr, load_model(path))
            except (ValueError, OSError) as e:
                self.errors.append("%s: %s" % (attr, e))   # 失败保持旧模型
