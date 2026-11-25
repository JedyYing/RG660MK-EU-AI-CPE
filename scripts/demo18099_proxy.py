#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""18099 演示代理：127.0.0.1:18099 → 板子 8099（默认走 ZeroTier，可回退局域网）
自动注入 X-Demo-Token（从 ~/.config/rg660/demo_token 读取），浏览器无需手工带 token。
"""
import os
import socket
import threading

LISTEN = ("127.0.0.1", 18099)
TARGETS = [("172.25.25.224", 8099), ("192.168.1.1", 8099)]   # ZT 优先，网线在时也行
TOKEN_FILE = os.path.expanduser("~/.config/rg660/demo_token")


def token():
    try:
        return open(TOKEN_FILE, encoding="utf-8").read().strip()
    except Exception:
        return ""


def pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except Exception:
        pass
    finally:
        try:
            b.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def handle(c):
    upstream = None
    try:
        head = b""
        c.settimeout(10)
        while b"\r\n\r\n" not in head and len(head) < 65536:
            d = c.recv(4096)
            if not d:
                return
            head += d
        head_only = head.split(b"\r\n\r\n", 1)[0]
        rest = head.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in head else b""
        tok = token()
        lines = head_only.split(b"\r\n")
        if tok and not any(l.lower().startswith(b"x-demo-token:") for l in lines):
            lines.append(b"X-Demo-Token: " + tok.encode())
        new_head = b"\r\n".join(lines) + b"\r\n\r\n"

        for host, port in TARGETS:
            try:
                upstream = socket.create_connection((host, port), timeout=5)
                break
            except Exception:
                upstream = None
        if upstream is None:
            c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        upstream.sendall(new_head + rest)
        c.settimeout(None)
        upstream.settimeout(None)
        t1 = threading.Thread(target=pipe, args=(c, upstream), daemon=True)
        t2 = threading.Thread(target=pipe, args=(upstream, c), daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()
    except Exception:
        pass
    finally:
        for s in (c, upstream):
            try:
                s.close()
            except Exception:
                pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(32)
    print("demo18099 proxy on %s:%d -> %s" % (LISTEN[0], LISTEN[1], TARGETS), flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
