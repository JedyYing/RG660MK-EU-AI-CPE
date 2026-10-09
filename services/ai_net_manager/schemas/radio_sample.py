"""RadioSample — 无线侧规范样本（设计 §3.1/§4）。"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

SCHEMA_VERSION = "1.0"


@dataclass
class RadioSample:
    ts_ms: int
    schema_version: str = SCHEMA_VERSION
    rat: Optional[str] = None            # "5G" / "LTE" / "5G-SA"
    reg_state: Optional[str] = None      # HOME / SEARCHING / ...
    radio_on: Optional[bool] = None
    mcc: Optional[str] = None
    mnc: Optional[str] = None
    cell_id: Optional[str] = None        # NCI（十六进制字符串）
    tac: Optional[str] = None
    pci: Optional[int] = None
    arfcn: Optional[int] = None
    band: Optional[str] = None
    rsrp_dbm: Optional[float] = None
    rsrq_db: Optional[float] = None
    sinr_db: Optional[float] = None
    rssi_dbm: Optional[float] = None
    cqi: Optional[int] = None
    tx_power_dbm: Optional[float] = None
    ca_active: Optional[bool] = None
    ca_cc_count: Optional[int] = None
    missing: List[str] = field(default_factory=list)   # 缺失字段显式标记（设计 §3.3）
    source: str = "mtk-mipc"
    raw: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
