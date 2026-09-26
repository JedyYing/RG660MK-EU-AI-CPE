#!/usr/bin/env python3
# camav.py v3 -- 摄像头画面 + C270 麦克风 + 低延时双向对讲
# 设计目标：窄上行链路（实测约 60KB/s）也能低延时
#   /                     页面（画面 + 声音 + 对讲，音频走 WebSocket）
#   /stream?w=&q=&fps=    低码率 MJPEG（默认 400px/q45/3fps ≈ 20KB/s）
#   /stream_hd            原画 MJPEG（局域网用，直接转发 camview）
#   /snapshot             单帧
#   /audio                设备->浏览器 PCM（HTTP 兼容通道，页面已改用 /ws）
#   /ws                   WebSocket：下行麦克风音频 + 上行对讲音频（主通道）
#   /talk                 WebSocket：仅上行对讲（旧接口保留）
#   /tone?dev=X           1.5s 测试音
#   /status               状态
# 关键点：视频【只发最新帧】——慢客户端只会丢帧，不会积压出秒级延时。
import http.server, socketserver, subprocess, socket, sys, os, time, threading, io, array
import base64, hashlib, struct, queue, math, urllib.parse
from PIL import Image

CAM   = ("127.0.0.1", 8090)
MICS  = os.environ.get("MIC_DEV", "plughw:1,0").split(",")     # 只用 C270
RATE  = int(os.environ.get("MIC_RATE", "8000"))                # 上线采样率(8k=16KB/s)
CAPR  = int(os.environ.get("MIC_CAP_RATE", "16000"))           # 采集采样率
SPK   = os.environ.get("SPK_DEV", "plughw:2,0").strip()        # 对讲播放(CM564，借用即释)
IDLE  = float(os.environ.get("TALK_IDLE", "45"))
TMAX  = float(os.environ.get("TALK_MAX", "300"))
PRE   = float(os.environ.get("AUDIO_PRE", "0.15"))             # 下行预缓冲(秒)
VID_W = int(os.environ.get("VID_W", "288"))
VID_Q = int(os.environ.get("VID_Q", "38"))
VID_F   = float(os.environ.get("VID_FPS", "2"))
PORT  = int(sys.argv[1]) if len(sys.argv) > 1 else 8092

BUF_MAX = RATE * 2 * 4
BUF = bytearray(); POS = 0
CV = threading.Condition(); READER_STARTED = False
STATS = {"dev": None, "bytes": 0, "clients": 0, "since": 0, "talk": 0, "talk_bytes": 0, "ws": 0, "spk": None}
TALK = {"active": False, "peer": "", "started": 0.0, "bytes": 0}
TALK_LOCK = threading.Lock()
VF = {"data": None, "seq": 0}
VFLOCK = threading.Condition(); VIDEO_STARTED = False
TCACHE = {}

