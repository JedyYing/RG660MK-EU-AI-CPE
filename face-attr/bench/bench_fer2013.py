"""在 FER2013 官方测试集上客观对比 FER+ 与 ViT-AffectNet"""
import pickle, os, time
import numpy as np, cv2, onnxruntime as ort

BASE = os.path.expanduser("~/models/face_attr")
sv = ort.InferenceSession(os.path.join(BASE, "vit_affectnet/vit_affectnet.onnx"), providers=["CPUExecutionProvider"])
sf = ort.InferenceSession(os.path.join(BASE, "emotion-ferplus-8.onnx"), providers=["CPUExecutionProvider"])

LV = ["anger", "contempt", "disgust", "fear", "happy", "neutral", "sad", "surprise"]      # AffectNet 8 类
LF = ["neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"]  # FER+ 8 类
FER = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]                    # FER2013 7 类

# 映射到 FER2013 的 7 类
MAP_V = {"anger": "angry", "disgust": "disgust", "fear": "fear", "happy": "happy",
         "sad": "sad", "surprise": "surprise", "neutral": "neutral", "contempt": None}
MAP_F = {"anger": "angry", "disgust": "disgust", "fear": "fear", "happiness": "happy",
         "sadness": "sad", "surprise": "surprise", "neutral": "neutral", "contempt": None}


def vit(img_bgr):
    im = cv2.cvtColor(cv2.resize(img_bgr, (224, 224)), cv2.COLOR_BGR2RGB).astype(np.float32)
    im = (im / 255.0 - 0.5) / 0.5
    b = np.ascontiguousarray(im.transpose(2, 0, 1))[None]
    v = sv.run(None, {sv.get_inputs()[0].name: b})[0].ravel()
    e = np.exp(v - v.max()); e /= e.sum()
    return LV[int(e.argmax())], float(e.max())


def fer(img_bgr):
    g = cv2.cvtColor(cv2.resize(img_bgr, (64, 64)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    v = sf.run(None, {sf.get_inputs()[0].name: g[None, None]})[0].ravel()
    e = np.exp(v - v.max()); e /= e.sum()
    return LF[int(e.argmax())], float(e.max())


with open(os.path.join(BASE, "fer_test.pt"), "rb") as f:
    data = pickle.load(f, encoding="latin1")

# 每类取前 N 张（数据按类顺序排列，先抽样统计分布）
from collections import Counter, defaultdict
dist = Counter(d["labels"] for d in data)
print("测试集分布:", dict(sorted(dist.items())))

N = 100
buckets = defaultdict(list)
for d in data:
    lb = d["labels"]
    if lb in FER and len(buckets[lb]) < N:
        buckets[lb].append(d["img_bytes"])

print(f"\n每类取 {N} 张，共 {sum(len(v) for v in buckets.values())} 张\n")
hdr = f"{'真实类别':<10}{'样本':<6}{'ViT':<22}{'FER+':<22}"
print(hdr); print("-" * 62)

ok_v = ok_f = total = 0
t_v = t_f = 0.0
for truth in FER:
    if truth not in buckets: continue
    cv_ = cf_ = 0
    n = 0
    for blob in buckets[truth]:
        arr = np.frombuffer(blob, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            continue
        n += 1
        t0 = time.time(); pv, scv = vit(img); t_v += (time.time() - t0) * 1000
        t0 = time.time(); pf, scf = fer(img); t_f += (time.time() - t0) * 1000
        if MAP_V.get(pv) == truth: cv_ += 1
        if MAP_F.get(pf) == truth: cf_ += 1
    total += n; ok_v += cv_; ok_f += cf_
    print(f"{truth:<10}{n:<6}{cv_}/{n} ({cv_/n*100:4.1f}%){'':<8}{cf_}/{n} ({cf_/n*100:4.1f}%)")

print("-" * 62)
print(f"{'合计':<10}{total:<6}{ok_v}/{total} ({ok_v/total*100:4.1f}%)   {ok_f}/{total} ({ok_f/total*100:4.1f}%)")
print(f"\n平均耗时: ViT {t_v/total:.0f} ms/张   FER+ {t_f/total:.1f} ms/张")
