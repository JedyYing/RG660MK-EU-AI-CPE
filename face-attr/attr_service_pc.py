#!/usr/bin/env python3
"""attr_service_pc.py v5 — 人脸性别识别（年龄与情绪经实测不具备交付条件，均已移除）

为什么去掉情绪（实测依据，2026-11-06）：
  在 1254 张带标注公开数据上测试（FER2013 700 张 + RAF-DB 554 张真实场景）：
    · FER+              实验室 49.6% / 真实场景 19.9%
    · ViT-AffectNet     实验室 56.0% / 真实场景 36.2%
    · 6 种融合策略最好   实验室 60.6% / 真实场景 28.6%
    · 随机猜 7 类 = 14.3%
  真实场景最好组合仅 36-40%，不具备交付条件，故移除。详见设计文档。

保留能力（实测可靠）：
  · 性别 —— 13/13 帧全部正确，置信度 0.97-0.999
  · 年龄段 —— 16 帧实测 5/5 正确（50 岁全部判"中年"）
  · 精确年龄 —— 实测系统性高估 +5.3 岁（真实 50 → 中位 55.6，范围 47.8-63.6），
                 不可靠，故不对外返回，仅内部计算段位

对 8099 的影响：
  · 推理 100% 在本机，不占用 RG660MK 的 CPU
  · 设备侧只多一次 HTTP GET /snapshot（camview 本来就在持续产帧，不额外打开摄像头）
  · 实测压测见 --bench 模式

用法:
  python3 attr_service_pc.py --port 8093                 # 起服务
  python3 attr_service_pc.py --bench 30                  # 压测：连续推理并记录 8099 响应
"""
import argparse, json, os, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import onnxruntime as ort

MODEL_DIR = os.path.expanduser("~/models/face_attr")
_sess = {}
_yunet = None
_yunet_lock = threading.Lock()
_state = {"calls": 0, "ok": 0, "fail": 0, "last_ms": None}
_lock = threading.Lock()

GENDER_MIN_CONF = 0.85   # 低于此值判定不可信（用户本人实测 0.94-0.999；非目标 0.52-0.70）
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def load_models():
    global _yunet
    ga = os.path.join(MODEL_DIR, "genderage.onnx")
    _sess["ga"] = ort.InferenceSession(ga, providers=["CPUExecutionProvider"]) if os.path.isfile(ga) else None
    yp = os.path.join(MODEL_DIR, "yunet.onnx")
    if os.path.isfile(yp):
        _yunet = cv2.FaceDetectorYN.create(yp, "", (320, 320), 0.45, 0.3, 5000)
    return {"genderage": _sess["ga"] is not None, "yunet": _yunet is not None}


def detect_faces(bgr, min_px=28):
    h, w = bgr.shape[:2]
    if _yunet is None:
        return []
    try:
        with _yunet_lock:
            _yunet.setInputSize((w, h))
            _, faces = _yunet.detect(bgr)
        if faces is None or not len(faces):
            return []
        out = []
        for f in faces:
            x, y, fw, fh = [int(round(v)) for v in f[:4]]
            if fw < min_px or fh < min_px:
                continue
            out.append(([max(0, x), max(0, y), fw, fh], f[4:14].reshape(5, 2).astype(np.float32)))
        return sorted(out, key=lambda t: -t[0][2] * t[0][3])
    except Exception:
        return []


def align_face(bgr, lm, size=224):
    src = np.array([lm[1], lm[0], lm[2], lm[4], lm[3]], dtype=np.float32)
    M, _ = cv2.estimateAffinePartial2D(src, TEMPLATE * (size / 112.0), method=cv2.LMEDS)
    if M is None:
        return None
    return cv2.warpAffine(bgr, M, (size, size), flags=cv2.INTER_LINEAR, borderValue=0)


def crop_bbox(bgr, box, pad=0.45):
    x, y, w, h = box
    p = int(pad * w)
    return bgr[max(0, y - p):y + h + p, max(0, x - p):x + w + p]


def pred_gender(face, bbox=None):
    """只输出性别。年龄已移除（2026-11-07 实测：两个模型在亚洲人脸上均无判别力，
    InsightFace 32岁/50岁都输出~50，FairFace 都输出30-39，详见 bench/results.md）"""
    s = _sess.get("ga")
    if s is None or face is None or face.size == 0:
        return {}
    img = cv2.cvtColor(cv2.resize(face, (96, 96)), cv2.COLOR_BGR2RGB).astype(np.float32)  # 0-255；模型内部归一化
    b = np.ascontiguousarray(img.transpose(2, 0, 1))[None]
    v = s.run(None, {s.get_inputs()[0].name: b})[0].ravel()
    g = np.exp(v[:2] - v[:2].max()); g /= g.sum()
    conf = float(max(g))
    if conf < GENDER_MIN_CONF:
        return {"gender": "uncertain", "gender_score": round(conf, 3),
                "note": "low_confidence_face"}
    try:
        with open("/tmp/attr_requests.log", "a") as lf:
            bb = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}" if bbox else "-"
            lf.write(f"{time.strftime('%F %T')}\tgender={'male' if g[1]>g[0] else 'female'}\tconf={conf:.3f}\tbbox={bb}\n")
    except Exception:
        pass
    return {"gender": "male" if g[1] > g[0] else "female",
            "gender_score": round(conf, 3), "note": None}


