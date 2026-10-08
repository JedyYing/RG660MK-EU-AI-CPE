"""FlowTracker 差分 + 启发式业务分类（设计 15.4 / 5.1）。"""
from ai_net.collectors.traffic_flow import FlowTracker, parse_conntrack_line
from ai_net.models.traffic import HeuristicTrafficClassifier

LINE = ("ipv4     2 udp      17 29 src=192.168.1.100 dst=223.5.5.5 "
        "sport=51000 dport=3478 packets=10 bytes=1200 "
        "src=223.5.5.5 dst=192.168.1.100 sport=3478 dport=51000 "
        "packets=12 bytes=1500 mark=0 use=1")


def test_parse_conntrack_line():
    f = parse_conntrack_line(LINE)
    assert f.proto == "udp" and f.src == "192.168.1.100" and f.dport == 3478
    assert f.o_bytes == 1200 and f.r_bytes == 1500


def test_parse_rejects_non_conntrack():
    assert parse_conntrack_line("tcp 1 2 3") is None


def _line(o_bytes, r_bytes, o_pkts=10, r_pkts=10, proto="udp", dport=3478):
    return ("ipv4     2 %s      17 29 src=192.168.1.100 dst=8.8.8.8 sport=51000 "
            "dport=%d packets=%d bytes=%d src=8.8.8.8 dst=192.168.1.100 "
            "sport=%d dport=51000 packets=%d bytes=%d mark=0 use=1"
            % (proto, dport, o_pkts, o_bytes, dport, r_pkts, r_bytes))


def test_flow_tracker_diff_direction_and_rates():
    ft = FlowTracker()
    first = ft.update([_line(1000, 2000)], ts=100.0)
    assert first["n_active"] == 1 and first["new_flows"] == 1
    second = ft.update([_line(4000, 10000, 40, 90)], ts=102.0)
    # 私有源 = 上行：orig 增量 3000B/2s
    assert second["ul_bytes"] == 3000 and second["dl_bytes"] == 8000
    assert second["ul_mbps"] == 0.012 and second["dl_mbps"] == 0.032
    assert second["small_pkt_ratio"] == 1.0           # 平均包小


def test_tracker_counter_reset_does_not_go_negative():
    ft = FlowTracker()
    ft.update([_line(100000, 200000)], ts=1.0)
    w = ft.update([_line(500, 600)], ts=2.0)          # 计数器回绕/重建
    assert w["ul_bytes"] == 0 and w["dl_bytes"] == 0


def _ff(**kw):
    base = {"n_active": 5, "dl_mbps": 0.1, "ul_mbps": 0.1, "ul_ratio": 0.5,
            "udp_ratio": 0.8, "tcp_ratio": 0.2, "pkt_size_p50": 120,
            "small_pkt_ratio": 0.8, "new_flow_ratio": 0.2, "top_ports": []}
    base.update(kw)
    return base


def test_classifier_no_features_is_generic_low_conf():
    p = HeuristicTrafficClassifier().predict({}, 0)
    assert p.cls == "generic" and p.confidence == 0.0 and "flow_features" in p.missing


def test_classifier_video_and_backup():
    c = HeuristicTrafficClassifier()
    p = c.predict(_ff(dl_mbps=8.0, ul_mbps=0.3, ul_ratio=0.04, tcp_ratio=0.9,
                      udp_ratio=0.1, pkt_size_p50=900, small_pkt_ratio=0.1), 0)
    assert p.cls == "video" and p.confidence >= 0.6
    p2 = c.predict(_ff(dl_mbps=60.0, ul_mbps=2.0, ul_ratio=0.03, new_flow_ratio=0.05,
                       pkt_size_p50=1200, small_pkt_ratio=0.05), 0)
    assert p2.cls == "backup"


def test_classifier_low_confidence_falls_back_to_generic():
    # 两个类得分接近 → 置信度低 → generic（由 RuleEngine 再回落 profile）
    p = HeuristicTrafficClassifier().predict(
        _ff(dl_mbps=0.4, ul_mbps=0.4, ul_ratio=0.5, udp_ratio=0.5, tcp_ratio=0.5,
            pkt_size_p50=250, small_pkt_ratio=0.6, top_ports=[("udp/3478", 3)]), 0)
    assert p.confidence < 0.9
    assert p.cls in ("generic", "conference", "voip", "gaming")
