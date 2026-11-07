#!/usr/bin/env python3
"""attr_live.py — 人脸属性实时预览页（独立页面，完全不碰 8099）

为什么单独做页面而不改 8099：
  8099 的页面内嵌在 aicpe_demo.py 的 DASH_HTML 字符串里，改它必须重启 aicpe-board，
  会造成 2-5 秒中断且有起不来的风险。演示主线不能赌，所以做独立页面。

架构：
  浏览器 ──GET /frame──> 本服务(:8094)
                          ├─ 抓设备 camview:8090/snapshot（只读）
                          ├─ analyse()：YuNet 检测 + genderage 推理
                          └─ 返回 {img: base64 jpg, faces: [...], ms}
  浏览器把 base64 图放进 <img>，用 canvas 叠画人脸框 —— 图和框严格同帧，不会错位

用法: python3 attr_live.py [--port 8094] [--device http://192.168.1.1:8090]
访问: http://192.168.1.212:8094/
"""
import argparse, base64, json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

import attr_service_pc as core

DEVICE = "http://192.168.1.1:8090"
_fetch_lock = threading.Lock()

PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>人脸属性 · 实时预览</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0b1220;color:#e8eef7;font-family:"Noto Sans CJK SC","Source Han Sans SC",system-ui,sans-serif;
     min-height:100vh;display:flex;flex-direction:column}
header{padding:14px 22px;background:linear-gradient(135deg,#0b2545,#134074 55%,#1b6ca8);
       display:flex;align-items:center;gap:14px;box-shadow:0 2px 14px rgba(0,0,0,.45)}
header h1{font-size:17px;font-weight:700;letter-spacing:.4px}
header .badge{font-size:11.5px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.26);
              border-radius:11px;padding:2px 10px}
