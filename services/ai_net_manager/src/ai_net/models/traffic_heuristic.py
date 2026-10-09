# -*- coding: utf-8 -*-
"""[PHASE1-STUB] 业务流启发式分类（设计 §15.6 的低风险过渡形态）。
透明规则 + 置信度封顶 0.6；低于阈值一律 generic（设计：不许硬猜）。
LightGBM 训练完成后由 models/traffic_lgbm.py 替换（接口保持一致）。"""
from schemas import TrafficPrediction

CLASSES = ["gaming", "video", "conference", "voip", "camera", "backup"]


class TrafficHeuristic:
    source = "heuristic"

    def predict(self, f):
        dl = f.get("dl_mbps_p50")
        if dl is None and f.get("flow_dl_kbps") is not None:
            dl = f["flow_dl_kbps"] / 1000.0
        dl = dl if dl is not None else 0.0
        ul = f.get("ul_mbps_p50")
        if ul is None and f.get("flow_ul_kbps") is not None:
            ul = f["flow_ul_kbps"] / 1000.0
        ul = ul if ul is not None else 0.0
        rtt = f.get("rtt_p50")
        rtt = rtt if rtt is not None else 999.0
        loss = f.get("loss_rate") or 0.0

        scores = {}
        # 上行持续视频（摄像头类：UL 显著主导）
        if ul > 1.0 and ul > 2.0 * max(dl, 0.05):
            scores["camera"] = 0.55
        # 大下行持续流（视频播放）
        if dl > 4.0 and dl > 1.5 * ul and rtt < 200:
            scores["video"] = 0.5
        # 双向中速 + 低时延（会议）
        if dl > 0.8 and ul > 0.8 and rtt < 90:
            scores["conference"] = 0.45
        # 低码率周期性（VoIP 近似：低速率低频次）
        if 0.02 <= dl <= 0.3 and ul <= 0.3 and rtt < 150:
            scores["voip"] = 0.35
        # 大流量非实时（备份/下载）
        if dl > 15.0 and rtt > 40:
            scores["backup"] = 0.5
        # 小包高频低吞吐 + 低时延（游戏近似）
        if rtt < 60 and 0.1 < dl < 3.0 and (f.get("active_flows") or 0) >= 2 and loss < 0.01:
            scores["gaming"] = 0.4

        if not scores:
            return TrafficPrediction(cls="generic", confidence=0.0, distribution={}, source=self.source)
        best = max(scores, key=scores.get)
        conf = min(scores[best], 0.6)
        if conf < 0.5:
            return TrafficPrediction(cls="generic", confidence=0.0, distribution=scores, source=self.source)
        dist = {c: round(scores.get(c, 0.0), 3) for c in CLASSES}
        return TrafficPrediction(cls=best, confidence=round(conf, 3), distribution=dist, source=self.source)
