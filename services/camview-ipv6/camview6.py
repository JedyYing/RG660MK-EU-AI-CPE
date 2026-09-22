#!/usr/bin/env python3
import socket, threading, select, sys
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
TARGET = ("127.0.0.1", 8090)
def pipe(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r: continue
            for s in r:
                d = s.recv(65536)
                if not d: raise OSError()
                (b if s is a else a).sendall(d)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try: s.close()
            except Exception: pass
srv = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
srv.bind(("::", PORT)); srv.listen(32)
while True:
    try: c, addr = srv.accept()
    except Exception: continue
    t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try: t.connect(TARGET)
    except Exception: c.close(); continue
    threading.Thread(target=pipe, args=(c, t), daemon=True).start()
