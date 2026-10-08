"""MTK E 命令族固件特化解析层（设计文档 3.1：raw -> parser -> canonical）。

RG660MK-EU 实测（2026-10-08，FW R01.QUECTEL_UNLOCK.177C2E9B701）：
- QENG / QNWINFO / QCAINFO / QNWLOCK 全部不存在（CME ERROR 4 = unknown command）。
- 可用替代：
    AT+ECELLMEAS?   服务小区 + 邻区测量（本文件主解析对象）
    AT+C5GREG?/=2   注册状态 + TAC/NCI
    AT+ECSQ?        标准 CESQ 语义的 RSRP/RSRQ（如可用，作为 canonical 校准源）
    AT+EMMCHLCK     MTK 锁小区/锁频（L3 执行器唯一允许的控制命令）

实测样本：
    +ECELLMEAS: 11,426030,784,-317,-46,29,"594FCB484",1,"46011","CHN-CT",1,38880

字段顺序（对齐 n0p/furimodem-tool parse_ecellmeas + 本机实测）：
    <rat>,<arfcn>,<pci>,<val1>,<val2>,<val3>,<cid>,<num_plmn>,<plmn_id>,<plmn_name>,<ext1>,<ext2>

⚠ 单位未定论（开放问题 OP-1）：val1/val2/val3 在强小区上实测为 -317/-46/29（负值、量级小），
   与 dBm/dB 直觉不符。两种假设：
   H1: 分别为 RSRP/RSRQ/SNR 的 0.1 倍缩放（-> -31.7 dBm / -4.6 dB / 2.9 dB）
   H2: 字段序或缩放不同（需 AT+ECSQ 现场对照标定）
   按设计文档"缺失必须显式保留 missing flag，不得填 0"的要求：
   默认输出 raw 值 + `calibration` 标记；标定前 canonical 字段按当前配置策略输出。
   现场标定工具：tools/calibrate_ecellmeas.py（需设备在线、只读）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 命令模板（不含参数拼装 —— 一律用结构化 builder）
# ---------------------------------------------------------------------------
CMD_ECELLMEAS_QUERY = "AT+ECELLMEAS?"      # 只读
CMD_C5GREG_QUERY = "AT+C5GREG?"            # 只读；如需 NCI 且 ? 不足时用 =2（URC 模式，低风险）
CMD_C5GREG_URC = "AT+C5GREG=2"              # ⛔ 写类，未经用户在场授权一律不得发送（2026-10-08 事故后政策）；当前无任何调用点
CMD_ECSQ_QUERY = "AT+ECSQ?"                # 标准 CESQ 语义（如固件支持）
CMD_EMMCHLCK_QUERY = "AT+EMMCHLCK?"        # 锁状态 0/1
CMD_EMMCHLCK_TEST = "AT+EMMCHLCK=?"        # 仅探测（capability probe 用）

# 锁参数合法域（实测 +EMMCHLCK: (0-3),(0,2,7,11),(0,1),(0-2279165),(0-1007)）
LOCK_RATS = (2, 7, 11)          # 2=UTRAN(不用), 7=LTE, 11=NR
LOCK_ARFCN_MAX = 2279165
LOCK_PCI_MAX = 1007

NR_BANDS = (                    # (band, arfcn_low, arfcn_high)；n78 先于 n77 判定
    ("n78", 620000, 653333),
    ("n77", 620000, 680000),
    ("n79", 693334, 733333),
    ("n41", 499200, 537999),
    ("n1", 422000, 434000),
    ("n3", 361000, 376000),
    ("n5", 173800, 178800),
    ("n8", 185000, 192000),
    ("n28", 151600, 160600),
)

LTE_BANDS = (                   # (band, earfcn_low, earfcn_high)
    ("B1", 0, 599), ("B3", 1200, 1949), ("B5", 2400, 2649), ("B8", 3450, 3799),
    ("B34", 36200, 36349), ("B38", 37750, 38249), ("B39", 38250, 38649),
    ("B40", 38650, 39649), ("B41", 39650, 41489),
)

_RAT_NAMES = {2: "UTRAN", 7: "LTE", 11: "NR5G-SA", 13: "NR5G-SA", 12: "NR5G-NSA"}


def rat_name(rat: int | None) -> str | None:
    if rat is None:
        return None
    return _RAT_NAMES.get(int(rat), "ACT%d" % rat)


def band_from_arfcn(rat: int | None, arfcn: int | None) -> str | None:
    if arfcn is None:
        return None
    table = NR_BANDS if rat in (11, 12, 13) else LTE_BANDS
    for name, lo, hi in table:
        if lo <= int(arfcn) <= hi:
            return name
    return None


def _split_quoted(s: str) -> list:
    """按逗号拆行，保留引号内内容（去引号）。"""
    out, buf, inq = [], [], False
    for ch in s:
        if ch == '"':
            inq = not inq
        elif ch == "," and not inq:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf).strip())
    return out


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ECELLMEAS
# ---------------------------------------------------------------------------
_ECELLMEAS_RE = re.compile(r"\+ECELLMEAS:\s*(.+)")


def parse_ecellmeas_lines(lines: list) -> list:
    """解析一行或多行 +ECELLMEAS，返回 [row, ...]（保留 raw 字符串）。"""
    rows = []
    for ln in lines:
        m = _ECELLMEAS_RE.search(ln)
        if not m:
            continue
        parts = _split_quoted(m.group(1))
        if len(parts) < 7:
            continue
        row = {
            "rat": _to_int(parts[0]),
            "arfcn": _to_int(parts[1]),
            "pci": _to_int(parts[2]),
            "val1_raw": _to_int(parts[3]),
            "val2_raw": _to_int(parts[4]),
            "val3_raw": _to_int(parts[5]),
            "cid": parts[6] or None,
            "num_plmn": _to_int(parts[7]) if len(parts) > 7 else None,
            "plmn": [],
            "ext": parts[10:] if len(parts) > 10 else [],
            "raw": ln,
        }
        if row["num_plmn"]:
            idx = 8
            for _ in range(row["num_plmn"]):
                if idx + 1 < len(parts):
                    row["plmn"].append([parts[idx], parts[idx + 1]])
                idx += 2
        rows.append(row)
    return rows


def calibrate_ecellmeas(row: dict, calibration: str = "provisional_div10") -> dict:
    """把 raw 测量值映射为 canonical 字段。

    calibration:
      - "raw_only"            : 只保留 raw，canonical 置 None（最保守）
      - "provisional_div10"   : 假设 H1，val/10（默认，标记 provisional）
      - "div10_off_140"       : 备用假设，val/10 - 140
    输出附加：rsrp_dbm / rsrq_db / sinr_db / calibration / provisional
    """
    out = dict(row)
    v1, v2, v3 = row.get("val1_raw"), row.get("val2_raw"), row.get("val3_raw")
    out["calibration"] = calibration
    out["provisional"] = calibration != "raw_only"
    if calibration == "raw_only" or v1 is None:
        out.update(rsrp_dbm=None, rsrq_db=None, sinr_db=None)
        return out
    if calibration == "provisional_div10":
        out["rsrp_dbm"] = round(v1 / 10.0, 1)
        out["rsrq_db"] = round(v2 / 10.0, 1) if v2 is not None else None
        out["sinr_db"] = round(v3 / 10.0, 1) if v3 is not None else None
    elif calibration == "div10_off_140":
        out["rsrp_dbm"] = round(v1 / 10.0 - 140.0, 1)
        out["rsrq_db"] = round(v2 / 10.0 - 19.5, 1) if v2 is not None else None
        out["sinr_db"] = round(v3 / 10.0 - 20.0, 1) if v3 is not None else None
    else:
        raise ValueError("unknown calibration: %s" % calibration)
    return out


# ---------------------------------------------------------------------------
# C5GREG（注册状态 / TAC / NCI）
# ---------------------------------------------------------------------------
_C5GREG_RE = re.compile(r"\+C5GREG:\s*(.+)")


def parse_c5greg(lines: list) -> dict:
    """+C5GREG: <n>,<stat>[,<tac>,<ci>,<AcT>[,<cause_type>,<reject_cause>]]"""
    for ln in lines:
        m = _C5GREG_RE.search(ln)
        if not m:
            continue
        p = _split_quoted(m.group(1))
        out = {"mode": _to_int(p[0]) if p else None,
               "status": _to_int(p[1]) if len(p) > 1 else None,
               "tac": p[2] if len(p) > 2 else None,
               "nci": p[3] if len(p) > 3 else None,
               "act": _to_int(p[4]) if len(p) > 4 else None}
        return out
    return {}


# ---------------------------------------------------------------------------
# ECSQ / CESQ（标准语义，canonical 校准源）
# ---------------------------------------------------------------------------
_ECSQ_RE = re.compile(r"\+(?:ECSQ|CESQ):\s*([\d,\-]+)")


def parse_ecsq(lines: list) -> dict:
    """+CESQ: <rxlev>,<ber>,<rscp>,<ecno>,<rsrq>,<rsrp>[,...]
    canonical: rsrp_dbm = rsrp-141, rsrq_db = rsrq/2-19.5（TS 27.007）"""
    for ln in lines:
        m = _ECSQ_RE.search(ln)
        if not m:
            continue
        v = [_to_int(x) for x in m.group(1).split(",")]
        out = {"raw": v}
        if len(v) >= 6:
            rsrq, rsrp = v[4], v[5]
            out["rsrq_db"] = None if rsrq in (None, 255) else round(rsrq / 2.0 - 19.5, 1)
            out["rsrp_dbm"] = None if rsrp in (None, 255) else rsrp - 141
            if len(v) > 6 and v[6] not in (None, 255):
                out["rssi_dbm"] = v[6] - 110
            if len(v) > 8 and v[8] not in (None, 255):
                out["sinr_db"] = round(v[8] / 2.0 - 20.0, 1)
        return out
    return {}


# ---------------------------------------------------------------------------
# EMMCHLCK（L3 锁小区 —— 唯一允许写操作；结构化参数 + 合法域校验）
# ---------------------------------------------------------------------------
_EMMCHLCK_RE = re.compile(r"\+EMMCHLCK:\s*(\d+)")


def parse_lock_state(lines: list) -> int | None:
    for ln in lines:
        m = _EMMCHLCK_RE.search(ln)
        if m:
            return int(m.group(1))
    return None


def validate_lock_params(rat: int, arfcn: int, pci: int) -> None:
    if rat not in LOCK_RATS:
        raise ValueError("rat %r 不在合法域 %s" % (rat, LOCK_RATS))
    if not (0 <= int(arfcn) <= LOCK_ARFCN_MAX):
        raise ValueError("arfcn %r 超出 0..%d" % (arfcn, LOCK_ARFCN_MAX))
    if not (0 <= int(pci) <= LOCK_PCI_MAX):
        raise ValueError("pci %r 超出 0..%d" % (pci, LOCK_PCI_MAX))


def build_lock_command(rat: int, arfcn: int, pci: int) -> str:
    """实测/FM350-GL 同栈语义：AT+EMMCHLCK=1,<rat>,0,<arfcn>,<pci>,0"""
    validate_lock_params(rat, arfcn, pci)
    return "AT+EMMCHLCK=1,%d,0,%d,%d,0" % (int(rat), int(arfcn), int(pci))


def build_unlock_command() -> str:
    """解锁（撤销锁定的唯一正确方式；锁定状态跨重启保留）。"""
    return "AT+EMMCHLCK=0"


# ---------------------------------------------------------------------------
# 组装 RadioSample
# ---------------------------------------------------------------------------
def _merge_rows(rows: list) -> list:
    """同一小区多 PLMN 行合并（共享网络实测：同 cid/arfcn/pci 出现 46011+46001 两行）。"""
    merged: dict = {}
    order: list = []
    for r in rows:
        key = (r.get("rat"), r.get("arfcn"), r.get("pci"), r.get("cid"))
        if key not in merged:
            merged[key] = dict(r)
            order.append(key)
        else:
            dst = merged[key]
            for p in r.get("plmn") or []:
                if p not in (dst.get("plmn") or []):
                    dst.setdefault("plmn", []).append(p)
            for f in ("val1_raw", "val2_raw", "val3_raw"):
                if dst.get(f) is None:
                    dst[f] = r.get(f)
            dst["raw"] = (dst.get("raw", "") + " || " + r.get("raw", "")).strip(" |")
    return [merged[k] for k in order]


def build_radio_sample(ecellmeas_lines: list, c5greg_lines: list | None = None,
                       ecsq_lines: list | None = None, *, ts_ms: int,
                       calibration: str = "provisional_div10",
                       source: str = "ecellmeas"):
    """把多源回包合成为 schemas.RadioSample。

    服务小区判定：优先用 C5GREG 的 NCI 匹配 ECELLMEAS 行的 cid；
    匹配不到则取第一行（实测按测量强度排序），并记录 serving_match 供报告核对。
    """
    from schemas import NeighbourCell, RadioSample

    rows = [calibrate_ecellmeas(r, calibration)
            for r in _merge_rows(parse_ecellmeas_lines(ecellmeas_lines))]
    reg = parse_c5greg(c5greg_lines or [])
    esq = parse_ecsq(ecsq_lines or [])

    missing = []
    serving = None
    match = "none"
    nci = (reg.get("nci") or "").upper().lstrip("0") if reg.get("nci") else None
    for r in rows:
        cid = (r.get("cid") or "").upper().lstrip("0")
        if nci and cid and cid == nci:
            serving = r
            match = "nci"
            break
    if serving is None and rows:
        serving = rows[0]
        match = "first_row"

    nb = []
    for r in rows:
        nb.append(NeighbourCell(
            rat=r.get("rat"), arfcn=r.get("arfcn"), pci=r.get("pci"),
            cid=r.get("cid"), rsrp_dbm=r.get("rsrp_dbm"), rsrq_db=r.get("rsrq_db"),
            sinr_db=r.get("sinr_db"), plmn=r.get("plmn"),
            is_serving=(r is serving), source="ecellmeas",
            raw=r.get("raw", ""),
        ))

    if serving is None:
        missing.append("serving_cell")
    if not rows:
        missing.append("ecellmeas")

    rsrp = serving.get("rsrp_dbm") if serving else None
    rsrq = serving.get("rsrq_db") if serving else None
    sinr = serving.get("sinr_db") if serving else None
    src_bits = ["ecellmeas"]
    if esq:                       # 标准语义优先（canonical），ECELLMEAS 作邻区/结构信息
        if esq.get("rsrp_dbm") is not None:
            rsrp, src_bits = esq["rsrp_dbm"], src_bits + ["ecsq"]
        if esq.get("rsrq_db") is not None:
            rsrq, src_bits = esq["rsrq_db"], src_bits + ["ecsq"]
        if esq.get("sinr_db") is not None:
            sinr = esq["sinr_db"]
    else:
        missing.append("ecsq(serving_calibration)")

    # RG660MK-EU 确认不可得项
    missing += ["ca_active", "ca_cc_count", "rssi_dbm"]

    mcc = mnc = None
    if serving and serving.get("plmn"):
        p = serving["plmn"][0][0]
        if p and len(p) >= 5:
            mcc, mnc = p[:3], p[3:]

    return RadioSample(
        ts_ms=ts_ms,
        rat=rat_name(serving.get("rat")) if serving else None,
        mcc=mcc, mnc=mnc,
        cell_id=serving.get("cid") if serving else None,
        pci=serving.get("pci") if serving else None,
        arfcn=serving.get("arfcn") if serving else None,
        band=band_from_arfcn(serving.get("rat") if serving else None,
                             serving.get("arfcn") if serving else None),
        rsrp_dbm=rsrp, rsrq_db=rsrq, sinr_db=sinr,
        reg_status=reg.get("status"), tac=reg.get("tac"),
        neighbours=nb, missing=sorted(set(missing)),
        source="+".join(src_bits) + ("|serve:%s" % match),
        raw="\n".join([r.get("raw", "") for r in rows])[:2000],
    )
