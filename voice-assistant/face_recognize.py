#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RG660MK 人脸识别（身份识别）—— 走 Immich 云人脸识别（buffalo_s 模型）。

用法:
    python3 face_recognize.py

流程: C270 拍照 -> 上传 Immich -> 等 ML 人脸识别 -> 查询该照片匹配到的人 -> 输出身份。

输出: 一行中文结果（供 TTS 播报），例如:
    '识别到 1 个人：应金栋'
    '识别到 1 个人（未命名）'
    '没有识别到人脸'
"""
import subprocess, json, sys, time, os, urllib.request, urllib.error

SNAPSHOT = '/data/ai_cpe/hermes/home/diag/rg660mk_c270_snapshot'
IMMICH_URL = 'http://192.168.1.244:2283'
KEY_FILE = '/data/ai_cpe/hermes/home/photos/.immich_key'


def load_key():
    for p in (KEY_FILE,):
        if os.path.exists(p):
            with open(p) as f:
                return f.read().strip()
    return os.environ.get('IMMICH_API_KEY', '')


def http(method, path, key, data=None, files=None):
    url = IMMICH_URL + path
    headers = {'x-api-key': key, 'Accept': 'application/json'}
    if method == 'POST' and files:
        # multipart 上传
        import uuid
        boundary = '----rg660mk%d' % int(time.time() * 1000)
        body = b''
        for name, (filename, content) in files.items():
            body += ('--%s\r\n' % boundary).encode()
            body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (name, filename)).encode()
            body += b'Content-Type: image/jpeg\r\n\r\n'
            body += content + b'\r\n'
        for name, value in data.items():
            body += ('--%s\r\n' % boundary).encode()
            body += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode()
            body += (str(value) + '\r\n').encode()
        body += ('--%s--\r\n' % boundary).encode()
        headers['Content-Type'] = 'multipart/form-data; boundary=%s' % boundary
        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    else:
        req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode('utf-8'))
        except Exception:
            return {'error': 'HTTP %s' % e.code}
    except Exception as e:
        return {'error': str(e)}


def main():
    key = load_key()
    if not key:
        print('未找到 Immich API Key')
        return 1

    # 1. 拍照
    subprocess.run([SNAPSHOT], capture_output=True, text=True, timeout=90)
    src = '/tmp/RG660MK_C270.jpg'
    if not os.path.exists(src):
        print('拍照失败')
        return 1
    with open(src, 'rb') as f:
        photo_bytes = f.read()

    # 2. 上传 Immich
    now = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    r = http('POST', '/api/assets', key,
             data={'deviceAssetId': 'face-%d' % int(time.time()),
                   'deviceId': 'rg660mk-hermes',
                   'fileCreatedAt': now, 'fileModifiedAt': now},
             files={'assetData': ('face.jpg', photo_bytes)})
    asset_id = r.get('id', '')
    if not asset_id:
        print('上传失败: %s' % json.dumps(r, ensure_ascii=False)[:120])
        return 1

    # 3. 等 ML 人脸识别（轮询，最长 ~15s）
    people = []
    for _ in range(6):
        time.sleep(3)
        a = http('GET', '/api/assets/%s' % asset_id, key)
        people = a.get('people') if isinstance(a, dict) else None
        if people is not None:
            break

    if not people:
        print('没有识别到人脸')
        return 0

    names = [p.get('name', '').strip() or '未命名的人' for p in people]
    n = len(people)
    if n == 1:
        print('识别到 1 个人：%s' % names[0])
    else:
        print('识别到 %d 个人：%s' % (n, '、'.join(names)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
