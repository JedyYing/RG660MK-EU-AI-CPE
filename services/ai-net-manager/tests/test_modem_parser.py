"""Step 2 验收：MTK E 命令解析器 + RadioSample 组装（真实回包 fixtures）。"""
import os

import pytest

from ai_net.collectors import modem

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _lines(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f
                if ln.strip() and not ln.startswith("#")]


CELLMEAS = _lines("ecellmeas_20261008.txt")
C5GREG = ['+C5GREG: 2,1,"1A2B3C","594FCB484",11']


def test_parse_rows_and_merge_plmn():
    rows = modem._merge_rows(modem.parse_ecellmeas_lines(CELLMEAS))
    assert len(rows) == 3, "5 行原始数据应合并为 3 个小区（PLMN 共享）"
    top = rows[0]
    assert (top["rat"], top["arfcn"], top["pci"]) == (11, 426030, 784)
    assert top["cid"] == "594FCB484"
    assert top["plmn"] == [["46011", "CHN-CT"], ["46001", "CHN-UNICOM"]]
    assert rows[2]["val3_raw"] == -16


def test_calibration_provisional_div10_and_raw_only():
    row = modem.parse_ecellmeas_lines(CELLMEAS[:1])[0]
    c = modem.calibrate_ecellmeas(row, "provisional_div10")
    assert c["rsrp_dbm"] == -31.7 and c["rsrq_db"] == -4.6 and c["sinr_db"] == 2.9
    assert c["provisional"] is True
    c2 = modem.calibrate_ecellmeas(row, "raw_only")
    assert c2["rsrp_dbm"] is None and c2["provisional"] is False
    with pytest.raises(ValueError):
        modem.calibrate_ecellmeas(row, "nonsense")


def test_build_radio_sample_serving_match_by_nci():
    s = modem.build_radio_sample(CELLMEAS, C5GREG, None, ts_ms=1770000000000)
    assert s.cell_id == "594FCB484"
    assert (s.pci, s.arfcn, s.band, s.rat) == (784, 426030, "n1", "NR5G-SA")
    assert s.reg_status == 1 and s.tac == "1A2B3C"
    assert "serve:nci" in s.source
    assert len(s.neighbours) == 3
    assert sum(1 for n in s.neighbours if n.is_serving) == 1
    assert s.mcc == "460" and s.mnc == "11"
    # 不可得项必须显式 missing，不允许填 0
    assert "ca_active" in s.missing and "ecsq(serving_calibration)" in s.missing
    d = s.to_dict()
    assert d["schema_version"] == 1
    s2 = type(s).from_dict(d)
    assert s2.cell_id == s.cell_id and len(s2.neighbours) == 3


def test_ecsq_overrides_serving_canonical():
    esq = ["+ECSQ: 99,255,255,255,15,60"]
    s = modem.build_radio_sample(CELLMEAS, C5GREG, esq, ts_ms=1)
    assert s.rsrp_dbm == -81            # 60 - 141
    assert s.rsrq_db == -12.0           # 15/2 - 19.5
    assert "ecsq" in s.source


def test_band_lookup():
    assert modem.band_from_arfcn(11, 426030) == "n1"
    assert modem.band_from_arfcn(11, 636666) == "n78"
    assert modem.band_from_arfcn(7, 1650) == "B3"
    assert modem.band_from_arfcn(7, 999999) is None


def test_lock_command_builders_and_validation():
    assert modem.build_lock_command(11, 426030, 784) == "AT+EMMCHLCK=1,11,0,426030,784,0"
    assert modem.build_lock_command(7, 1650, 78) == "AT+EMMCHLCK=1,7,0,1650,78,0"
    assert modem.build_unlock_command() == "AT+EMMCHLCK=0"
    with pytest.raises(ValueError):
        modem.build_lock_command(5, 1, 1)          # rat 不在 {2,7,11}
    with pytest.raises(ValueError):
        modem.build_lock_command(11, 99999999, 1)
    with pytest.raises(ValueError):
        modem.build_lock_command(11, 1, 1008)      # pci > 1007
    assert modem.parse_lock_state(["+EMMCHLCK: 0"]) == 0
    assert modem.parse_lock_state(["+EMMCHLCK: 1"]) == 1
    assert modem.parse_lock_state([]) is None


def test_c5greg_parse_variants():
    assert modem.parse_c5greg(['+C5GREG: 2,1,"1A2B3C","594FCB484",11'])["nci"] == "594FCB484"
    d = modem.parse_c5greg(['+C5GREG: 0,2'])
    assert d["mode"] == 0 and d["status"] == 2 and d["nci"] is None
    assert modem.parse_c5greg([]) == {}
