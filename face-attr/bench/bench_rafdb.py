"""RAF-DB 真实场景测试集：端到端准确率（检测→对齐→双模型→融合）"""
import os, time
import numpy as np, cv2, pandas as pd, onnxruntime as ort
from collections import defaultdict

BASE = os.path.expanduser("~/models/face_attr")
sv = ort.InferenceSession(os.path.join(BASE, "vit_affectnet/vit_affectnet.onnx"), providers=["CPUExecutionProvider"])
sf = ort.InferenceSession(os.path.join(BASE, "emotion-ferplus-8.onnx"), providers=["CPUExecutionProvider"])
yn = cv2.FaceDetectorYN.create(os.path.join(BASE, "yunet.onnx"), " ", (320, 320), 0.45, 0.3, 5000)

LV = ["anger", "contempt", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
LF = ["neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"]
# RAF-DB: 1=Surprise 2=Fear 3=Disgust 4=Happiness 5=Sadness 6=Anger 7=Neutral
RAF = {1: "surprise", 2: "fear", 3: "disgust", 4: "happy", 5: "sad", 6: "angry", 7: "neutral"}
CANON = {"happiness": "happy", "sadness": "sad", "anger": "angry"}
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def align(img, lm, size=112):
    src = np.array([lm[1], lm[0], lm[2], lm[4], lm[3]], dtype=np.float32)
    M, _ = cv2.estimateAffinePartial2D(src, TEMPLATE * (size / 112.0), method=cv2.LMEDS)
    if M is None: return None
    return cv2.warpAffine(img, M, (size, size), flags=cv2.INTER_LINEAR, borderValue=0)


def run_vit(face):
    im = cv2.cvtColor(cv2.resize(face, (224, 224)), cv2.COLOR_BGR2RGB).astype(np.float32)
    im = (im / 255.0 - 0.5) / 0.5
    b = np.ascontiguousarray(im.transpose(2, 0, 1))[None]
    v = sv.run(None, {sv.get_inputs()[0].name: b})[0].ravel()
    e = np.exp(v - v.max()); e /= e.sum()
    return LV[int(e.argmax())], float(e.max())


def run_fer(face):
    g = cv2.cvtColor(cv2.resize(face, (64, 64)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    v = sf.run(None, {sf.get_inputs()[0].name: g[None, None]})[0].ravel()
    e = np.exp(v - v.max()); e /= e.sum()
    return LF[int(e.argmax())], float(e.max())


df = pd.read_parquet(os.path.join(BASE, "raf_test.parquet"))
N = 80
buckets = defaultdict(list)
for _, row in df.iterrows():
    lb = int(row["label"])
    if lb in RAF and len(buckets[lb]) < N:
        buckets[lb].append(row["image"]["bytes"])

print(f"抽样: 每类 {N} 张, 实际 {sum(len(v) for v in buckets.values())} 张\n")
print(f"{'真实':<10}{'样本':<6}{'ViT':<14}{'FER+':<14}{'融合D':<14}{'未检出':<8}")
print("-" * 66)

stats = {k: [0, 0] for k in ("vit", "fer", "fus")}
nodet = 0
total = 0
t0 = time.time()
for lb in sorted(buckets):
    truth = RAF[lb]
    c = {"vit": 0, "fer": 0, "fus": 0}
    nd = 0
    n = 0
    for blob in buckets[lb]:
        img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
        if img is None: continue
        h, w = img.shape[:2]
        yn.setInputSize((w, h))
        _, faces = yn.detect(img)
        if faces is None or not len(faces):
            nd += 1; continue
        f = max(faces, key=lambda r: r[2] * r[3])
        lm = f[4:14].reshape(5, 2).astype(np.float32)
        face = align(img, lm, 224)
        if face is None: nd += 1; continue
        n += 1
        pv, sv_ = run_vit(face)
        pf, sfl = run_fer(face)
        fv = "neutral" if sfl > 0.90 else pv          # 融合 A 用 >0.90；D 见下
        pd_ = pv if sv_ >= sfl else pf                # 融合 D
        if CANON.get(pv, pv) == truth: c["vit"] += 1
        if CANON.get(pf, pf) == truth: c["fer"] += 1
        if CANON.get(pd_, pd_) == truth: c["fus"] += 1
    total += n; nodet += nd
    for k in c: stats[k][0] += c[k]; stats[k][1] += n
    print(f"{truth:<10}{n:<6}{c['vit']}/{n} ({c['vit']/max(n,1)*100:3.0f}%){'':<4}"
          f"{c['fer']}/{n} ({c['fer']/max(n,1)*100:3.0f}%){'':<4}"
          f"{c['fus']}/{n} ({c['fus']/max(n,1)*100:3.0f}%){'':<4}{nd}")

print("-" * 66)
for k, name in (("vit", "仅 ViT"), ("fer", "仅 FER+"), ("fus", "融合D")):
    a, b = stats[k]
    print(f"{name:<10}{a}/{b} = {a/max(b,1)*100:.1f}%")
print(f"\n未检出 {nodet} 张（检测器能力限制，不计入准确率）")
print(f"总耗时 {time.time()-t0:.0f}s, 平均 {(time.time()-t0)/max(total,1)*1000:.0f} ms/张")
