"""FairFace 评测：验证它对亚洲人脸的年龄段判别力

对照 InsightFace genderage（已证明对亚洲人脸恒输出 ~55 岁）：
  西方人脸  Lena ~25 → 31.3 / 梅西 ~20 → 26.9   （有区分）
  亚洲人脸  你 50 → 55.3 / 小伙子 32 → 55.3      （无区分）

FairFace 预期：9 档年龄段（0-2/3-9/10-19/20-29/30-39/40-49/50-59/60-69/70+）
关键看：32 岁是否落 30-39、50 岁是否落 50-59
"""
import os
import cv2
import numpy as np
import onnxruntime as ort

BASE = os.path.expanduser("~/models/face_attr")
AGE = ["0-2", "3-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70+"]
GENDER = ["Male", "Female"]
RACE = ["White", "Black", "Latino_Hispanic", "East Asian", "Southeast Asian", "Indian", "Middle Eastern"]

sess = ort.InferenceSession(os.path.join(BASE, "fairface.onnx"), providers=["CPUExecutionProvider"])
iname = sess.get_inputs()[0].name
outs = [o.name for o in sess.get_outputs()]
print("输入:", iname, sess.get_inputs()[0].shape)
print("输出:", outs)


def softmax(x):
    e = np.exp(x - x.max())
    return e / e.sum()


def predict(face_bgr):
    """face_bgr: 已裁剪的人脸（BGR）"""
    img = cv2.resize(face_bgr, (224, 224))
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)
    img = (img - mean) / std
    blob = np.ascontiguousarray(img.transpose(2, 0, 1))[None]
    out = sess.run(None, {iname: blob})
    # 输出顺序需确认：race, gender, age
    res = {}
    for name, o in zip(outs, out):
        p = softmax(o[0])
        n = name.lower()
        if "age" in n:
            res["age"] = AGE[int(p.argmax())]
            res["age_conf"] = float(p.max())
            res["age_probs"] = p
        elif "gender" in n:
            res["gender"] = GENDER[int(p.argmax())]
            res["gender_conf"] = float(p.max())
        elif "race" in n:
            res["race"] = RACE[int(p.argmax())]
            res["race_conf"] = float(p.max())
        else:
            # 无命名信息时按维度猜
            if o.shape[-1] == 9:
                res["age"] = AGE[int(p.argmax())]; res["age_conf"] = float(p.max())
            elif o.shape[-1] == 2:
                res["gender"] = GENDER[int(p.argmax())]; res["gender_conf"] = float(p.max())
            elif o.shape[-1] == 7:
                res["race"] = RACE[int(p.argmax())]; res["race_conf"] = float(p.max())
    return res


if __name__ == "__main__":
    import sys
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
    files = sys.argv[1:] or ["/tmp/lena.jpg", "/tmp/messi5.jpg"]
    for f in files:
        img = cv2.imread(f)
        if img is None:
            print(f"{f}: 读取失败"); continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = casc.detectMultiScale(g, 1.08, 5, minSize=(40, 40))
        if not len(faces):
            print(f"{os.path.basename(f)}: 未检出人脸"); continue
        x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
        p = int(0.25 * w)
        crop = img[max(0, y - p):y + h + p, max(0, x - p):x + w + p]
        r = predict(crop)
        print(f"{os.path.basename(f):<20} age={r.get('age')} ({r.get('age_conf',0):.3f})  "
              f"gender={r.get('gender')} ({r.get('gender_conf',0):.3f})  "
              f"race={r.get('race')} ({r.get('race_conf',0):.3f})")
