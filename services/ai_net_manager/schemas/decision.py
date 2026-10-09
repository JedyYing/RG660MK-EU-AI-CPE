"""Decision — 决策日志结构（设计 §9.1）。"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List

SCHEMA_VERSION = "1.0"


@dataclass
class Decision:
    decision_id: str
    ts_ms: int
    schema_version: str = SCHEMA_VERSION
    state: str = "NORMAL"                # 状态机状态
    mode: str = "shadow"
    serving_cell: Dict = field(default_factory=dict)
    serving_score: Optional[float] = None
    candidates: List[Dict] = field(default_factory=list)
    trigger: str = ""                    # bad_qoe | score_gain | radio_drop | policy
    traffic_class: str = "generic"
    traffic_confidence: float = 0.0
    action: str = "none"                 # none | recommend | execute | rollback
    action_detail: Dict = field(default_factory=dict)
    blocked_by: List[str] = field(default_factory=list)   # 被哪条安全规则拦截
    model_version: str = "baseline-1.0"
    rules_version: str = "rules-1.0"
    qoe_before: Optional[float] = None
    qoe_after: Optional[float] = None
    rollback: bool = False
    rollback_reason: str = ""
    top_features: List[str] = field(default_factory=list)  # 设计 §13 可解释输出

    def to_dict(self) -> dict:
        return asdict(self)
