#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 camview 预览代理：127.0.0.1:18090 -> 192.168.1.1:8090，自动注入运维令牌。
仅监听回环；令牌从 ~/.config/rg660/camview_token 读取（0600）。"""
import os
import socket
import threading

LISTEN = ("127.0.0.1", 18092)
UPSTREAM = ("192.168.1.1", 8090)
TOKEN_FILE = os.path.expanduser("~/.config/rg660/camview_token")


def token():
    try:
        return open(TOKEN_FILE).read().strip()
    except Exception:
        return ""


def pipe(src, dst):
    try:
        while True:
            b = src.recv(65536)
            if not b:
                break
            dst.sendall(b)
    except Exception:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


def handle(c):
    try:
        c.settimeout(10)
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 16384:
            b = c.recv(4096)
            if not b:
                c.close(); return
            head += b
        if b"\r\n\r\n" not in head:
            c.close(); return
        raw_head, rest = head.split(b"\r\n\r\n", 1)
        lines = raw_head.split(b"\r\n")
        req = lines[0]
        # 已带 Authorization 就不重复加
        if b"authorization:" not in raw_head.lower():
            t = token()
            if t:
                lines.append(("Authorization: Bearer " + t).encode())
        new_head = b"\r\n".join(lines) + b"\r\n\r\n" + rest
        u = socket.create_connection(UPSTREAM, timeout=10)
        u.sendall(new_head)
        c.settimeout(None); u.settimeout(None)
        threading.Thread(target=pipe, args=(u, c), daemon=True).start()
        pipe(c, u)
    except Exception:
        try:
            c.close()
        except Exception:
            pass


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(LISTEN)
    s.listen(64)
    print("camview proxy on http://%s:%d -> %s:%d" % (LISTEN + UPSTREAM), flush=True)
    while True:
        c, _ = s.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
