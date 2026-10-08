"""FeatureWindow —— 特征窗（设计文档 4 / 6.4），决策模型的输入单元。"""
from dataclasses import dataclass, field, asdict

SCHEMA_VERSION = 1


@dataclass
class FeatureWindow:
    window_end_ms: int
    window_s: int = 10
    traffic_class: str = "generic"
    traffic_confidence: float = 0.0
    cell: dict = field(default_factory=dict)     # {cell_id, band, pci, arfcn, rat}
    features: dict = field(default_factory=dict) # rsrp_mean/rsrp_slope/rtt_p95/...
    missing: list = field(default_factory=list)  # 参与窗计算但不可得的特征名
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureWindow":
        d = dict(d)
        d.pop("schema_version", None)
        return cls(**d)
