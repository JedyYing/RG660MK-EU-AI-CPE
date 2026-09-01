#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 每小时坐姿巡检:
拍照 -> YOLO pose 推理 -> 人脸可见性 + 坐姿分析 -> 有人脸则传 Immich -> 异常则输出警告
stdout 非空 = 需要发给用户的警告; stdout 空 = 无需打扰; exit!=0 = 工具故障(调度器会告警)
"""
import subprocess, json, time, os, sys, shutil

SNAPSHOT = '/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot'
VR = '/data/ai_cpe/demo/bin/vision_runner'
MODELS = {
    "detect": "/data/ai_cpe/demo/ai_models/yolov8n/model.ncnn.param",
    "pose": "/data/ai_cpe/demo/ai_models/yolov8n-pose/model.ncnn.param"
}
IMMICH_URL = 'http://192.168.1.244:2283/api/assets'
def _load_api_key():
    k = os.environ.get('IMMICH_API_KEY')
    if k:
        return k
    for p in ('/data/ai_cpe/hermes/home/photos/.immich_key',):
        if os.path.exists(p):
            with open(p) as f:
                return f.read().strip()
    raise SystemExit('未找到 Immich API Key（IMMICH_API_KEY 或 photos/.immich_key）')

API_KEY = _load_api_key()
PHOTO_DIR = '/data/ai_cpe/hermes/home/photos'
ARCHIVE = os.path.join(PHOTO_DIR, 'archive')

# ---- 坐姿阈值 (可调) ----
FACE_KEYPOINT_SCORE = 0.45      # 鼻子+眼睛关键点置信度下限
SHOULDER_TILT_MAX = 0.18        # 肩部左右高度差/肩宽 上限
HIP_TILT_MAX = 0.25             # 髋部倾斜上限
HEAD_RATIO_MIN = 0.12           # (肩中点y-鼻y)/肩宽 下限 (低于=前倾低头)
HEAD_RATIO_MAX = 0.75           # 高于=头抬太高/后仰
TRUNK_SWAY_MAX = 0.35           # 躯干侧倾 (肩中点x-髋中点x)/肩宽

def run(cmd, t=90):
    try:
        r = subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=t)
        return (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return 'ERR: %s' % e

def analyze(photo_path):
    """返回 (face_visible, issues:list[str], det_str:str)"""
    req = {"version": 1, "operation": "pose", "input": {"path": photo_path}, "models": MODELS}
    r = subprocess.run([VR], input=json.dumps(req) + '\n', capture_output=True, text=True, timeout=150)
    if r.returncode != 0:
        raise RuntimeError('vision_runner 失败: %s' % r.stderr[-200:])
    out = json.loads(r.stdout)
    if not out.get('ok'):
        raise RuntimeError('vision_runner 返回错误: %s' % r.stdout[:200])
    persons = out.get('result', {}).get('persons', [])
    if not persons:
        return False, [], '(无人)'
    # 取置信度最高的人
    p = max(persons, key=lambda x: x.get('score', 0))
    kps = {i: k for i, k in enumerate(p.get('keypoints', []))}
    def kp(i):
        k = kps.get(i)
        return k if k and k.get('score', 0) > 0.3 else None

    nose = kp(0); eyeL = kp(1); eyeR = kp(2)
    shL = kp(5); shR = kp(6)
    hipL = kp(11); hipR = kp(12)

    face_visible = bool(nose) and (bool(eyeL) or bool(eyeR))
    # 人脸可见性: 鼻子 + 至少一只眼睛, 且关键点置信度达标
    if nose and nose['score'] >= FACE_KEYPOINT_SCORE and \
       ((eyeL and eyeL['score'] >= FACE_KEYPOINT_SCORE) or (eyeR and eyeR['score'] >= FACE_KEYPOINT_SCORE)):
        face_visible = True
    else:
        face_visible = False

    issues = []
    if not (nose and shL and shR):
        return face_visible, issues, '(关键点不全)'

    sh_w = abs(shR['x'] - shL['x']) or 1.0
    sh_mid_y = (shL['y'] + shR['y']) / 2
    sh_mid_x = (shL['x'] + shR['x']) / 2

    # a. 左右是否对称: 肩部倾斜
    tilt = abs(shL['y'] - shR['y']) / sh_w
    if tilt > SHOULDER_TILT_MAX:
        issues.append('左右不对称（肩部倾斜 %.0f%%）' % (tilt * 100))

    # b. 前倾 (2D 近似: 头部相对肩线过低)
    ratio = (sh_mid_y - nose['y']) / sh_w
    if nose['y'] > sh_mid_y + 0.1 * sh_w:
        issues.append('严重前倾（头部低于肩线）')
    elif ratio < HEAD_RATIO_MIN:
        issues.append('头部前倾/低头（头肩比 %.2f）' % ratio)

    # c. 头太高
    if ratio > HEAD_RATIO_MAX:
        issues.append('头部抬得过高（头肩比 %.2f）' % ratio)

    # 躯干侧倾
    if hipL and hipR:
        hip_mid_x = (hipL['x'] + hipR['x']) / 2
        sway = abs(sh_mid_x - hip_mid_x) / sh_w
        if sway > TRUNK_SWAY_MAX:
            issues.append('躯干侧倾（偏移 %.0f%%）' % (sway * 100))

    return face_visible, issues, '(肩宽%.0f 头肩比%.2f 倾斜%.0f%%)' % (sh_w, ratio, tilt * 100)

def upload(photo_path):
    now = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    asset_id = 'rg660mk-posture-%d' % int(time.time())
    cmd = ("curl -s -m 30 -X POST '%s' -H 'x-api-key: %s' -H 'Accept: application/json' "
           "-F 'assetData=@%s' -F 'deviceAssetId=%s' -F 'deviceId=rg660mk-hermes' "
           "-F 'fileCreatedAt=%s' -F 'fileModifiedAt=%s'") % (IMMICH_URL, API_KEY, photo_path,
                                                               asset_id, now, now)
    resp = run(cmd, t=60)
    try:
        return json.loads(resp).get('id')
    except Exception:
        return None

def main():
    photo = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].startswith('/') else None
    if photo is None:
        # 拍照
        out = run('%s 2>&1' % SNAPSHOT, t=90)
        if 'PHOTO_OK' not in out:
            sys.stderr.write('拍照失败: %s\n' % out[-300:])
            return 1
        src = '/tmp/RG660MK_C270.jpg'
        if not os.path.exists(src):
            sys.stderr.write('拍照输出不存在\n')
            return 1
        ts = time.strftime('%Y%m%d_%H%M%S')
        photo = os.path.join(PHOTO_DIR, 'photo_%s.jpg' % ts)
        shutil.copy(src, photo)
        os.makedirs(ARCHIVE, exist_ok=True)
        shutil.copy(src, os.path.join(ARCHIVE, 'hourly_%s.jpg' % ts))

    face, issues, stat = analyze(photo)

    if not face:
        # 无人脸: 不上传, 不打扰
        return 0

    # 有人脸: 上传 Immich
    asset = upload(photo)

    if issues:
        lines = ['⚠️ 坐姿提醒：检测到人在镜头前，请调整坐姿！']
        for i in issues:
            lines.append('· ' + i)
        if asset:
            lines.append('（照片已上传 Immich：%s）' % asset)
        print('\n'.join(lines))
    return 0

if __name__ == '__main__':
    sys.exit(main())