def analyse(bgr):
    t0 = time.time()
    out = []
    for box, lm in detect_faces(bgr):
        face = crop_bbox(bgr, box)
        item = {"bbox": box}
        item.update(pred_gender(face, box))
        out.append(item)
    return {"ok": True, "faces": out, "face_count": len(out),
            "elapsed_ms": int((time.time() - t0) * 1000), "capability": "gender"}


def grab(device, timeout=6):
    with urllib.request.urlopen(device.rstrip("/") + "/snapshot", timeout=timeout) as r:
        data = r.read()
    if len(data) < 1024:
        raise RuntimeError("snapshot too small")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("decode failed")
    return img


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    device = "http://192.168.1.1:8090"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "attr-pc", "version": 5,
                             "capability": ["gender"], "port": PORT,
                             "models": {k: v is not None for k, v in _sess.items()},
                             "yunet": _yunet is not None, "device": self.device, "stats": _state})
        elif self.path == "/metrics":
            self._send(200, dict(_state))
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path != "/vision/attribute":
            self._send(404, {"ok": False, "error": "not found"}); return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send(400, {"ok": False, "error": "bad json"}); return
        t0 = time.time()
        try:
            p = payload.get("image")
            if p and os.path.isfile(p):
                img = cv2.imread(p)
                if img is None: raise RuntimeError("image decode failed")
            else:
                img = grab(self.device, payload.get("timeout", 6))
            res = analyse(img)
            res["total_ms"] = int((time.time() - t0) * 1000)
            with _lock:
                _state["calls"] += 1; _state["ok"] += 1; _state["last_ms"] = res["total_ms"]
            self._send(200, res)
        except Exception as e:
            with _lock:
                _state["calls"] += 1; _state["fail"] += 1
            self._send(503, {"ok": False, "error": str(e)})


def bench(n, device):
    """压测：连续抓帧+推理，同时高频探测 8099"""
    import statistics
    http8099 = "http://192.168.1.1:8099/state"

    def probe():
        t0 = time.time()
        try:
            with urllib.request.urlopen(http8099, timeout=5) as r:
                r.read()
            return (time.time() - t0) * 1000
        except Exception:
            return None

    print("① 基线：只测 8099，不跑推理（10 次）")
    base = [probe() for _ in range(10)]
    base = [x for x in base if x]
    print(f"   中位 {statistics.median(base):.1f} ms  min {min(base):.1f}  max {max(base):.1f}")

    print(f"\n② 压测：连续 {n} 次「抓帧 + 推理」，每轮之间探测一次 8099")
    during = []
    attrs = []
    for i in range(n):
        t0 = time.time()
        try:
            img = grab(device)
            res = analyse(img)
            attrs.append((time.time() - t0) * 1000)
            m = probe()
            if m: during.append(m)
            print(f"   #{i+1:02d}  推理 {res['elapsed_ms']}ms  脸 {res['face_count']}  8099 {m:.1f}ms" if m else
                  f"   #{i+1:02d}  推理 {res['elapsed_ms']}ms  脸 {res['face_count']}  8099 超时")
        except Exception as e:
            print(f"   #{i+1:02d} 失败: {e}")
    print(f"\n③ 结论")
    print(f"   推理期间 8099 中位 {statistics.median(during):.1f} ms  min {min(during):.1f}  max {max(during):.1f}")
    delta = statistics.median(during) - statistics.median(base)
    print(f"   相对基线变化 {delta:+.1f} ms  ({delta/statistics.median(base)*100:+.1f}%)")
    print(f"   属性推理自身 中位 {statistics.median(attrs):.0f} ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8093)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--device", default="http://192.168.1.1:8090")
    ap.add_argument("--bench", type=int, default=0, help="压测模式：连续 N 次推理并记录 8099 响应")
    a = ap.parse_args()
    PORT = a.port
    Handler.device = a.device
    print(f"models: {load_models()}", flush=True)
    if a.bench:
        bench(a.bench, a.device)
    else:
        Handler.device = a.device
        print(f"attr_service_pc v5 on {a.host}:{PORT} (gender only)", flush=True)
        ThreadingHTTPServer((a.host, PORT), Handler).serve_forever()
