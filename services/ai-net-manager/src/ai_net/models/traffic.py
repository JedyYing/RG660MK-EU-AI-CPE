"""业务分类：启发式分类器（Phase 1 端侧保底）+ 训练模型接入点。

设计 5.1：首版优先 LightGBM，训练在 PC；端侧支持纯 Python 推理。
在模型产物就绪前，本启发式分类器保证 six-class 管线完整可跑；
置信度刻意保守（<=0.75），低置信度由 RuleEngine 回落 generic（设计 7.2）。
"""
from __future__ import annotations

from schemas import TrafficPrediction

CLASSES = ("gaming", "video", "conference", "voip", "camera", "backup")

# 模型输入特征顺序（训练工具与端侧推理必须一致；缺一不可）
FLOW_FEATURE_ORDER = ("dl_mbps", "ul_mbps", "ul_ratio", "udp_ratio", "tcp_ratio",
                      "pkt_size_p50", "small_pkt_ratio", "new_flow_ratio",
                      "n_active", "rtc_port", "small_pkt_udp")

# RTP/STUN/SIP 常用端口（conference/voip 特征）
RTC_PORTS = {3478, 3479, 3480, 3481, 5060, 5061, 19302, 19305, 19307, 19308, 19309}


def _rtc_flag(ff: dict) -> int | None:
    ports = set()
    for entry in ff.get("top_ports") or []:
        try:
            ports.add(int(str(entry[0]).split("/")[1]))
        except (ValueError, IndexError):
            continue
    return 1 if (ports & RTC_PORTS) else 0


def flow_features(ff: dict) -> tuple:
    """FlowTracker 窗口 → 模型特征（None 保持缺失，绝不填 0）。

    返回 (features, missing)；missing 非空时调用方不得喂模型。
    """
    ff = ff or {}
    feat = {k: ff.get(k) for k in FLOW_FEATURE_ORDER}
    feat["rtc_port"] = _rtc_flag(ff)
    small = ff.get("small_pkt_ratio")
    udp = ff.get("udp_ratio")
    feat["small_pkt_udp"] = None if (small is None or udp is None) else round(small * udp, 3)
    missing = sorted(k for k, v in feat.items() if v is None)
    return feat, missing


def _score_classes(ff: dict) -> dict:
    """输入 FlowTracker 窗口特征，输出六类原始分数（未归一）。"""
    s = {c: 0.0 for c in CLASSES}
    if not ff or not ff.get("n_active"):
        return s
    dl = ff.get("dl_mbps") or 0.0
    ul = ff.get("ul_mbps") or 0.0
    ul_ratio = ff.get("ul_ratio")
    udp_ratio = ff.get("udp_ratio") or 0.0
    tcp_ratio = ff.get("tcp_ratio") or 0.0
    p50 = ff.get("pkt_size_p50") or 0.0
    small = ff.get("small_pkt_ratio")
    new_ratio = ff.get("new_flow_ratio") or 0.0
    rtc = bool(_rtc_flag(ff))

    total = dl + ul
    # camera：上行主导、持续
    if ul_ratio is not None and ul_ratio > 0.55 and ul > 0.5:
        s["camera"] += 2.0 * min(1.0, ul_ratio)
        if ul > 2.0:
            s["camera"] += 0.5
    # video：下行大流量、包较大
    if dl > 1.0 and (ul_ratio is None or ul_ratio < 0.4):
        s["video"] += 1.5
        if p50 > 400:
            s["video"] += 0.5
    # backup：极大下行 + 少量新流（长连接大文件）
    if dl > 20.0 and new_ratio < 0.3:
        s["backup"] += 2.0 + min(1.0, dl / 100.0)
    # gaming：小包双向、低吞吐、UDP 多
    if small is not None and small > 0.5 and total < 2.0 and udp_ratio > 0.3:
        s["gaming"] += 1.5
        if 0.2 <= (ul_ratio or 0) <= 0.8:
            s["gaming"] += 0.5
    # voip：低码率、小包、RTC 端口
    if total < 0.6 and small is not None and small > 0.6:
        s["voip"] += 1.2
        if rtc:
            s["voip"] += 0.8
    # conference：双向实时、RTC 端口、中等码率
    if rtc and 0.2 <= (ul_ratio or 0) <= 0.8 and total > 0.2:
        s["conference"] += 1.6
    if tcp_ratio > 0.9 and total > 5.0 and udp_ratio < 0.1:
        s["conference"] = max(0.0, s["conference"] - 0.5)
    return s


class HeuristicTrafficClassifier:
    model_version = "heuristic-v1"

    def predict(self, ff: dict, ts_ms: int) -> TrafficPrediction:
        s = _score_classes(ff or {})
        tot = sum(s.values())
        if tot <= 0:
            return TrafficPrediction(ts_ms=ts_ms, cls="generic", confidence=0.0,
                                     distribution={c: 0.0 for c in CLASSES},
                                     model_version=self.model_version,
                                     missing=["flow_features"])
        dist = {c: round(s[c] / tot, 3) for c in CLASSES}
        top = max(dist.items(), key=lambda x: x[1])
        second = sorted(dist.values(), reverse=True)[1] if len(dist) > 1 else 0.0
        margin = top[1] - second
        conf = round(min(0.75, 0.2 + 1.5 * margin + 0.55 * top[1]), 3)
        cls = top[0] if conf >= 0.6 else "generic"
        return TrafficPrediction(ts_ms=ts_ms, cls=cls, confidence=conf,
                                 distribution=dist, model_version=self.model_version)


def make_classifier(model_doc: dict | None, unknown_threshold: float = 0.6):
    """按模型可用性选择分类器：有校验通过的 GBDT 模型用模型，否则启发式。"""
    if not model_doc:
        return HeuristicTrafficClassifier()
    try:
        return ModelTrafficClassifier(model_doc, unknown_threshold)
    except (KeyError, ValueError):
        return HeuristicTrafficClassifier()


class ModelTrafficClassifier:
    """GBDT 模型包装（设计 5.1：训练在 PC、推理纯 Python、缺特征不预测）。"""

    def __init__(self, model_doc: dict, unknown_threshold: float = 0.6):
        from . import gbdt
        if model_doc.get("model_type") != "gbdt_multiclass":
            raise ValueError("unsupported model_type: %s" % model_doc.get("model_type"))
        if tuple(model_doc.get("feature_order") or ()) != FLOW_FEATURE_ORDER:
            raise ValueError("feature_order mismatch")
        self._gbdt = gbdt
        self.model = model_doc
        self.unknown_threshold = unknown_threshold
        self.model_version = model_doc.get("version") or ("gbdt-%s"
                                                          % (model_doc.get("sha256") or "?")[:8])

    def predict(self, ff: dict, ts_ms: int) -> TrafficPrediction:
        _feat, missing = flow_features(ff)
        if missing:
            return TrafficPrediction(ts_ms=ts_ms, cls="generic", confidence=0.0,
                                     distribution={c: 0.0 for c in CLASSES},
                                     model_version=self.model_version,
                                     missing=["flow_features:" + ",".join(missing)])
        cls, conf, dist = self._gbdt.predict(self.model, _feat)
        if conf < self.unknown_threshold:
            cls = "generic"
        return TrafficPrediction(ts_ms=ts_ms, cls=cls, confidence=conf,
                                 distribution=dist, model_version=self.model_version)
