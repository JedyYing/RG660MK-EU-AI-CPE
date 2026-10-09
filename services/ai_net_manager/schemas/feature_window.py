"""FeatureWindow / TrafficPrediction — 特征窗与业务分类输出（设计 §4/§5.4）。"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List

SCHEMA_VERSION = "1.0"


@dataclass
class TrafficPrediction:
    cls: str = "generic"                 # gaming|video|conference|voip|camera|backup|generic
    confidence: float = 0.0
    distribution: Dict[str, float] = field(default_factory=dict)
    source: str = "heuristic"            # heuristic | lgbm
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FeatureWindow:
    window_end_ms: int
    window_s: int
    schema_version: str = SCHEMA_VERSION
    traffic_class: str = "generic"
    traffic_confidence: float = 0.0
    cell: Dict = field(default_factory=dict)
    features: Dict = field(default_factory=dict)   # 见设计 §4 feature_window.json
    missing_flags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