def _decimate(d):
    """CAPR -> RATE 的整数抽取（块平均，简易抗混叠）"""
    if RATE <= 0: return d
    f = int(round(CAPR / RATE))
    if f <= 1: return d
    n = len(d) - (len(d) % (2 * f))
    if n <= 0: return b""
    a = array.array("h"); a.frombytes(d[:n])
    if f == 2:
        o = array.array("h", ((a[i] + a[i+1]) // 2 for i in range(0, len(a), 2)))
    else:
        o = array.array("h", (sum(a[i:i+f]) // f for i in range(0, len(a), f)))
    return o.tobytes()

def _reader():
    global BUF, POS
    while True:
        dev = None
        for d in MICS:
            d = d.strip()
            q = subprocess.Popen(["arecord","-D",d,"-f","S16_LE","-r",str(CAPR),"-c","1","-t","raw","-q"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            time.sleep(0.4)
            if q.poll() is None:
                dev = d; break
            try: q.kill()
            except Exception: pass
        if dev is None:
            time.sleep(1.5); continue
        STATS["dev"] = dev; STATS["since"] = time.time()
        sys.stderr.write("audio reader: %s @%d -> %dHz\n" % (dev, CAPR, RATE)); sys.stderr.flush()
        try:
            while True:
                d = q.stdout.read(4096)
                if not d: break
                d = _decimate(d)
                if not d: continue
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

# ---------------- 视频：常驻读帧（最新帧胜出） ----------------
def _video_reader():
    while True:
        try:
            up = socket.create_connection(CAM, timeout=10)
            up.sendall(b"GET /stream HTTP/1.0\r\nHost: cam\r\nConnection: close\r\n\r\n")
            f = up.makefile("rb")
            while True:
                ln = f.readline()
                if not ln or ln in (b"\r\n", b"\n"): break
            buf = bytearray()
            while True:
                d = f.read(65536)
                if not d: break
                buf.extend(d)
                while True:
                    i = buf.find(b"\xff\xd8")
                    if i < 0:
                        if len(buf) > 1: del buf[:-1]
                        break
                    j = buf.find(b"\xff\xd9", i + 2)
                    if j < 0:
                        if i: del buf[:i]
                        break
                    fr = bytes(buf[i:j+2]); del buf[:j+2]
                    if len(fr) < 200: continue
                    with VFLOCK:
                        VF["data"] = fr; VF["seq"] += 1; VFLOCK.notify_all()
        except Exception:
            pass
        time.sleep(1.0)

def ensure_video():
    global VIDEO_STARTED
    if VIDEO_STARTED: return
    VIDEO_STARTED = True
    threading.Thread(target=_video_reader, daemon=True).start()

def transcode(seq, jpg, w, q):
    key = (seq, w, q)
    hit = TCACHE.get(key)
    if hit is not None: return hit
    im = Image.open(io.BytesIO(jpg)).convert("RGB")
    if w and im.width > w:
        h = max(2, int(im.height * w / im.width))
        im = im.resize((w, h), Image.BILINEAR)
    b = io.BytesIO(); im.save(b, "JPEG", quality=q)
    out = b.getvalue()
    if len(TCACHE) > 40: TCACHE.clear()
    TCACHE[key] = out
    return out

# ---------------- WebSocket 最小实现 ----------------
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
def ws_accept(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
def ws_send(wfile, opcode, payload=b""):
    n = len(payload)
    hdr = bytes([0x80 | opcode])
    if n < 126:      hdr += bytes([n])
    elif n < 65536:  hdr += bytes([126]) + struct.pack(">H", n)
    else:            hdr += bytes([127]) + struct.pack(">Q", n)
    wfile.write(hdr + payload); wfile.flush()
def ws_read(rfile):
    h = rfile.read(2)
    if not h or len(h) < 2: return None
    b1, b2 = h[0], h[1]
    opcode = b1 & 0x0F; masked = b2 & 0x80; n = b2 & 0x7F
    if n == 126:   n = struct.unpack(">H", rfile.read(2))[0]
    elif n == 127: n = struct.unpack(">Q", rfile.read(8))[0]
    mask = rfile.read(4) if masked else b""
    data = rfile.read(n) if n else b""
    if len(data) < n: return None
    if masked and mask:
        data = bytes(data[i] ^ mask[i % 4] for i in range(n))
    return opcode, data

# ---------------- IMA ADPCM（4bit，8kHz 时仅 4KB/s） ----------------
IMA_STEP = (7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,
            130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,
            1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,
            5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,
            27086,29794,32767)
IMA_IDX = (-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8)

class ImaEnc:
    __slots__ = ("pred", "idx")
    def __init__(self): self.pred = 0; self.idx = 0
    def enc(self, smp):
        step = IMA_STEP[self.idx]
        diff = smp - self.pred
        if diff < 0: code = 8; diff = -diff
        else: code = 0
        if diff >= step:
            code |= 4; diff -= step
        step >>= 1
        if diff >= step:
            code |= 2; diff -= step
        step >>= 1
        if diff >= step: code |= 1
        step = IMA_STEP[self.idx]
        d = step >> 3
        if code & 4: d += step
        if code & 2: d += step >> 1
        if code & 1: d += step >> 2
        if code & 8: self.pred -= d
        else: self.pred += d
        if self.pred > 32767: self.pred = 32767
        elif self.pred < -32768: self.pred = -32768
        self.idx += IMA_IDX[code]
        if self.idx < 0: self.idx = 0
        elif self.idx > 88: self.idx = 88
        return code

class ImaDec:
    __slots__ = ("pred", "idx")
    def __init__(self): self.pred = 0; self.idx = 0
    def dec(self, code):
        step = IMA_STEP[self.idx]
        d = step >> 3
        if code & 4: d += step
        if code & 2: d += step >> 1
        if code & 1: d += step >> 2
        if code & 8: self.pred -= d
        else: self.pred += d
        if self.pred > 32767: self.pred = 32767
        elif self.pred < -32768: self.pred = -32768
        self.idx += IMA_IDX[code]
        if self.idx < 0: self.idx = 0
        elif self.idx > 88: self.idx = 88
        return self.pred

def adpcm_encode(pcm, st):
    """S16LE bytes -> IMA ADPCM bytes（低半字节在前）"""
    n = len(pcm) // 2
    out = bytearray((n + 1) // 2)
    for i in range(n):
        c = st.enc(struct.unpack_from("<h", pcm, i * 2)[0])
        if i & 1: out[i >> 1] |= (c << 4)
        else: out[i >> 1] = c
    return bytes(out)

def adpcm_decode(data, st):
    """IMA ADPCM bytes -> S16LE bytes"""
    out = array.array("h")
    ap = out.append
    for b in data:
        ap(st.dec(b & 0x0F)); ap(st.dec((b >> 4) & 0x0F))
    return out.tobytes()

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RG660MK 摄像头（画面 + 声音 + 对讲）</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e8ec;
     min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:16px;gap:12px}
h1{font-size:18px;font-weight:600}
.sub{color:#8b8f98;font-size:12px;text-align:center;max-width:640px}
#stage{width:100%;max-width:900px;aspect-ratio:4/3;background:#000;border-radius:12px;overflow:hidden;
       display:flex;align-items:center;justify-content:center;box-shadow:0 8px 30px rgba(0,0,0,.5)}
#stage img{width:100%;height:100%;object-fit:contain}
button{font-size:16px;padding:12px 22px;border-radius:10px;border:1px solid #2b2f36;background:#1a1d23;
       color:#e6e8ec;cursor:pointer}
button.on{background:#16a34a;border-color:#16a34a;color:#fff}
button.talk.on{background:#dc2626;border-color:#dc2626}
.bar{display:flex;gap:10px;flex-wrap:wrap;justify-content:center;align-items:center}
#lw{width:100%;max-width:320px;height:6px;background:#1a1d23;border-radius:3px;overflow:hidden}
#lv{height:100%;width:0;background:#16a34a}
#st{min-height:32px;color:#8b8f98;font-size:12px;text-align:center;max-width:640px}
</style></head><body>
<h1>RG660MK 实时预览（画面 + 声音 + 对讲）</h1>
<div class="sub">Logitech C270 · 低延时低流量：标准≈10KB/s，省流量≈5KB/s｜音频 !SR!Hz ADPCM 4KB/s（不点"开启声音"就不耗流量）｜对讲结束立即释放音箱</div>
<div id="stage"><img id="v" alt="camera"></div>
<div id="lw"><div id="lv"></div></div>
<div class="bar">
  <button id="snd">🔊 点击开启声音</button>
  <button id="talk" class="talk">🎤 对讲</button>
  <button id="rc">↻ 重连画面</button>
  <button id="lite">🐢 省流量：关</button>
  <a href="/stream_hd" style="color:#8b8f98;font-size:13px">高清原画（仅局域网，勿在手机上点）</a>
  <a href="/snapshot" download="snapshot.jpg" style="color:#8b8f98;font-size:13px">截图保存</a>
</div>
<div id="st">手机请用 Chrome/Safari 直接打开；微信内置浏览器可能拿不到麦克风权限。</div>
<script>
var SR=!SR!, wsA=null, wsOpen=false, playing=false, ac=null, gainIn=null, next=0;
var decState=null, encState=null;
var IS=[7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767];
var II=[-1,-1,-1,-1,2,4,6,8,-1,-1,-1,-1,2,4,6,8];
function imaNew(){return {p:0,i:0};}
function imaEnc(s,st){
  var step=IS[st.i], diff=s-st.p, code=0;
  if(diff<0){ code=8; diff=-diff; }
  if(diff>=step){ code|=4; diff-=step; }
  step>>=1;
  if(diff>=step){ code|=2; diff-=step; }
  step>>=1;
  if(diff>=step){ code|=1; }
  step=IS[st.i];
  var d=step>>3;
  if(code&4){ d+=step; } if(code&2){ d+=step>>1; } if(code&1){ d+=step>>2; }
  if(code&8){ st.p-=d; } else { st.p+=d; }
  if(st.p>32767){ st.p=32767; } else if(st.p<-32768){ st.p=-32768; }
  st.i+=II[code]; if(st.i<0){ st.i=0; } else if(st.i>88){ st.i=88; }
  return code;
}
function imaDec(code,st){
  var step=IS[st.i], d=step>>3;
  if(code&4){ d+=step; } if(code&2){ d+=step>>1; } if(code&1){ d+=step>>2; }
  if(code&8){ st.p-=d; } else { st.p+=d; }
  if(st.p>32767){ st.p=32767; } else if(st.p<-32768){ st.p=-32768; }
  st.i+=II[code]; if(st.i<0){ st.i=0; } else if(st.i>88){ st.i=88; }
  return st.p;
}
var talkOn=false, micCtx=null, micStream=null, proc=null, micRatio=1;
function st(t){document.getElementById("st").textContent=t;}
function setLevel(v){document.getElementById("lv").style.width=Math.max(0,Math.min(100,Math.round(v*500)))+"%";}
var vws=null, vurl=null, vfail=0, vfallback=false, lastFrame=0, vwatch=null;
var lite=(location.search.indexOf('lite=1')>=0);
function openVideo(){
  if(vfallback){ document.getElementById("v").src="/stream?"+Date.now(); return; }
  var u=(location.protocol==="https:"?"wss://":"ws://")+location.host+"/wsv"+(lite?"?w=208&q=32&fps=1":"");
  try{ vws=new WebSocket(u); }catch(e){ vfail++; if(vfail>2){ vfallback=true; } return; }
  vws.binaryType="arraybuffer";
  vws.onmessage=function(ev){
    var url=URL.createObjectURL(new Blob([ev.data],{type:"image/jpeg"}));
    var img=document.getElementById("v");
    var old=vurl;
    img.onload=function(){ if(old){ URL.revokeObjectURL(old); } };
    vurl=url; img.src=url; vfail=0; lastFrame=Date.now();
  };
  vws.onclose=function(){ if(!vfallback){ vfail++; if(vfail>3){ vfallback=true; openVideo(); return; } setTimeout(openVideo,1200); } };
  vws.onerror=function(){};
}
function restartVideo(){
  try{ if(vws) vws.close(); }catch(e){}
  vws=null; vfail=0; vfallback=false; openVideo();
}
document.getElementById("rc").onclick=function(){ restartVideo(); };
document.getElementById("lite").onclick=function(){
  lite=!lite; this.textContent = lite? "🐢 省流量：开（≈5KB/s）" : "🐢 省流量：关（≈10KB/s）";
  restartVideo(); st(lite? "省流量模式：1fps/208px（1G/月能用约 50 小时）" : "标准模式：2fps/288px（1G/月约 28 小时）");
};
window.addEventListener("load", function(){
  var lb=document.getElementById("lite");
  if(lite){ lb.textContent="🐢 省流量：开（≈5KB/s）"; }
  openVideo();
  vwatch=setInterval(function(){                 // 连上但没画面 → 回退 HTTP 流
    if(vfallback) return;
    if(lastFrame && Date.now()-lastFrame > 4000){ vfallback=true; try{ if(vws) vws.close(); }catch(e){}
      document.getElementById("v").src="/stream?"+Date.now(); st("画面已切换为 HTTP 低码率流"); }
  }, 2000);
});
function ensureWS(cb){
  if(wsOpen && wsA && wsA.readyState===1){ if(cb) cb(); return; }
  var u=(location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws";
  try{ wsA=new WebSocket(u); }catch(e){ st("❌ 无法建立音频通道"); return; }
  wsA.binaryType="arraybuffer";
  wsA.onopen=function(){ wsOpen=true; decState=imaNew(); if(cb) cb(); };
  wsA.onmessage=function(ev){
    if(typeof ev.data==="string"){
      if(ev.data.indexOf("cfg")===0){ try{ var c=JSON.parse(ev.data.slice(4)); if(c.sr) SR=c.sr; }catch(e){} }
      else if(ev.data.indexOf("busy")===0){ st("⚠️ 有人正在对讲，请稍后再试"); stopTalk(); }
      else if(ev.data.indexOf("spk_busy")===0){ st("⚠️ 设备音箱正被语音助手占用，请稍后再试"); stopTalk(); }
      else if(ev.data.indexOf("timeout")===0){ stopTalk("⏱ 长时间没说话或到时限，已自动释放设备音箱"); }
      return;
    }
    if(!playing || !ac) return;
    if(!decState){ decState=imaNew(); }
    var d=new Uint8Array(ev.data);
    var n=d.length*2;
    var i16=new Int16Array(n);
    for(var k=0;k<d.length;k++){ i16[k*2]=imaDec(d[k]&15,decState); i16[k*2+1]=imaDec((d[k]>>4)&15,decState); }
    var f32=new Float32Array(n);
    for(var i=0;i<n;i++) f32[i]=i16[i]/32768;
    var ab=ac.createBuffer(1,n,SR); ab.copyToChannel(f32,0);
    var s=ac.createBufferSource(); s.buffer=ab; s.connect(gainIn);
    if(next < ac.currentTime+0.02){ next = ac.currentTime + 0.05; }
    s.start(next); next += ab.duration;
  };
  wsA.onclose=function(){ wsOpen=false; if(talkOn){ stopTalk("对讲已断开"); } };
  wsA.onerror=function(){ st("❌ 音频通道出错"); };
}
document.getElementById("snd").onclick=function(){
  var btn=this;
  if(playing){ playing=false; next=0; decState=null; btn.textContent="🔊 点击开启声音"; btn.className=""; st("已停止监听（音频已停发，不耗流量）");
    try{ if(wsA && wsA.readyState===1){ wsA.send("audio_off"); } }catch(e){} return; }
  if(!ac){ ac=new (window.AudioContext||window.webkitAudioContext)(); }
  if(!gainIn){ gainIn=ac.createGain(); gainIn.connect(ac.destination); }
  var go=function(){ playing=true; next=0; decState=imaNew(); btn.textContent="🔊 声音已开启"; btn.className="on"; st("监听中（低延时，8kHz ADPCM）");
    try{ if(wsA && wsA.readyState===1){ wsA.send("audio_on"); } }catch(e){} };
  ensureWS(function(){ if(ac.state==="suspended"){ ac.resume().then(go); } else { go(); } });
};
document.getElementById("talk").onclick=function(){
  if(talkOn){
    try{ if(wsA && wsA.readyState===1){ wsA.send("talk_end"); } }catch(e){}
    stopTalk("已停止对讲（设备音箱已释放）"); return;
  }
  if(!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia){
    st("❌ 此浏览器不支持麦克风（需 HTTPS；微信内置浏览器请改用 Chrome/Safari 打开）"); return;
  }
  var btn=this; btn.textContent="正在请求麦克风…";
  ensureWS(function(){
    navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}})
    .then(function(s){
      micStream=s;
      micCtx=new (window.AudioContext||window.webkitAudioContext)();
      if(micCtx.state==="suspended"){ micCtx.resume(); }
      var src=micCtx.createMediaStreamSource(s);
      proc=micCtx.createScriptProcessor(1024,1,1);
      micRatio=micCtx.sampleRate/SR;
      encState=imaNew();
      proc.onaudioprocess=function(e){
        var inp=e.inputBuffer.getChannelData(0);
        var n=Math.max(1,Math.floor(inp.length/micRatio));
        var i16=new Int16Array(n), sum=0;
        for(var i=0;i<n;i++){
          var v=inp[Math.floor(i*micRatio)]||0;
          sum+=v*v;
          i16[i]=Math.max(-32768,Math.min(32767,Math.round(v*32767)));
        }
        setLevel(Math.sqrt(sum/n));
        if(wsA && wsA.readyState===1){
          var out=new Uint8Array((n+1)>>1);
          for(var k=0;k<n;k++){
            var c=imaEnc(i16[k], encState);
            if(k&1){ out[k>>1] |= (c<<4); } else { out[k>>1]=c; }
          }
          try{ wsA.send(out.buffer); }catch(err){}
        }
      };
      var zero=micCtx.createGain(); zero.gain.value=0;
      src.connect(proc); proc.connect(zero); zero.connect(micCtx.destination);
      talkOn=true;
      btn.textContent="🎤 对讲中（点此结束）"; btn.className="talk on";
      if(gainIn){ gainIn.gain.value=0; }
      st("正在对讲：你的声音从设备音箱播出；结束后立即释放音箱（本机监听已静音防啸叫）");
    }).catch(function(e){ st("❌ 麦克风被拒绝："+e); btn.textContent="🎤 对讲"; });
  });
};
function stopTalk(msg){
  talkOn=false;
  try{ if(proc){ proc.disconnect(); } }catch(e){}
  proc=null;
  try{ if(micStream){ micStream.getTracks().forEach(function(t){ t.stop(); }); } }catch(e){}
  micStream=null;
  try{ if(micCtx){ micCtx.close(); } }catch(e){}
  micCtx=null;
  if(gainIn){ gainIn.gain.value=1; }
  var b=document.getElementById("talk"); b.textContent="🎤 对讲"; b.className="talk";
  setLevel(0);
  if(msg){ st(msg); }
}
</script></body></html>"""

class TalkSess:
    """一次对讲会话：懒启动 aplay，静默/结束立即释放。"""
    def __init__(self, send, peer):
        self.send = send; self.peer = peer
        self.q = queue.Queue(maxsize=40); self.p = None; self.dec = ImaDec()
        self.stop_flag = threading.Event()
        with TALK_LOCK:
            acquired = not TALK["active"]
            if acquired:
                TALK.update({"active": True, "peer": peer, "started": time.time(), "bytes": 0})
        self.acquired = acquired
        self.busy = not acquired
        if acquired:
            STATS["talk"] += 1
            threading.Thread(target=self._writer, daemon=True).start()

    def _writer(self):
        for attempt in range(7):
            try:
                self.p = subprocess.Popen(["aplay","-D",SPK,"-f","S16_LE","-r",str(RATE),"-c","1","-t","raw","-q",
                                           "--buffer-time=120000","--period-time=30000"],
                                          stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                break
            except Exception:
                time.sleep(0.4)
        if self.p is None:
            try: self.send(1, b"spk_busy")
            except Exception: pass
            self.stop(); return
        STATS["spk"] = SPK
        sys.stderr.write("talk: spk acquired %s\n" % SPK); sys.stderr.flush()
        try:
            while not self.stop_flag.is_set():
                try: d = self.q.get(timeout=0.5)
                except queue.Empty: continue
                if d is None: break
                self.p.stdin.write(d); self.p.stdin.flush()
                TALK["bytes"] += len(d); STATS["talk_bytes"] += len(d)
        except Exception:
            pass
        finally:
            try: self.p.stdin.close()
            except Exception: pass
            try: self.p.kill()
            except Exception: pass
            try: self.p.wait(timeout=3)
            except Exception: pass
            sys.stderr.write("talk: spk released %s\n" % SPK); sys.stderr.flush()

    def write(self, adpcm):
        try: self.q.put_nowait(self.dec.__class__ and adpcm_decode(adpcm, self.dec))
        except Exception: pass

    def stop(self):
        if self.stop_flag.is_set(): return
        self.stop_flag.set()
        try: self.q.put_nowait(None)
        except Exception: pass
        if self.acquired:
            with TALK_LOCK:
                TALK["active"] = False
            STATS["talk"] = max(0, STATS["talk"] - 1)

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def handle_error(self, *a): pass          # 客户端断流/重置不再刷 traceback

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        qs = urllib.parse.parse_qs(u.query)
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        if p == "/":
            body = PAGE.replace("!SR!", str(RATE)).replace("!FPS!", ("%g" % VID_F)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if p == "/stream":
            self.stream(qs); return
        if p == "/stream_hd":
            self.proxy("/stream"); return
        if p == "/snapshot":
            self.proxy("/snapshot"); return
        if p == "/audio":
            self.audio(); return
        if p == "/ws":
            self.ws_endpoint(); return
        if p == "/wsv":
            self.ws_video(qs); return
        if p == "/talk":
            self.talk_only(); return
        if p == "/tone":
            dev = (qs.get("dev") or [SPK])[0]
            self.tone(dev); return
        if p == "/status":
            b = ("mic=%s@%dHz bytes=%d listeners=%d ws=%d uptime=%.0fs | talk_active=%s peer=%s talk_bytes=%d | "
                 "video_seq=%d video_kb=%.1f | spk=%s vid=%dpx/q%d/%gfps\n" %
                 (STATS["dev"], RATE, STATS["bytes"], STATS["clients"], STATS["ws"],
                  time.time()-STATS["since"] if STATS["since"] else 0,
                  TALK["active"], TALK["peer"], TALK["bytes"],
                  VF["seq"], (len(VF["data"])/1024 if VF["data"] else 0), SPK, VID_W, VID_Q, VID_F)).encode()
            self.send_response(200); self.send_header("Content-Type","text/plain")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.send_response(404); self.send_header("Content-Length","2"); self.end_headers(); self.wfile.write(b"no")

    # ---------- 低码率视频：只发最新帧 ----------
    def stream(self, qs):
        def num(key, default, lo, hi, cast=float):
            try: return max(lo, min(hi, cast((qs.get(key) or [default])[0])))
            except Exception: return default
        w   = int(num("w", VID_W, 160, 1280, int))
        q   = int(num("q", VID_Q, 20, 85, int))
        fps = num("fps", VID_F, 1.0, 15.0)
        ensure_video()
        interval = 1.0 / fps
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, private")
        self.send_header("Transfer-Encoding", "chunked")     # 必须 chunked：否则 CF 隧道只回 200 不给数据
        self.send_header("Connection", "close")
        self.end_headers()
        last_seq = 0; last_t = 0.0
        STATS["clients"] += 1
        try:
            while True:
                with VFLOCK:
                    if VF["seq"] == last_seq:
                        VFLOCK.wait(1.0)
                    seq = VF["seq"]; fr = VF["data"]
                if fr is None or seq == last_seq:
                    continue
                last_seq = seq
                now = time.time()
                if now - last_t < interval:
                    continue                      # 不够快就丢帧：永远发最新的一帧
                jpg = transcode(seq, fr, w, q)
                part = (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg)) + jpg
                self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
                self.wfile.flush()
                last_t = now
        except Exception:
            pass
        finally:
            STATS["clients"] -= 1

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
                pos = max(POS - int(PRE * RATE * 2), start)
            while True:
                with CV:
                    start = POS - len(BUF)
                    if pos < start: pos = start
                    while pos >= POS:
                        CV.wait(1.0)
                    off = pos - start
                    data = bytes(BUF[off:off+1024])
                    pos += len(data)
                if data:
                    self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
                    try: self.wfile.flush()
                    except Exception: pass
        except Exception:
            pass
        finally:
            STATS["clients"] -= 1

    # ---------- WebSocket：握手 ----------
    def hs(self):
        if self.headers.get("Upgrade", "").lower() != "websocket": return False
        key = self.headers.get("Sec-WebSocket-Key")
        if not key: return False
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept(key))
        self.end_headers()
        return True

    def ws_endpoint(self):
        if not self.hs(): return
        send_lock = threading.Lock()
        def send(op, payload=b""):
            with send_lock:
                ws_send(self.wfile, op, payload)
        STATS["ws"] += 1
        ensure_reader()
        stop = threading.Event()
        play = {"on": False, "enc": ImaEnc()}      # 不按"开启声音"就不发音频（省流量）
        sess = {"s": None, "last": 0.0}

        def down():
            with CV:
                pos = max(POS - int(PRE * RATE * 2), POS - len(BUF))
            while not stop.is_set():
                with CV:
                    start = POS - len(BUF)
                    if pos < start: pos = start
                    while pos >= POS and not stop.is_set():
                        CV.wait(0.5)
                    off = pos - start
                    data = bytes(BUF[off:off+1024])
                    pos += len(data)
                if data and play["on"]:
                    try: send(2, adpcm_encode(data, play["enc"]))
                    except Exception: break
            stop.set()

        threading.Thread(target=down, daemon=True).start()
        try: send(1, ("cfg {\"sr\":%d,\"codec\":\"adpcm\"}" % RATE).encode())
        except Exception: pass
        try:
            while True:
                fr = ws_read(self.rfile)
                if fr is None: break
                op, data = fr
                if op == 8: break
                if op == 9:
                    try: send(10, data)
                    except Exception: pass
                    continue
                if op == 10: continue
                if op == 1:
                    cmd = data.strip().lower()
                    if cmd == b"audio_on":
                        play["enc"] = ImaEnc(); play["on"] = True
                    elif cmd == b"audio_off":
                        play["on"] = False
                    elif cmd == b"talk_end":
                        if sess["s"]: sess["s"].stop(); sess["s"] = None
                    continue
                if op == 2 and data:
                    if sess["s"] is None:
                        ts = TalkSess(send, self.client_address[0])
                        if ts.busy:                       # 已有别人在对讲
                            send(1, b"busy"); continue
                        sess["s"] = ts
                    try: sess["s"].write(data)
                    except Exception: pass
                    sess["last"] = time.time()
                # 静默/超时自动释放
                if sess["s"] is not None:
                    why = ""
                    if time.time() - sess["last"] > IDLE: why = "idle"
                    elif time.time() - TALK.get("started", time.time()) > TMAX: why = "max"
                    if why:
                        try: send(1, ("timeout_" + why).encode())
                        except Exception: pass
                        sess["s"].stop(); sess["s"] = None
        except Exception:
            pass
        if sess["s"]: sess["s"].stop()
        stop.set()
        STATS["ws"] = max(0, STATS["ws"] - 1)

    def ws_video(self, qs=None):
        """视频走独立 WebSocket（CF/穿透隧道对 HTTP 流做字节缓冲，对 WS 不缓冲）。
        支持 ?w=&q=&fps= 降码率：省流量模式用 w=208&q=32&fps=1。"""
        if not self.hs(): return
        qs = qs or {}
        def num(k, d, lo, hi, cast=float):
            try: return max(lo, min(hi, cast((qs.get(k) or [d])[0])))
            except Exception: return d
        W = int(num("w", VID_W, 160, 1280, int))
        Q = int(num("q", VID_Q, 20, 85, int))
        F = num("fps", VID_F, 1.0, 15.0)
        ensure_video()
        lock = threading.Lock()
        def send(op, payload=b""):
            with lock:
                ws_send(self.wfile, op, payload)
        try: send(1, ("cfgv {\"fps\":%g,\"w\":%d,\"q\":%d}" % (F, W, Q)).encode())
        except Exception: pass
        last_seq = 0; last_t = 0.0
        try:
            while True:
                with VFLOCK:
                    if VF["seq"] == last_seq:
                        VFLOCK.wait(1.0)
                    seq = VF["seq"]; fr = VF["data"]
                if fr is None or seq == last_seq:
                    continue
                last_seq = seq
                now = time.time()
                if now - last_t < 1.0 / F:
                    continue                    # 只发最新帧：慢客户端只丢帧，不积压出延时
                try:
                    send(2, transcode(seq, fr, W, Q))
                except Exception:
                    break
                last_t = now
        except Exception:
            pass

    def talk_only(self):
        if not self.hs(): return
        send_lock = threading.Lock()
        def send(op, payload=b""):
            with send_lock:
                ws_send(self.wfile, op, payload)
        stop = threading.Event()
        try:
            self.talk_loop(send, stop)
        except Exception:
            pass

    # ---------- 上行对讲：借用喇叭 -> 结束时立即释放 ----------
    def talk_loop(self, send, stop):
        with TALK_LOCK:
            busy = TALK["active"]
            if not busy:
                TALK.update({"active": True, "peer": self.client_address[0],
                             "started": time.time(), "bytes": 0})
        if busy:
            try: send(1, b"busy")
            except Exception: pass
            return
        STATS["talk"] += 1
        q = queue.Queue(maxsize=40)
        dec = ImaDec()
        wstop = threading.Event()
        bflag = threading.Event()
        stt = {"last": time.time(), "started": time.time()}

        def writer():
            p = None
            for attempt in range(7):            # 语音助手可能正占着喇叭，重试等它
                try:
                    p = subprocess.Popen(["aplay","-D",SPK,"-f","S16_LE","-r",str(RATE),"-c","1","-t","raw","-q",
                                          "--buffer-time=120000","--period-time=30000"],
                                         stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    break
                except Exception as e:
                    sys.stderr.write("talk: aplay busy/fail (%s) retry %d\n" % (e, attempt)); sys.stderr.flush()
                    time.sleep(0.4)
            if p is None:
                bflag.set(); wstop.set(); return
            STATS["spk"] = SPK
            sys.stderr.write("talk: spk acquired %s\n" % SPK); sys.stderr.flush()
            try:
                while not wstop.is_set():
                    try: d = q.get(timeout=0.5)
                    except queue.Empty: continue
                    if d is None: break
                    p.stdin.write(d); p.stdin.flush()
                    TALK["bytes"] += len(d); STATS["talk_bytes"] += len(d)
            except Exception:
                pass
            finally:                            # 立即释放喇叭
                try: p.stdin.close()
                except Exception: pass
                try: p.kill()
                except Exception: pass
                try: p.wait(timeout=3)
                except Exception: pass
                sys.stderr.write("talk: spk released %s\n" % SPK); sys.stderr.flush()

        def watchdog():
            why = ""
            while not wstop.is_set():
                time.sleep(0.5)
                if time.time() - stt["last"] > IDLE:      why = "idle"; break
                if time.time() - stt["started"] > TMAX:   why = "max";  break
            if why and not wstop.is_set():
                sys.stderr.write("talk: %s timeout -> release speaker\n" % why); sys.stderr.flush()
                try: send(1, ("timeout_" + why).encode())
                except Exception: pass
                try: self.connection.shutdown(socket.SHUT_RDWR)
                except Exception: pass

        threading.Thread(target=writer, daemon=True).start()
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            while True:
                if bflag.is_set():
                    try: send(1, b"spk_busy")
                    except Exception: pass
                    break
                fr = ws_read(self.rfile)
                if fr is None: break
                op, data = fr
                if op == 8: break
                if op == 9:
                    try: send(10, data)
                    except Exception: pass
                    continue
                if op == 10: continue
                if op == 1 and data.strip() == b"talk_end":
                    sys.stderr.write("talk: client end\n"); sys.stderr.flush(); break
                if op in (1, 2) and data:
                    stt["last"] = time.time()
                    try: q.put_nowait(adpcm_decode(data, dec))
                    except Exception: pass
        except Exception:
            pass
        wstop.set()
        try: q.put_nowait(None)
        except Exception: pass
        with TALK_LOCK:
            TALK["active"] = False
        STATS["talk"] = max(0, STATS["talk"] - 1)
        stop.set()

    def tone(self, dev):
        try:
            p = subprocess.Popen(["aplay","-D",dev,"-f","S16_LE","-r",str(RATE),"-c","1","-t","raw","-q"],
                                 stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            n = int(RATE * 1.5)
            buf = bytearray()
            for i in range(n):
                f = 440.0 if i < n//2 else 660.0
                env = min(1.0, i/400.0, (n-i)/400.0)
                buf += struct.pack("<h", int(11000 * env * math.sin(2*math.pi*f*i/RATE)))
            p.stdin.write(bytes(buf)); p.stdin.flush(); p.stdin.close()
            rc = p.wait(timeout=20)
            msg = "tone ok dev=%s rc=%s rate=%d" % (dev, rc, RATE)
        except Exception as e:
            msg = "tone err dev=%s %s" % (dev, e)
        b = msg.encode()
        self.send_response(200); self.send_header("Content-Type","text/plain")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == "__main__":
    sys.stderr.write("camav v3: port=%d spk=%s mic=%s (%d->%dHz) video=%dpx/q%d/%gfps\n"
                     % (PORT, SPK, MICS, CAPR, RATE, VID_W, VID_Q, VID_F)); sys.stderr.flush()
    TS(("0.0.0.0", PORT), H).serve_forever()
