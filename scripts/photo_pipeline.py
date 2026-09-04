#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 一键拍照 -> YOLO 推理 -> Immich 上传"""
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
DEVICE_ID = 'rg660mk-hermes'
PHOTO_DIR = '/data/ai_cpe/hermes/home/photos'
os.makedirs(PHOTO_DIR, exist_ok=True)

def run(cmd, t=90):
    try:
        r = subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=t)
        return (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return 'ERR: %s' % e

def step(name, ok, detail):
    print('[%s] %s: %s' % ('PASS' if ok else 'FAIL', name, detail), flush=True)
    return ok

def main():
    ts = time.strftime('%Y%m%d_%H%M%S')
    photo = os.path.join(PHOTO_DIR, 'photo_%s.jpg' % ts)

    # 1. 拍照
    out = run('%s' % SNAPSHOT, t=90)
    src = '/tmp/RG660MK_C270.jpg'
    if not os.path.exists(src):
        step('拍照', False, out[-200:])
        return 1
    shutil.copy(src, photo)
    size = os.path.getsize(photo)
    if not step('拍照', True, '%s (%dB)' % (photo, size)):
        return 1

    # 2. YOLO 推理
    results = {}
    for op in ['detect', 'pose']:
        req = {"version": 1, "operation": op, "input": {"path": photo}, "models": MODELS}
        r = subprocess.run([VR], input=json.dumps(req) + '\n', capture_output=True, text=True, timeout=150)
        out = json.loads(r.stdout)
        results[op] = out.get('result', {})
    det = results.get('detect', {})
    dets = det.get('detections', [])
    desc = ', '.join('%s(%.2f)' % (d['class_name'], d['score']) for d in dets) or '无目标'
    pose_cnt = results.get('pose', {}).get('count', 0)
    step('YOLO detect', True, '%.0fms [%s]' % (det.get('inference_ms', 0), desc))
    step('YOLO pose', True, '%.0fms [%d 人]' % (results.get('pose', {}).get('inference_ms', 0), pose_cnt))

    # 3. 上传 Immich
    now = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    asset_id = 'rg660mk-hermes-%d' % int(time.time())
    cmd = ("curl -s -m 30 -X POST '%s' -H 'x-api-key: %s' -H 'Accept: application/json' "
           "-F 'assetData=@%s' -F 'deviceAssetId=%s' -F 'deviceId=%s' "
           "-F 'fileCreatedAt=%s' -F 'fileModifiedAt=%s'") % (IMMICH_URL, API_KEY, photo,
                                                               asset_id, DEVICE_ID, now, now)
    resp = run(cmd, t=60)
    try:
        j = json.loads(resp)
        ok = j.get('status') == 'created'
        aid = j.get('id', '?')
    except Exception:
        ok, aid = False, resp[:100]
    step('Immich 上传', ok, 'asset=%s' % aid)
    print('\n结果: photo=%s detections=%s persons=%d' % (photo, desc, pose_cnt))
    return 0

if __name__ == '__main__':
    sys.exit(main())
