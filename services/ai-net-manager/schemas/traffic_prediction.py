"""TrafficPrediction —— 业务流分类输出（设计文档 5.4）。"""
from dataclasses import dataclass, field, asdict

SCHEMA_VERSION = 1

CLASSES = ("gaming", "video", "conference", "voip", "camera", "backup")


@dataclass
class TrafficPrediction:
    ts_ms: int
    cls: str = "generic"               # 六分类之一；低置信度回落 generic
    confidence: float = 0.0
    distribution: dict = field(default_factory=dict)   # class -> prob
    model_version: str = "none"        # 未加载模型时为 none（规则/统计兜底）
    missing: list = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TrafficPrediction":
        d = dict(d)
        d.pop("schema_version", None)
        return cls(**d)
