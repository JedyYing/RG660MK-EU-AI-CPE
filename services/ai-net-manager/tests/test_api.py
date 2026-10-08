"""只读 API 冒烟测试（设计 12 章）。"""
import json
import threading
import urllib.request

from ai_net.api import StatusAPI, _tail_jsonl
from test_service import FakeTransport, make_service


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read().decode())


def test_api_endpoints(tmp_path):
    svc, clk = make_service(tmp_path, FakeTransport())
    svc.setup()
    svc.sample_radio(clk())
    svc.sample_qoe(clk())
    svc.decide(clk())
    api = StatusAPI(svc, "127.0.0.1", 0)          # 端口 0 = 随机空闲端口
    assert api.start(), "API 应能绑定"
    port = api.httpd.server_address[1]
    try:
        st, health = _get("http://127.0.0.1:%d/health" % port)
        assert st == 200 and health["ok"] and health["mode"] == "shadow"
        st, snap = _get("http://127.0.0.1:%d/status" % port)
        assert st == 200 and snap["radio"]["cell_id"]
        st, dec = _get("http://127.0.0.1:%d/decisions?n=10" % port)
        assert st == 200 and isinstance(dec["decisions"], list)
        try:
            _get("http://127.0.0.1:%d/nope" % port)
            raise AssertionError("404 expected")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        api.stop()
        svc.shutdown()


def test_tail_jsonl_tolerates_partial_last_line(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a":1}\n{"a":2}\n{"a":3', encoding="utf-8")   # 末行截断
    got = _tail_jsonl(str(p), 10)
    assert [g["a"] for g in got] == [1, 2]
