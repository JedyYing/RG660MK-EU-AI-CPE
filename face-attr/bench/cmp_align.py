"""对齐 vs 不对齐，对情绪模型精度的影响对比"""
import cv2, numpy as np, onnxruntime as ort, os, glob

V = os.path.expanduser("~/models/face_attr/vit_affectnet/vit_affectnet.onnx")
F = os.path.expanduser("~/models/face_attr/emotion-ferplus-8.onnx")
sv = ort.InferenceSession(V, providers=["CPUExecutionProvider"])
sf = ort.InferenceSession(F, providers=["CPUExecutionProvider"])
LV = ["anger", "contempt", "disgust", "fear", "happy", "neutral", "sad", "surprise"]
LF = ["neutral", "happiness", "surprise", "sadness", "anger", "disgust", "fear", "contempt"]
CN = {"anger": "生气", "contempt": "轻蔑", "disgust": "厌恶", "fear": "恐惧", "happy": "高兴",
      "neutral": "平静", "sad": "难过", "surprise": "惊讶", "happiness": "高兴", "sadness": "难过"}

yn = cv2.FaceDetectorYN.create(os.path.expanduser("~/models/face_attr/yunet.onnx"), " ", (320, 320), 0.45, 0.3, 5000)

# ArcFace 标准 5 点模板（112x112）
TEMPLATE = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def align_face(img, face_row, size=112):
    """YuNet 关键点顺序: 右眼(0:2) 左眼(2:4) 鼻尖(4:6) 右嘴角(6:8) 左嘴角(8:10)"""
    lm = face_row[4:14].reshape(5, 2).astype(np.float32)
    src = np.array([lm[1], lm[0], lm[2], lm[4], lm[3]], dtype=np.float32)
    dst = TEMPLATE * (size / 112.0)
    M, _ = cv2.estimateAffinePartial2D(src, dst, method=cv2.LMEDS)
    if M is None:
        raise RuntimeError("align failed")
    return cv2.warpAffine(img, M, (size, size), flags=cv2.INTER_LINEAR, borderValue=0)


def crop_bbox(img, face_row, pad_ratio=0.15):
    x, y, w, h = [int(v) for v in face_row[:4]]
    p = int(pad_ratio * w)
    return img[max(0, y - p):y + h + p, max(0, x - p):x + w + p]


def vit_predict(face_bgr):
    img = cv2.cvtColor(cv2.resize(face_bgr, (224, 224)), cv2.COLOR_BGR2RGB).astype(np.float32)
    img = (img / 255.0 - 0.5) / 0.5
    b = np.ascontiguousarray(img.transpose(2, 0, 1))[None]
    v = sv.run(None, {sv.get_inputs()[0].name: b})[0].ravel()
    e = np.exp(v - v.max()); e /= e.sum(); i = int(e.argmax())
    return LV[i], float(e[i])


def fer_predict(face_bgr):
    g = cv2.cvtColor(cv2.resize(face_bgr, (64, 64)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    v = sf.run(None, {sf.get_inputs()[0].name: g[None, None]})[0].ravel()
    e = np.exp(v - v.max()); e /= e.sum(); i = int(e.argmax())
    return LF[i], float(e[i])


SEG = {1: "平静", 3: "微笑", 5: "皱眉", 7: "惊讶"}
bench = os.path.expanduser("~/models/face_attr/emotion_bench")

print(f"{'帧':<8}{'你做的':<7}{'ViT+对齐':<16}{'ViT+bbox':<16}{'FER+对齐':<16}{'FER+bbox':<14}")
print("-" * 82)
hit_a = hit_b = 0
n = 0
for f in sorted(glob.glob(os.path.join(bench, "f*.jpg"))):
    i = int(os.path.basename(f)[1:3])
    st = SEG.get(i - (i - 1) % 2, "?")
    img = cv2.imread(f)
    h, w = img.shape[:2]
    yn.setInputSize((w, h))
    _, fc = yn.detect(img)
    if fc is None or not len(fc):
        print(f"{os.path.basename(f):<8}{st:<7}未检出"); continue
    row = fc[0]
    try:
        al = align_face(img, row, 224)
        la, ca = vit_predict(al)
        fa, cfa = fer_predict(al)
    except Exception as e:
        la, ca, fa, cfa = "err", 0, "err", 0
    bb = crop_bbox(img, row)
    lb, cb = vit_predict(bb)
    fb, cfb = fer_predict(bb)
    n += 1
    mark = ""
    if st == "微笑" and la == "happy": mark = " ✓"; hit_a += 1
    if st == "惊讶" and la == "surprise": mark += " ✓"
    print(f"{os.path.basename(f):<8}{st:<7}{CN.get(la,la)+f' {ca:.2f}':<16}{CN.get(lb,lb)+f' {cb:.2f}':<16}"
          f"{CN.get(fa,fa)+f' {cfa:.2f}':<16}{CN.get(fb,fb)+f' {cfb:.2f}':<14}{mark}")

print(f"\nViT+对齐 命中关键表情: {hit_a}/{n}")
