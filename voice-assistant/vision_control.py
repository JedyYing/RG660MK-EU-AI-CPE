#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 视觉控制（语音助手用）：人脸检测 / 坐姿检测

用法:
    python3 vision_control.py face       # 拍照 + pose 推理 -> 人脸是否可见
    python3 vision_control.py posture    # 拍照 + pose 推理 -> 坐姿是否良好

输出: 一行中文自然语言结果（供 TTS 播报，<120 字）。

依赖: vision_runner (NCNN) + yolov8n-pose (COCO 17 关键点)。
人脸检测 = 鼻子 + 眼睛关键点是否可见且置信度达标（无专用人脸模型，用 pose 近似）。
"""
import subprocess, json, sys, os, shutil, time

SNAPSHOT = '/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot'
VR = '/data/ai_cpe/demo/bin/vision_runner'
MODELS = {
    "detect": "/data/ai_cpe/demo/ai_models/yolov8n/model.ncnn.param",
    "pose": "/data/ai_cpe/demo/ai_models/yolov8n-pose/model.ncnn.param"
}
PHOTO_DIR = '/data/ai_cpe/hermes/home/photos'

# ---- 阈值（与 posture_check.py 保持一致）----
FACE_KEYPOINT_SCORE = 0.45   # 鼻子/眼睛关键点置信度下限
SHOULDER_TILT_MAX = 0.18     # 肩部左右高度差/肩宽 上限
HEAD_RATIO_MIN = 0.12        # (肩中点y-鼻y)/肩宽 下限（低于=低头）
HEAD_RATIO_MAX = 0.75        # 高于=头抬太高
TRUNK_SWAY_MAX = 0.35        # 躯干侧倾（肩中点x-髋中点x）/肩宽


def take_photo():
    """拍照并落盘，返回照片路径；失败返回 None。"""
    subprocess.run([SNAPSHOT], capture_output=True, text=True, timeout=90)
    src = '/tmp/RG660MK_C270.jpg'
    if not os.path.exists(src):
        return None
    os.makedirs(PHOTO_DIR, exist_ok=True)
    photo = os.path.join(PHOTO_DIR, 'voice_%s.jpg' % time.strftime('%Y%m%d_%H%M%S'))
    shutil.copy(src, photo)
    return photo


def run_pose(photo):
    """pose 推理，返回 persons 列表；出错返回 None。"""
    req = {"version": 1, "operation": "pose", "input": {"path": photo}, "models": MODELS}
    try:
        r = subprocess.run([VR], input=json.dumps(req) + '\n',
                           capture_output=True, text=True, timeout=150)
        out = json.loads(r.stdout)
        if not out.get('ok'):
            return None
        return out.get('result', {}).get('persons', [])
    except Exception:
        return None


def _kp(p, i):
    """取第 i 个关键点（COCO 17），低置信度视为缺失。"""
    kps = {j: k for j, k in enumerate(p.get('keypoints', []))}
    k = kps.get(i)
    return k if k and k.get('score', 0) > 0.3 else None


def analyze_person(p):
    """返回 (face_visible:bool, issues:list[str])。"""
    nose = _kp(p, 0); eyeL = _kp(p, 1); eyeR = _kp(p, 2)
    shL = _kp(p, 5); shR = _kp(p, 6)
    hipL = _kp(p, 11); hipR = _kp(p, 12)

    face = bool(nose) and nose['score'] >= FACE_KEYPOINT_SCORE and \
        ((eyeL and eyeL['score'] >= FACE_KEYPOINT_SCORE) or
         (eyeR and eyeR['score'] >= FACE_KEYPOINT_SCORE))

    issues = []
    if nose and shL and shR:
        sh_w = abs(shR['x'] - shL['x']) or 1.0
        sh_mid_y = (shL['y'] + shR['y']) / 2
        sh_mid_x = (shL['x'] + shR['x']) / 2
        tilt = abs(shL['y'] - shR['y']) / sh_w
        if tilt > SHOULDER_TILT_MAX:
            issues.append('左右肩不平衡')
        ratio = (sh_mid_y - nose['y']) / sh_w
        if nose['y'] > sh_mid_y + 0.1 * sh_w:
            issues.append('严重低头')
        elif ratio < HEAD_RATIO_MIN:
            issues.append('低头前倾')
        if ratio > HEAD_RATIO_MAX:
            issues.append('头抬太高')
        if hipL and hipR:
            hip_mid_x = (hipL['x'] + hipR['x']) / 2
            if abs(sh_mid_x - hip_mid_x) / sh_w > TRUNK_SWAY_MAX:
                issues.append('身体侧倾')
    return face, issues


def cmd_face():
    photo = take_photo()
    if not photo:
        return '拍照失败，请检查摄像头'
    persons = run_pose(photo)
    if persons is None:
        return '人脸检测推理失败'
    if not persons:
        return '画面里没有检测到人'
    for p in persons:
        face, _ = analyze_person(p)
        if face:
            return '检测到 %d 个人，有人脸正对镜头' % len(persons)
    return '检测到 %d 个人，但没有人脸正对镜头' % len(persons)


def cmd_posture():
    photo = take_photo()
    if not photo:
        return '拍照失败，请检查摄像头'
    persons = run_pose(photo)
    if persons is None:
        return '坐姿检测推理失败'
    if not persons:
        return '画面里没有检测到人'
    p = max(persons, key=lambda x: x.get('score', 0))
    face, issues = analyze_person(p)
    if not issues:
        return '坐姿良好'
    return '坐姿提醒：' + '、'.join(issues)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'face'
    if action == 'face':
        print(cmd_face())
    elif action == 'posture':
        print(cmd_posture())
    else:
        print('用法: python3 vision_control.py face|posture')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