header .sp{flex:1}
header .st{font-size:12px;display:flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;background:#38d39f;box-shadow:0 0 8px #38d39f}
.dot.bad{background:#ff5d5d;box-shadow:0 0 8px #ff5d5d}
main{flex:1;display:flex;gap:18px;padding:18px 22px;align-items:flex-start;flex-wrap:wrap}
.stage{position:relative;flex:1 1 640px;max-width:820px;background:#000;border-radius:10px;overflow:hidden;
       box-shadow:0 6px 28px rgba(0,0,0,.55);border:1px solid #1e3350;aspect-ratio:4/3}
.stage img{width:100%;height:100%;object-fit:contain;display:block}
.stage canvas{position:absolute;inset:0;width:100%;height:100%}
.side{flex:0 0 300px;display:flex;flex-direction:column;gap:12px}
.card{background:#111c30;border:1px solid #1e3350;border-radius:9px;padding:14px 16px}
.card h2{font-size:12px;color:#7f9ac0;font-weight:600;letter-spacing:.5px;margin-bottom:9px}
.big{font-size:34px;font-weight:700;line-height:1.1;color:#5eead4}
.big.male{color:#7dd3fc}.big.female{color:#f9a8d4}.big.uncertain{color:#94a3b8}
.sub{font-size:13px;color:#9fb3cd;margin-top:6px}
.band{font-size:22px;font-weight:700;color:#fbbf24}
.row{display:flex;justify-content:space-between;font-size:12.5px;color:#9fb3cd;padding:4px 0;
     border-bottom:1px dashed #1e3350}
.row:last-child{border:0}
.row b{color:#e8eef7;font-weight:600}
.empty{color:#64748b;font-size:13.5px;line-height:1.6}
footer{padding:10px 22px;font-size:11.5px;color:#5b7290;border-top:1px solid #16263c}
@media(max-width:900px){.side{flex:1 1 100%}.big{font-size:28px}}
</style></head>
<body>
<header>
  <h1>人脸属性 · 实时预览</h1>
  <span class="badge">性别识别</span>
  <span class="badge">旁路部署 · 不影响 8099</span>
  <span class="sp"></span>
  <span class="st"><span class="dot" id="dot"></span><span id="stxt">连接中…</span></span>
</header>
<main>
  <div class="stage">
    <img id="cam" alt="等待画面">
    <canvas id="ov"></canvas>
  </div>
  <aside class="side">
    <div class="card">
      <h2>识别结果</h2>
      <div id="panel"><div class="empty">等待检出人脸…<br>请正对摄像头，距离 50–80 cm</div></div>
    </div>
    <div class="card">
      <h2>运行状态</h2>
      <div class="row"><span>推理耗时</span><b id="ms">—</b></div>
      <div class="row"><span>检出人脸</span><b id="cnt">—</b></div>
      <div class="row"><span>刷新帧率</span><b id="fps">—</b></div>
      <div class="row"><span>画幅</span><b id="dim">—</b></div>
    </div>
  </aside>
</main>
<footer>数据源：RG660MK camview:8090/snapshot（只读）｜ 推理在本机执行，设备 CPU 零占用 ｜ 年龄与情绪经实测不具备交付条件，均已移除</footer>
<script>
const cam=document.getElementById('cam'), ov=document.getElementById('ov'), ctx=ov.getContext('2d');
const dot=document.getElementById('dot'), stxt=document.getElementById('stxt');
let n=0, t0=performance.now(), fails=0;

function drawBoxes(faces, w, h){
  ov.width=w; ov.height=h;
  ctx.clearRect(0,0,w,h);
  faces.forEach(f=>{
    const [x,y,bw,bh]=f.bbox;
    const bad = f.gender==='uncertain';
    ctx.strokeStyle = bad ? '#94a3b8' : '#22d3ee';
    ctx.lineWidth = Math.max(2, w/300);
    ctx.strokeRect(x,y,bw,bh);
    // 角标线
    const len = Math.max(10, w/40);
    ctx.lineWidth = Math.max(3, w/200);
    [[x,y,1,1],[x+bw,y,-1,1],[x,y+bh,1,-1],[x+bw,y+bh,-1,-1]].forEach(([px,py,sx,sy])=>{
      ctx.beginPath(); ctx.moveTo(px, py+sy*len); ctx.lineTo(px,py); ctx.lineTo(px+sx*len, py); ctx.stroke();
    });
    const label = bad ? '无法判定' : (f.gender==='male'?'男':'女');
    ctx.font = `600 ${Math.max(13, w/36)}px "Noto Sans CJK SC",system-ui,sans-serif`;
    const tw = ctx.measureText(label).width + 14;
    const th = Math.max(20, w/28);
    ctx.fillStyle = bad ? 'rgba(148,163,184,.88)' : 'rgba(34,211,238,.92)';
    ctx.fillRect(x, Math.max(0,y-th), tw, th);
    ctx.fillStyle = '#04121f';
    ctx.fillText(label, x+7, Math.max(th-6, y-6));
  });
}

async function tick(){
  try{
    const r = await fetch('/frame', {cache:'no-store'});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'bad');
    cam.src = 'data:image/jpeg;base64,' + d.img;
    cam.onload = ()=>drawBoxes(d.faces, d.w, d.h);
    // 侧栏
    const faces = d.faces || [];
    const p = document.getElementById('panel');
    if(faces.length){
      p.innerHTML = faces.map((f,i)=>{
        const cls = f.gender==='uncertain' ? 'uncertain' : f.gender;
        const txt = f.gender==='uncertain' ? '无法判定' : (f.gender==='male'?'男':'女');
        const warn = f.note==='low_confidence_face' ? '<div class="sub" style="color:#f59e0b;margin-top:4px">人脸不够清晰，已拒绝输出</div>' : '';
        const multi = faces.length>1 ? `<div class="sub" style="color:#7f9ac0">人脸 ${i+1}</div>` : '';
        return `<div style="padding:6px 0;border-bottom:1px dashed #1e3350">`
          + multi
          + `<span class="big ${cls}" style="font-size:${faces.length>1?25:34}px">${txt}</span>`
          + `<div class="sub">置信度 ${f.gender_score}</div>${warn}</div>`;
      }).join('');
    } else {
      p.innerHTML = '<div class="empty">未检出人脸<br>请正对摄像头，距离 50–80 cm</div>';
    }
    document.getElementById('ms').textContent = d.ms + ' ms';
    document.getElementById('cnt').textContent = (d.faces?d.faces.length:0) + ' 张';
    document.getElementById('dim').textContent = d.w + '×' + d.h;
    n++; const el=(performance.now()-t0)/1000;
    if(el>1){ document.getElementById('fps').textContent = (n/el).toFixed(1) + ' fps'; }
    dot.className='dot'; stxt.textContent='实时'; fails=0;
  }catch(e){
    fails++;
    if(fails>2){ dot.className='dot bad'; stxt.textContent='连接中断'; }
  }
  setTimeout(tick, 150);
}
tick();
</script>
</body></html>
"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
            return
        if self.path == "/frame":
            try:
                with _fetch_lock:
                    img = core.grab(DEVICE, timeout=5)
                    res = core.analyse(img)
                ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                if not ok:
                    raise RuntimeError("jpeg encode failed")
                h, w = img.shape[:2]
                self._send(200, json.dumps({
                    "ok": True,
                    "img": base64.b64encode(buf.tobytes()).decode(),
                    "w": w, "h": h,
                    "faces": res["faces"],
                    "ms": res["elapsed_ms"],
                }))
            except Exception as e:
                self._send(503, json.dumps({"ok": False, "error": str(e)}))
            return
        self._send(404, json.dumps({"ok": False, "error": "not found"}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8094)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--device", default="http://192.168.1.1:8090")
    a = ap.parse_args()
    DEVICE = a.device
    print(f"models: {core.load_models()}", flush=True)
    print(f"attr_live on http://{a.host}:{a.port}  device={DEVICE}", flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()
