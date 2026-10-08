"""RadioSample / NeighbourCell —— 无线侧 canonical schema。

来源适配：RG660MK-EU 固件无 QENG/QNWINFO/QCAINFO（实测 CME ERROR 4），
无线采样来自 MTK E 命令族：
  - AT+ECELLMEAS?  -> 服务小区 + 邻区（rat/arfcn/pci/rsrp/rsrq/snr/cid/plmn）
  - AT+C5GREG=2    -> 注册状态 + TAC/NCI
  - AT+ECSQ        -> 标准化 RSRP/RSRQ（如有）
解析器：src/ai_net/collectors/modem.py（禁止业务代码直接按逗号下标解析）。
"""
from dataclasses import dataclass, field, asdict

SCHEMA_VERSION = 1

# 3GPP TS 27.007 AcT 值
ACT_NAMES = {0: "GSM", 2: "UTRAN", 3: "GSM-EGPRS", 7: "LTE", 11: "NR5G-SA", 13: "NR5G-SA"}


@dataclass
class NeighbourCell:
    """邻区/候选小区（测量量来自 ECELLMEAS 行）。"""
    rat: int | None = None            # 3GPP AcT：7=LTE, 11=NR
    arfcn: int | None = None
    pci: int | None = None
    cid: str | None = None            # hex NCI（邻区可能缺）
    rsrp_dbm: float | None = None
    rsrq_db: float | None = None
    sinr_db: float | None = None
    plmn: list | None = None          # [["46011", "CHN-CT"], ...]
    is_serving: bool = False
    source: str = "ecellmeas"
    raw: str = ""

    def key(self) -> str:
        """候选唯一键：RAT+ARFCN+PCI（邻区无 Cell ID 时的规范做法）。"""
        return "%s:%s:%s" % (self.rat, self.arfcn, self.pci)


@dataclass
class RadioSample:
    ts_ms: int
    rat: str | None = None            # "NR5G-SA" / "LTE" / ...
    mcc: str | None = None
    mnc: str | None = None
    cell_id: str | None = None        # hex NCI/CGI（服务小区）
    pci: int | None = None
    arfcn: int | None = None
    band: str | None = None           # 由 arfcn 反查（无 QNWINFO 时本地映射）
    rsrp_dbm: float | None = None
    rsrq_db: float | None = None
    sinr_db: float | None = None
    rssi_dbm: float | None = None
    ca_active: bool | None = None     # RG660MK-EU 不可得 -> None + missing
    ca_cc_count: int | None = None
    reg_status: int | None = None     # C5GREG <stat>
    tac: str | None = None
    neighbours: list = field(default_factory=list)   # list[NeighbourCell]
    missing: list = field(default_factory=list)      # 不可得字段名
    source: str = ""                  # "ecellmeas+c5greg" 等
    raw: str = ""                     # 原始回包（截断保存）
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d["neighbours"] = [asdict(n) for n in self.neighbours]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RadioSample":
        d = dict(d)
        nb = [NeighbourCell(**n) for n in d.pop("neighbours", [])]
        d.pop("schema_version", None)
        return cls(neighbours=nb, **d)
