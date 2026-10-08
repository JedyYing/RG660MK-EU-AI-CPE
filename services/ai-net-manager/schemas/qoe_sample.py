"""QoESample —— 网络体验采样（设计文档 3.2）。"""
from dataclasses import dataclass, field, asdict

SCHEMA_VERSION = 1


@dataclass
class QoESample:
    ts_ms: int
    wan_if: str | None = None          # 运行期探测，禁止硬编码 ccmni 编号
    dl_mbps: float | None = None       # 窗口内下行字节增量
    ul_mbps: float | None = None
    rtt_ms_p50: float | None = None    # ICMP/UDP 探针
    rtt_ms_p95: float | None = None
    jitter_ms_p95: float | None = None # abs(rtt_t - rtt_t-1) 的 p95
    loss_rate: float | None = None     # 探针丢包率（窗口）
    active_flows: int | None = None    # conntrack 五元组数
    tcp_retrans_rate: float | None = None   # ss -ti 可得则填，否则 None
    stall_ratio: float | None = None   # 无埋点时代理估算；不可得 None
    queue_delay_ms: float | None = None
    missing: list = field(default_factory=list)
    source: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "QoESample":
        d = dict(d)
        d.pop("schema_version", None)
        return cls(**d)
