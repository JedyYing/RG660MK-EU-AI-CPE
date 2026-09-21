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

CAMVIEW_SNAP = 'http://127.0.0.1:8090/snapshot'


def take_snapshot(out='/tmp/RG660MK_C270.jpg'):
    """抓帧：优先走 camview HTTP（避免与常驻 camview 抢占 C270 USB 导致 uvc_open Busy），
    失败回退直开 USB。先删除旧文件，杜绝"抓帧失败→复用旧图上传"的僵尸路径。"""
    try:
        os.unlink(out)
    except OSError:
        pass
    try:
        subprocess.run(['curl', '-s', '-m', '8', '-o', out, CAMVIEW_SNAP],
                       capture_output=True, timeout=15)
        with open(out, 'rb') as f:
            if f.read(2) == b'\xff\xd8' and os.path.getsize(out) > 5000:
                return True, 'camview-http'
    except Exception:
        pass
    try:
        r = subprocess.run([SNAPSHOT], capture_output=True, timeout=90)
        if os.path.exists(out) and os.path.getsize(out) > 5000:
            return True, 'usb-direct'
        return False, (r.stdout or b'').decode('utf-8', 'replace')[-160:]
    except Exception as e:
        return False, 'usb-direct err: %s' % e

def main():
    ts = time.strftime('%Y%m%d_%H%M%S')
    photo = os.path.join(PHOTO_DIR, 'photo_%s.jpg' % ts)

    # 1. 拍照（优先 camview HTTP；失败不复用旧图，如实报失败）
    src = '/tmp/RG660MK_C270.jpg'
    ok, how = take_snapshot(src)
    if not ok:
        step('拍照', False, how)
        return 1
    shutil.copy(src, photo)
    size = os.path.getsize(photo)
    step('拍照', True, '%s (%dB, %s)' % (photo, size, how))

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
        st = j.get('status')
        aid = j.get('id', '?')
        if st == 'created':
            step('Immich 上传', True, 'asset=%s' % aid)
        elif st == 'duplicate':
            step('Immich 上传', True, '重复照片（服务器已有同一张，未重复入库）asset=%s' % aid)
        else:
            step('Immich 上传', False, str(j)[:120])
    except Exception:
        step('Immich 上传', False, resp[:100])
    print('\n结果: photo=%s detections=%s persons=%d' % (photo, desc, pose_cnt))
    return 0

if __name__ == '__main__':
    sys.exit(main())
