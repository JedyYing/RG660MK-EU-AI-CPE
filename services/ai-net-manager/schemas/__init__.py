"""ai-net-manager canonical schemas（设计文档第 4 章）。

约定：
- 所有 schema 携带 schema_version；
- 不可得字段一律 None 并在 missing 列表中显式标记，禁止用 0 冒充；
- to_dict/from_dict 保证 JSONL 落盘与回放一致。
"""
from .radio_sample import NeighbourCell, RadioSample, SCHEMA_VERSION as RADIO_SCHEMA_VERSION
from .qoe_sample import QoESample, SCHEMA_VERSION as QOE_SCHEMA_VERSION
from .traffic_prediction import TrafficPrediction, SCHEMA_VERSION as TRAFFIC_SCHEMA_VERSION
from .feature_window import FeatureWindow, SCHEMA_VERSION as FEATURE_SCHEMA_VERSION
from .decision import Decision, Candidate, SCHEMA_VERSION as DECISION_SCHEMA_VERSION

__all__ = [
    "NeighbourCell", "RadioSample", "RADIO_SCHEMA_VERSION",
    "QoESample", "QOE_SCHEMA_VERSION",
    "TrafficPrediction", "TRAFFIC_SCHEMA_VERSION",
    "FeatureWindow", "FEATURE_SCHEMA_VERSION",
    "Decision", "Candidate", "DECISION_SCHEMA_VERSION",
]
