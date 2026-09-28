#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯标准库 MQTT 3.1.1（broker + client）—— 本机无 pip/无编译器，装不了 mosquitto。

支持：CONNECT/CONNACK、SUBSCRIBE/SUBACK、PUBLISH(QoS0/1)+PUBACK、PINGREQ/PINGRESP、
DISCONNECT、retain、通配符 + 与 #。仅用于 AI CPE 本地控制面（局域网、受控环境）。
生产建议：换 mosquitto（本模块对外接口保持不变，可整体替换）。
"""
import json, socket, struct, threading, time

# ---------------------------------------------------------------- 编解码
def _enc_len(n):
    out = b""
    while True:
        b = n % 128; n //= 128
        if n: out += bytes([b | 0x80])
        else: out += bytes([b]); break
    return out

def _read_len(sock):
    mult, val = 1, 0
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("eof")
        d = b[0]; val += (d & 127) * mult
        if not d & 0x80:
            return val
        mult *= 128
        if mult > 128 ** 3:
            raise ValueError("bad length")

def _utf(s):
    b = s.encode(); return struct.pack(">H", len(b)) + b

def _read_utf(buf, i):
    n = struct.unpack(">H", buf[i:i + 2])[0]
    return buf[i + 2:i + 2 + n].decode("utf-8", "replace"), i + 2 + n

def topic_match(filt, topic):
    if filt == topic:
        return True
    f, t = filt.split("/"), topic.split("/")
    for i, seg in enumerate(f):
        if seg == "#":
            return True
        if i >= len(t):
            return False
        if seg != "+" and seg != t[i]:
            return False
    return len(f) == len(t)

# ---------------------------------------------------------------- Broker
class Broker:
    def __init__(self, host="0.0.0.0", port=1883, log=None):
        self.host, self.port = host, port
        self.clients = []          # [{sock, subs:[], id}]
        self.retained = {}         # topic -> payload
        self.lock = threading.Lock()
        self.log = log or (lambda *a: None)
        self.running = False
        self.stats = {"connect": 0, "msg_in": 0, "msg_out": 0, "subs": 0}

    def start(self):
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port)); srv.listen(16)
        self.running = True
        self.log("broker listening on %s:%d" % (self.host, self.port))
        while self.running:
            try:
                c, addr = srv.accept()
            except OSError:
                break
            threading.Thread(target=self._client, args=(c, addr), daemon=True).start()

    def _send(self, sock, data):
        try:
            sock.sendall(data); self.stats["msg_out"] += 1
        except OSError:
            pass

    def _publish(self, topic, payload, retain=False, origin=None):
        with self.lock:
            if retain:
                self.retained[topic] = payload
            targets = [c for c in self.clients if c is not origin and any(topic_match(f, topic) for f, _ in c["subs"])]
        pkt = b"\x30" + _enc_len(2 + len(topic.encode()) + len(payload)) + _utf(topic) + payload
        for c in targets:
            self._send(c["sock"], pkt)

    def _client(self, sock, addr):
        c = {"sock": sock, "subs": [], "id": "?"}
        try:
            while True:
                hdr = sock.recv(1)
                if not hdr:
                    break
                typ = hdr[0] >> 4
                ln = _read_len(sock)
                body = b""
                while len(body) < ln:
                    chunk = sock.recv(ln - len(body))
                    if not chunk:
                        raise ConnectionError("eof")
                    body += chunk
                if typ == 1:      # CONNECT
                    i = 0
                    _, i = _read_utf(body, i); i += 1; i += 1; i += 2   # proto, level, flags, keepalive
                    cid, i = _read_utf(body, i)
                    c["id"] = cid or ("%s:%d" % addr)
                    self.stats["connect"] += 1
                    with self.lock:
                        self.clients.append(c)
                    self._send(sock, b"\x20\x02\x00\x00")
                    self.log("CONNECT %s" % c["id"])
                elif typ == 8:    # SUBSCRIBE
                    mid = struct.unpack(">H", body[0:2])[0]; i = 2; granted = b""
                    while i < len(body):
                        f, i = _read_utf(body, i); q = body[i]; i += 1
                        c["subs"].append((f, q)); granted += bytes([min(q, 1)]); self.stats["subs"] += 1
                        self.log("SUB %s <- %s" % (f, c["id"]))
                    self._send(sock, b"\x90" + _enc_len(2 + len(granted)) + struct.pack(">H", mid) + granted)
                    with self.lock:
                        snap = [(t, p) for t, p in self.retained.items() if any(topic_match(f, t) for f, _ in c["subs"])]
                    for t, p in snap:                       # 补发 retained
                        self._send(sock, b"\x30" + _enc_len(2 + len(t.encode()) + len(p)) + _utf(t) + p)
                elif typ == 3:    # PUBLISH
                    qos = (hdr[0] >> 1) & 3; retain = hdr[0] & 1
                    i = 0; topic, i = _read_utf(body, i)
                    if qos: mid = struct.unpack(">H", body[i:i + 2])[0]; i += 2
                    payload = body[i:]
                    self.stats["msg_in"] += 1
                    self._publish(topic, payload, retain, origin=c)
                    self.log("PUB %s <- %s (%dB)" % (topic, c["id"], len(payload)))
                    if qos == 1:
                        self._send(sock, b"\x40\x02" + struct.pack(">H", mid))
                elif typ == 12:   # PINGREQ
                    self._send(sock, b"\xd0\x00")
                elif typ == 14:   # DISCONNECT
                    break
        except Exception as e:
            self.log("client %s closed: %s" % (c.get("id"), e))
        finally:
            with self.lock:
                if c in self.clients:
                    self.clients.remove(c)
            try:
                sock.close()
            except OSError:
                pass

# ---------------------------------------------------------------- Client
class Client:
    def __init__(self, host="127.0.0.1", port=1883, client_id="aicpe", on_message=None, keepalive=30):
        self.host, self.port, self.cid = host, port, client_id
        self.sock = None; self.on_message = on_message
        self.keepalive = keepalive; self._mid = 0; self._stop = False

    def connect(self, timeout=8):
        self.sock = socket.socket(); self.sock.settimeout(timeout)
        self.sock.connect((self.host, self.port))
        payload = _utf("MQTT") + bytes([4, 0x02]) + struct.pack(">H", self.keepalive) + _utf(self.cid)
        self.sock.sendall(b"\x10" + _enc_len(len(payload)) + payload)
        resp = self.sock.recv(4)
        if not resp or resp[0] != 0x20 or resp[3] != 0:
            raise ConnectionError("CONNACK failed: %r" % resp)
        self.sock.settimeout(None)
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def _loop(self):
        last = time.time()
        while not self._stop:
            try:
                if time.time() - last > self.keepalive * 0.6:
                    self.sock.sendall(b"\xc0\x00"); last = time.time()
                hdr = self.sock.recv(1)
                if not hdr:
                    break
                typ = hdr[0] >> 4
                ln = _read_len(self.sock)
                body = b""
                while len(body) < ln:
                    ch = self.sock.recv(ln - len(body))
                    if not ch:
                        return
                    body += ch
                if typ == 3:
                    topic, i = _read_utf(body, 0)
                    if (hdr[0] >> 1) & 3:
                        i += 2
                    if self.on_message:
                        self.on_message(topic, body[i:])
                elif typ == 13:
                    last = time.time()
            except Exception:
                break

    def subscribe(self, topic, qos=0):
        self._mid += 1
        payload = struct.pack(">H", self._mid) + _utf(topic) + bytes([qos])
        self.sock.sendall(b"\x82" + _enc_len(len(payload)) + payload)

    def publish(self, topic, payload, retain=False, qos=0):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        payload = payload.encode() if isinstance(payload, str) else payload
        head = 0x30 | (qos << 1) | (1 if retain else 0)
        body = _utf(topic) + payload
        self.sock.sendall(bytes([head]) + _enc_len(len(body)) + body)

    def close(self):
        self._stop = True
        try:
            self.sock.sendall(b"\xe0\x00"); self.sock.close()
        except OSError:
            pass
