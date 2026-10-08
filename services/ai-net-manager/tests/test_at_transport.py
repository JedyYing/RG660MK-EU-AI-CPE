"""Step 2 验收：ATTransport 回包分类 / 终止符 / 流式命令护栏（无需设备）。"""
import os

from ai_net.collectors.at_transport import (
    ATResponse, ATTransport, CcciAtBackend, _classify, _find_terminator,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_blocks():
    with open(os.path.join(FIX, "at_raw_samples.txt"), encoding="utf-8") as f:
        txt = f.read()
    blocks = {}
    cur = None
    for ln in txt.splitlines():
        if ln.startswith("--- "):
            cur = ln.strip("- ").strip()
            blocks[cur] = []
        elif cur is not None:
            blocks[cur].append(ln)
    return {k: "\n".join(v).strip("\n") for k, v in blocks.items()}


BLOCKS = _load_blocks()


def _make(raw, cmd):
    r = ATResponse(raw=raw, cmd=cmd)
    _classify(r, cmd)
    return r


def test_ecellmeas_block_classified_with_urc_separated():
    r = _make(BLOCKS["ECELLMEAS 正常（含 echo 与 +EDMFAPP URC 干扰）"], "AT+ECELLMEAS?")
    assert r.ok and r.error is None
    assert len([l for l in r.lines if l.startswith("+ECELLMEAS")]) == 2
    assert all(l.startswith("+EDMFAPP") for l in r.urc)
    assert len(r.urc) == 2


def test_cme_error_recognized_as_unknown_command():
    r = _make(BLOCKS['未知命令（CME ERROR 4 = unknown command，本固件无 QENG 的判定依据）'],
              'AT+QENG="servingcell"')
    assert not r.ok
    assert r.error == "+CME ERROR: 4"
    assert r.lines == []


def test_emmchlcK_query_and_probe_blocks():
    r = _make(BLOCKS["EMMCHLCK 查询"], "AT+EMMCHLCK?")
    assert r.ok and r.lines == ["+EMMCHLCK: 0"]
    r2 = _make(BLOCKS["EMMCHLCK 能力探测"], "AT+EMMCHLCK=?")
    assert r2.ok and r2.lines[0].startswith("+EMMCHLCK: (0-3)")


def test_terminator_detection():
    assert _find_terminator(b"AT\r\r\nOK\r\n") == "OK"
    assert _find_terminator(b"x\r\n+CME ERROR: 4\r\n") == "+CME ERROR: 4"
    assert _find_terminator(b"+ECELLMEAS: 1,2,3\r\n") is None


def test_streaming_mode_guard_blocks_enable_but_allows_disable():
    t = ATTransport(backends=[], lock_file=None)
    r = t.send("AT+ECELLMEAS=1")
    assert not r.ok and "blocked" in (r.error or "")
    r2 = t.send("AT+ECELL=1")
    assert not r2.ok and "blocked" in (r2.error or "")
    # =0 / ? 放行（只是后续没有可用后端）
    assert t.send("AT+ECELLMEAS=0").error != "blocked: streaming-mode command forbidden"
    assert t.send("AT+ECELLMEAS?").error != "blocked: streaming-mode command forbidden"


def test_no_backend_reported_clearly():
    t = ATTransport(backends=[], lock_file=None)
    r = t.send("AT")
    assert not r.ok and r.error == "no backend"
