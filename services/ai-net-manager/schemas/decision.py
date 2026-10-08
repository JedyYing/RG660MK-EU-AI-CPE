"""Decision / Candidate —— 决策日志（设计文档 9.1 要求字段全覆盖）。"""
from dataclasses import dataclass, field, asdict

SCHEMA_VERSION = 1

STATES = ("NORMAL", "EVALUATE_CANDIDATES", "PRE_SWITCH_GUARD", "EXECUTE",
          "VERIFY", "ROLLBACK", "COOLDOWN")
TRIGGERS = ("bad_qoe", "score_gain", "radio_drop", "policy", "manual", "initial")


@dataclass
class Candidate:
    key: str = ""                    # RAT:ARFCN:PCI
    cell_id: str | None = None
    score: float | None = None       # 0..100 预测 QoE 分数
    score_source: str = "rule"       # model | rule | history
    rsrp_dbm: float | None = None
    passed_hard_filter: bool = False
    reasons: list = field(default_factory=list)   # 可解释性：top features / 拦截原因
    schema_version: int = SCHEMA_VERSION


@dataclass
class Decision:
    decision_id: str
    ts_ms: int
    state: str = "NORMAL"
    trigger: str = "initial"
    serving_cell: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)     # list[Candidate]
    qoe_before: dict = field(default_factory=dict)
    qoe_after: dict | None = None
    traffic_class: str = "generic"
    traffic_confidence: float = 0.0
    model_version: str = "none"
    rules_version: str = "rules.yaml"
    executor_level: str = "L0"       # L0 Shadow / L1 Recommend / L2 Preference / L3 Force
    action: dict = field(default_factory=dict)         # {type, params(脱敏)}
    action_result: str | None = None
    blocked_by: list = field(default_factory=list)     # safety rule 拦截原因
    rollback: bool = False
    rollback_reason: str | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d["candidates"] = [asdict(c) if hasattr(c, "__dataclass_fields__") else c
                           for c in self.candidates]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Decision":
        d = dict(d)
        d["candidates"] = [Candidate(**c) if isinstance(c, dict) else c
                           for c in d.get("candidates", [])]
        d.pop("schema_version", None)
        return cls(**d)
