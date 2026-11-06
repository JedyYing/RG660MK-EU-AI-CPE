"""融合 FER+ 的 neutral 判断 + ViT 的负面情绪判别"""
import pickle, os, time
import numpy as np, cv2, onnxruntime as ort
from collections import Counter, defaultdict

BASE = os.path.expanduser("~/models/face_attr")
sv = ort.InferenceSession(os.path.join(BASE, "vit_affectnet/vit_affectnet.onnx"), providers=["CPUExecutionProvider"])
sf = ort.InferenceSession(os.path.join(BASE, "emotion-ferplus-8.onnx"), providers=["CPUExecutionProvider"])
LV = ["anger", "contempt", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
LF = ["neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"]
FER = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]
MAP_V = {"anger": "angry", "disgust": "disgust", "fear": "fear", "happy": "happy",
         "sad": "sad", "surprise": "surprise", "neutral": "neutral", "contempt": None}
MAP_F = {"anger": "angry", "disgust": "disgust", "fear": "fear", "happiness": "happy",
         "sadness": "sad", "surprise": "surprise", "neutral": "neutral", "contempt": None}


def both(img):
    im = cv2.cvtColor(cv2.resize(img, (224, 224)), cv2.COLOR_BGR2RGB).astype(np.float32)
    im = (im / 255.0 - 0.5) / 0.5
    b = np.ascontiguousarray(im.transpose(2, 0, 1))[None]
    v = sv.run(None, {sv.get_inputs()[0].name: b})[0].ravel()
    ev = np.exp(v - v.max()); ev /= ev.sum()
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.resize(g, (64, 64)).astype(np.float32)
    v2 = sf.run(None, {sf.get_inputs()[0].name: g[None, None]})[0].ravel()
    ef = np.exp(v2 - v2.max()); ef /= ef.sum()
    return ev, ef


with open(os.path.join(BASE, "fer_test.pt"), "rb") as f:
    data = pickle.load(f, encoding="latin1")

N = 100
buckets = defaultdict(list)
for d in data:
    lb = d["labels"]
    if lb in FER and len(buckets[lb]) < N:
        buckets[lb].append(d["img_bytes"])

strategies = {
    "仅 ViT": lambda ev, ef: LV[int(ev.argmax())],
    "仅 FER+": lambda ev, ef: LF[int(ef.argmax())],
    "融合A: FER+ neutral>0.90 优先": lambda ev, ef: "neutral" if ef[0] > 0.90 else LV[int(ev.argmax())],
    "融合B: FER+ neutral>0.70 优先": lambda ev, ef: "neutral" if ef[0] > 0.70 else LV[int(ev.argmax())],
    "融合C: ViT说neutral则改用FER+": lambda ev, ef: LF[int(ef.argmax())] if LV[int(ev.argmax())] == "neutral" else LV[int(ev.argmax())],
    "融合D: 两模型最大概率取大者": lambda ev, ef: (LV[int(ev.argmax())] if ev.max() >= ef.max() else LF[int(ef.argmax())]),
}
MAPS = {"仅 ViT": MAP_V, "仅 FER+": MAP_F,
        "融合A: FER+ neutral>0.90 优先": None, "融合B: FER+ neutral>0.70 优先": None,
        "融合C: ViT说neutral则改用FER+": None, "融合D: 两模型最大概率取大者": None}


def canon(label):
    if label is None: return None
    return {"happiness": "happy", "sadness": "sad", "anger": "angry"}.get(label, label)


res = {k: [0, 0] for k in strategies}
per_class = {k: defaultdict(lambda: [0, 0]) for k in strategies}
t0 = time.time()
for truth in FER:
    if truth not in buckets: continue
    for blob in buckets[truth]:
        img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
        if img is None: continue
        ev, ef = both(img)
        for name, fn in strategies.items():
            pred = canon(fn(ev, ef))
            ok = (pred == truth)
            res[name][0] += ok
            res[name][1] += 1
            per_class[name][truth][0] += ok
            per_class[name][truth][1] += 1

print(f"{'策略':<34}{'总准确率':<14}{'neutral':<10}{'sad':<8}{'angry':<8}{'happy':<8}")
print("-" * 82)
for name in strategies:
    ok, tot = res[name]
    pc = per_class[name]
    def pct(k):
        a, b = pc[k]
        return f"{a/b*100:.0f}%" if b else "-"
    print(f"{name:<34}{ok}/{tot} ({ok/tot*100:4.1f}%)  {pct('neutral'):<10}{pct('sad'):<8}{pct('angry'):<8}{pct('happy'):<8}")
print(f"\n双模型跑 700 张总耗时 {time.time()-t0:.0f}s")
