#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Moes 智能灯泡控制 (Tuya Cloud OpenAPI 新版签名, 2021-06+)

Python 纯 stdlib 实现，适配 OpenWrt 精简环境（无 bash 数组/openssl 依赖）。
与 ~/sg560d_pose_qnn/bulb_control.sh 签名逻辑完全一致。

用法:
    python3 bulb_control.py on|off|toggle|status

凭证读取顺序:
    1. 环境变量 TUYA_ACCESS_ID / TUYA_ACCESS_SECRET
    2. /data/hermes/.hermes/.env 中的 TUYA_ACCESS_ID= / TUYA_ACCESS_SECRET=
"""
import os, sys, json, time, hashlib, hmac
import urllib.request, urllib.error

DEVICE_ID = "6cef15216413d61f09c6u3"
API_HOST = "openapi.tuyacn.com"
API_BASE = "https://" + API_HOST
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ENV_FILE = "/data/hermes/.hermes/.env"


def _load_from_envfile():
    """从 Hermes 的 .env 读取 TUYA_ACCESS_ID / TUYA_ACCESS_SECRET（不覆盖已有环境变量）。"""
    env = {}
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


_ENV = _load_from_envfile()
ACCESS_ID = os.environ.get("TUYA_ACCESS_ID") or _ENV.get("TUYA_ACCESS_ID", "")
ACCESS_SECRET = os.environ.get("TUYA_ACCESS_SECRET") or _ENV.get("TUYA_ACCESS_SECRET", "")


def hmac_sha256_upper(msg: str, key: str) -> str:
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest().upper()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def make_sign(token: str, ts: str, method: str, body: str, path: str, ctype: str = "") -> str:
    """构造签名串并返回 HMAC 大写 hex。path 需带前导 /。"""
    body_sha = sha256_hex(body) if body else EMPTY_SHA256
    payload = ACCESS_ID + token + ts + method + "\n" + body_sha + "\n"
    if ctype:
        payload += ctype + "\n"
    payload += "\n" + path
    return hmac_sha256_upper(payload, ACCESS_SECRET)


def http(method: str, path: str, token: str = "", body: str = "", ctype: str = ""):
    """发送带签名的请求。path 形如 'v1.0/token?grant_type=1'（不带前导 /）。"""
    ts = str(int(time.time() * 1000))
    sign = make_sign(token, ts, method, body, "/" + path, ctype)
    headers = {
        "client_id": ACCESS_ID,
        "sign": sign,
        "t": ts,
        "sign_method": "HMAC-SHA256",
        "mode": "cors",
    }
    if token:
        headers["access_token"] = token
    else:
        headers["secret"] = ACCESS_SECRET  # 取 token 场景用 secret 头
    data = None
    if method == "POST":
        headers["Content-type"] = "application/json"
        headers["Signature-Headers"] = "Content-type"
        data = body.encode("utf-8")
    req = urllib.request.Request(API_BASE + "/" + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"error": "HTTP %s" % e.code}
    except Exception as e:
        return {"error": str(e)}


def get_token() -> str:
    r = http("GET", "v1.0/token?grant_type=1")
    return (r.get("result") or {}).get("access_token", "")


def main() -> int:
    if not ACCESS_ID or not ACCESS_SECRET:
        print("❌ 缺少 Tuya 凭据：请设置 TUYA_ACCESS_ID / TUYA_ACCESS_SECRET")
        return 1
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in ("on", "off", "toggle", "status"):
        print("用法: python3 bulb_control.py on|off|toggle|status")
        return 2

    token = get_token()
    if not token:
        print("❌ 获取 token 失败")
        return 1

    if action == "status":
        r = http("GET", "v1.0/devices/%s/status" % DEVICE_ID, token=token)
        print(json.dumps(r, ensure_ascii=False))
        return 0

    if action == "toggle":
        r = http("GET", "v1.0/devices/%s/status" % DEVICE_ID, token=token)
        cur = False
        try:
            for dp in r.get("result", []):
                if dp.get("code") == "switch_led":
                    cur = bool(dp.get("value"))
        except Exception:
            cur = False
        action = "off" if cur else "on"

    body = json.dumps({"commands": [{"code": "switch_led", "value": action == "on"}]},
                      separators=(",", ":"))
    r = http("POST", "v1.0/devices/%s/commands" % DEVICE_ID, token=token,
             body=body, ctype="Content-type:application/json")
    ok = bool(r.get("success"))
    print("✅ 已发送%s灯指令" % ("开" if action == "on" else "关") if ok else
          "❌ 指令失败: %s" % json.dumps(r, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
