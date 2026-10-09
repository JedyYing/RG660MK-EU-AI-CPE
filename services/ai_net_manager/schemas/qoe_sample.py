"""QoESample — 网络体验样本（设计 §3.2）。"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict

SCHEMA_VERSION = "1.0"


@dataclass
class QoESample:
    ts_ms: int
    schema_version: str = SCHEMA_VERSION
    dl_mbps: Optional[float] = None
    ul_mbps: Optional[float] = None
    rtt_ms: Optional[float] = None        # 本轮探测的瞬时往返
    rtt_ok: Optional[bool] = None
    probe_name: Optional[str] = None
    proto: str = "icmp"
    tcp_retrans_rate: Optional[float] = None
    active_flows: Optional[int] = None
    missing: list = field(default_factory=list)
    raw: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
