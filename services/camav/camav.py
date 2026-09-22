#!/usr/bin/env python3
# camav.py -- 摄像头画面 + C270(Logitech)麦克风声音 合流服务
#   /          页面（视频 + 声音）
#   /stream    MJPEG 视频（转发 camview:8090）
#   /snapshot  单帧
#   /audio     实时音频 S16LE mono 16kHz
# 音频采用【常驻单例采集 + 广播】：一个 arecord 长期运行，多个观看者共享同一路数据。
import http.server, socketserver, subprocess, socket, sys, os, time, threading

CAM  = ("127.0.0.1", 8090)
MICS = os.environ.get("MIC_DEV", "plughw:1,0").split(",")   # 只用 C270 (Logitech)
RATE = int(os.environ.get("MIC_RATE", "16000"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8092

BUF_MAX = RATE * 2 * 2          # 保留最近 2 秒
BUF     = bytearray()
POS     = 0                     # 累计写入字节数(时间轴)
CV      = threading.Condition()
READER_STARTED = False
STATS   = {"dev": None, "bytes": 0, "clients": 0, "since": 0}

def _reader():
    global BUF, POS
    while True:
        dev = None
        for d in MICS:
            d = d.strip()
            q = subprocess.Popen(["arecord","-D",d,"-f","S16_LE","-r",str(RATE),"-c","1","-t","raw","-q"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            time.sleep(0.4)
            if q.poll() is None:
                dev = d; break
            try: q.kill()
            except Exception: pass
        if dev is None:
            time.sleep(1.5); continue
        STATS["dev"] = dev; STATS["since"] = time.time()
        sys.stderr.write("audio reader: %s\n" % dev); sys.stderr.flush()
        try:
            while True:
                d = q.stdout.read(2048)
                if not d: break
                with CV:
                    BUF.extend(d); POS += len(d); STATS["bytes"] += len(d)
                    if len(BUF) > BUF_MAX: del BUF[:len(BUF)-BUF_MAX]
                    CV.notify_all()
        except Exception:
            pass
        finally:
            try: q.kill()
            except Exception: pass
        sys.stderr.write("audio reader: device lost (%s), reconnecting\n" % dev); sys.stderr.flush()
        time.sleep(0.6)

def ensure_reader():
    global READER_STARTED
    if READER_STARTED: return
    READER_STARTED = True
    threading.Thread(target=_reader, daemon=True).start()

PAGE = (b'<!doctype html><html lang="zh"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>RG660MK \xe6\x91\x84\xe5\x83\x8f\xe5\xa4\xb4\xef\xbc\x88\xe5\x90\xab\xe5\xa3\xb0\xe9\x9f\xb3\xef\xbc\x89</title>\n<style>\n*{box-sizing:border-box;margin:0;padding:0}\nbody{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e8ec;\n     min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:16px;gap:12px}\nh1{font-size:18px;font-weight:600}.sub{color:#8b8f98;font-size:12px;text-align:center}\n#stage{width:100%;max-width:900px;aspect-ratio:4/3;background:#000;border-radius:12px;overflow:hidden;\n       display:flex;align-items:center;justify-content:center;box-shadow:0 8px 30px rgba(0,0,0,.5)}\n#stage img{width:100%;height:100%;object-fit:contain}\nbutton{font-size:16px;padding:12px 22px;border-radius:10px;border:1px solid #2b2f36;background:#1a1d23;\n       color:#e6e8ec;cursor:pointer}\nbutton.on{background:#16a34a;border-color:#16a34a;color:#fff}\n.bar{display:flex;gap:10px;flex-wrap:wrap;justify-content:center;align-items:center}\n</style></head><body>\n<h1>RG660MK \xe5\xae\x9e\xe6\x97\xb6\xe9\xa2\x84\xe8\xa7\x88\xef\xbc\x88\xe7\x94\xbb\xe9\x9d\xa2 + \xe5\xa3\xb0\xe9\x9f\xb3\xef\xbc\x89</h1>\n<div class="sub">Logitech C270 \xc2\xb7 \xe8\xa7\x86\xe9\xa2\x91 MJPEG + \xe9\xba\xa6\xe5\x85\x8b\xe9\xa3\x8e\xe5\xae\x9e\xe6\x97\xb6\xe9\x9f\xb3\xe9\xa2\x91</div>\n<div id="stage"><img id="v" src="/stream" alt="camera"></div>\n<div class="bar">\n  <button id="snd">\xf0\x9f\x94\x8a \xe7\x82\xb9\xe5\x87\xbb\xe5\xbc\x80\xe5\x90\xaf\xe5\xa3\xb0\xe9\x9f\xb3</button>\n  <button id="rc">\xe2\x86\xbb \xe9\x87\x8d\xe8\xbf\x9e\xe7\x94\xbb\xe9\x9d\xa2</button>\n  <a href="/snapshot" download="snapshot.jpg" style="color:#8b8f98;font-size:13px">\xe6\x88\xaa\xe5\x9b\xbe\xe4\xbf\x9d\xe5\xad\x98</a>\n</div>\n<script>\nvar ac=null, running=false, SR=16000;\ndocument.getElementById("rc").onclick=function(){document.getElementById("v").src="/stream?"+Date.now();};\ndocument.getElementById("snd").onclick=function(){\n  var btn=this;\n  if(running) return; running=true; btn.textContent="\xe8\xbf\x9e\xe6\x8e\xa5\xe5\xa3\xb0\xe9\x9f\xb3\xe4\xb8\xad\xe2\x80\xa6";\n  try{\n    ac = new (window.AudioContext||window.webkitAudioContext)({sampleRate: SR});\n    var go = function(){\n      fetch("/audio",{cache:"no-store"}).then(function(resp){\n        var reader=resp.body.getReader(), pending=new Uint8Array(0), next=0;\n        btn.textContent="\xf0\x9f\x94\x8a \xe5\xa3\xb0\xe9\x9f\xb3\xe5\xb7\xb2\xe5\xbc\x80\xe5\x90\xaf"; btn.className="on";\n        var pump=function(){\n          reader.read().then(function(r){\n            if(r.done) return;\n            var val=r.value;\n            var merged=new Uint8Array(pending.length+val.length);\n            merged.set(pending); merged.set(val,pending.length);\n            var n=Math.floor(merged.length/2), usable=n*2;\n            var i16=new Int16Array(merged.buffer, merged.byteOffset, n);\n            var f32=new Float32Array(n);\n            for(var i=0;i<n;i++) f32[i]=i16[i]/32768;\n            var ab=ac.createBuffer(1,n,SR); ab.copyToChannel(f32,0);\n            var s=ac.createBufferSource(); s.buffer=ab; s.connect(ac.destination);\n            if(next < ac.currentTime) next = ac.currentTime + 0.2;\n            s.start(next); next += ab.duration;\n            pending = merged.slice(usable);\n            pump();\n          }).catch(function(){});\n        };\n        pump();\n      }).catch(function(e){ btn.textContent="\xe2\x9d\x8c \xe5\x8f\x96\xe9\x9f\xb3\xe9\xa2\x91\xe5\xa4\xb1\xe8\xb4\xa5"; });\n    };\n    if(ac.state==="suspended"){ ac.resume().then(go); } else { go(); }\n  }catch(e){ btn.textContent="\xe2\x9d\x8c \xe5\xa3\xb0\xe9\x9f\xb3\xe5\xa4\xb1\xe8\xb4\xa5: "+e; }\n};\n</script></body></html>')

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers(); self.wfile.write(PAGE); return
        if p in ("/stream", "/snapshot"):
            self.proxy(p); return
        if p == "/audio":
            self.audio(); return
        if p == "/status":
            b = ("dev=%s bytes=%d clients=%d uptime=%.0fs\n" %
                 (STATS["dev"], STATS["bytes"], STATS["clients"],
                  time.time()-STATS["since"] if STATS["since"] else 0)).encode()
            self.send_response(200); self.send_header("Content-Type","text/plain")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.send_response(404); self.send_header("Content-Length","2"); self.end_headers(); self.wfile.write(b"no")

    def proxy(self, path):
        try:
            up = socket.create_connection(CAM, timeout=10)
        except Exception:
            self.send_response(502); self.send_header("Content-Length","8"); self.end_headers()
            self.wfile.write(b"up fail!"); return
        up.sendall(("GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n" % (path, CAM[0])).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            d = up.recv(4096)
            if not d: break
            buf += d
        head, _, rest = buf.partition(b"\r\n\r\n")
        ct = "application/octet-stream"
        for ln in head.split(b"\r\n")[1:]:
            if ln.lower().startswith(b"content-type:"):
                ct = ln.split(b":",1)[1].strip().decode("latin1")
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            if rest: self.wfile.write(rest)
            while True:
                d = up.recv(32768)
                if not d: break
                self.wfile.write(d)
        except Exception: pass
        finally: up.close()

    def audio(self):
        ensure_reader()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        STATS["clients"] += 1
        try:
            with CV:
                start = POS - len(BUF)
                pos   = max(POS - RATE//2, start)     # 预送 ~0.5 秒做缓冲
            while True:
                with CV:
                    start = POS - len(BUF)
                    if pos < start: pos = start        # 落后太多则跳帧
                    while pos >= POS:
                        CV.wait(1.0)
                    off  = pos - start
                    data = bytes(BUF[off:off+4096])
                    pos += len(data)
                if data:
                    self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
                    try: self.wfile.flush()
                    except Exception: pass
        except Exception:
            pass
        finally:
            STATS["clients"] -= 1

class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == "__main__":
    TS(("0.0.0.0", PORT), H).serve_forever()
