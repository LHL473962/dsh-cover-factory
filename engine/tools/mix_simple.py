
# -*- coding: utf-8 -*-
"""混音实时台（音频元素版，最稳）：两个 <audio> + GainNode，滑杆实时改增益。
用法: python mix_simple.py --vA a.wav --vB b.wav --inst inst.wav [--port 8897]
"""
import argparse, os, urllib.parse, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
HTML = r"""<!doctype html><meta charset="utf-8"><title>混音实时台</title>
<style>
body{background:#0e1220;color:#e8ecf6;font:14px/1.6 "Microsoft YaHei",sans-serif;margin:0;padding:20px}
h1{font-size:20px;margin:0 0 2px}.sub{color:#8a96b4;margin-bottom:12px}
.card{background:#161c2e;border:1px solid #26304a;border-radius:12px;padding:14px;max-width:820px}
label{display:block;margin:10px 0 2px;color:#b8c4dc}input[type=range]{width:100%}
.val{color:#6ea8ff;font-weight:700}
button{background:#3b6cff;color:#fff;border:0;border-radius:8px;padding:8px 13px;cursor:pointer;margin:6px 6px 0 0}
button.ghost{background:#252f47}button.on{background:#22c55e}
#bar{height:14px;background:#0b1020;border:1px solid #26304a;border-radius:7px;margin:12px 0 4px;cursor:pointer;position:relative;overflow:hidden}
#fill{height:100%;width:0;background:linear-gradient(90deg,#3b6cff,#7aa2ff)}
#time{color:#8a96b4;font-size:12px}
#st{color:#9fb0d0;font-size:12px;margin-top:8px;white-space:pre-wrap}
</style>
<h1>混音实时台</h1><div class="sub">人声/伴奏分轨播放，滑杆实时改增益。切换 A/B 只换人声。</div>
<div class="card">
 <button id="play" onclick="toggle()">▶ 播放</button>
 <button class="ghost" onclick="pick('A')" id="bA">人声 A</button>
 <button class="ghost" onclick="pick('B')" id="bB">人声 B</button>
 <button class="ghost" onclick="soloV()" id="bSv">只人声</button>
 <button class="ghost" onclick="soloI()" id="bSi">只伴奏</button>
 <div id="bar" onclick="seek(event)"><div id="fill"></div></div>
 <div id="time">0:00 / 0:00</div>
 <label>人声增益 <span class="val" id="o_vocal">0.0 dB</span></label>
 <input type="range" id="vocal" min="-20" max="6" step="0.5" value="0">
 <label>伴奏增益 <span class="val" id="o_bed">0.0 dB</span></label>
 <input type="range" id="bed" min="-20" max="6" step="0.5" value="0">
 <div id="st">等待加载…</div>
</div>
<audio id="av" preload="auto" loop></audio>
<audio id="ai" preload="auto" loop></audio>
<script>
let ctx=null, gv=null, gi=null, cur='B', playing=false, muteV=false, muteI=false;
const el=id=>document.getElementById(id);
const fmt=v=>v.toFixed(1)+' dB';
function setSt(t){el('st').textContent=t;}
function tfmt(t){t=Math.max(0,t||0);return Math.floor(t/60)+':'+('0'+Math.floor(t%60)).slice(-2);}
function dur(){return el('ai').duration||el('av').duration||0;}
function setup(){
 if(ctx)return;
 try{
  ctx=new (window.AudioContext||window.webkitAudioContext)();
  const sv=ctx.createMediaElementSource(el('av')); gv=ctx.createGain(); sv.connect(gv).connect(ctx.destination);
  const si=ctx.createMediaElementSource(el('ai')); gi=ctx.createGain(); si.connect(gi).connect(ctx.destination);
  setSt('音频引擎就绪');
 }catch(e){setSt('音频引擎失败: '+e);}
}
function apply(){
 const v=parseFloat(el('vocal').value), b=parseFloat(el('bed').value);
 el('o_vocal').textContent=fmt(v); el('o_bed').textContent=fmt(b);
 el('av').volume=1; el('ai').volume=1;
 if(gv) gv.gain.value = muteV?0:Math.pow(10,v/20);
 if(gi) gi.gain.value = muteI?0:Math.pow(10,b/20);
}
el('vocal').addEventListener('input',apply); el('bed').addEventListener('input',apply);
function loadV(){
 const src=(cur==='A')?'/audio/vA':'/audio/vB';
 el('av').src=src; el('av').load();
 el('bA').className=(cur==='A')?'on':'ghost'; el('bB').className=(cur==='B')?'on':'ghost';
}
function pick(w){cur=w; loadV(); if(playing){el('av').currentTime=el('ai').currentTime; el('av').play();}}
function toggle(){
 setup(); if(ctx && ctx.state==='suspended') ctx.resume();
 if(playing){el('av').pause();el('ai').pause();playing=false;el('play').textContent='▶ 播放';return;}
 el('av').currentTime=el('ai').currentTime;
 Promise.all([el('av').play(),el('ai').play()]).then(()=>{playing=true;el('play').textContent='⏸ 暂停';}).catch(e=>setSt('播放失败: '+e));
}
function seek(e){const r=el('bar').getBoundingClientRect();const p=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));const t=p*dur();el('av').currentTime=t;el('ai').currentTime=t;}
function soloV(){muteI=!muteI;muteV=false;el('bSv').className=muteI?'on':'ghost';el('bSi').className='ghost';apply();}
function soloI(){muteV=!muteV;muteI=false;el('bSi').className=muteV?'on':'ghost';el('bSv').className='ghost';apply();}
function tick(){
 if(playing){const t=el('ai').currentTime;el('fill').style.width=(t/Math.max(dur(),0.01)*100)+'%';el('time').textContent=tfmt(t)+' / '+tfmt(dur());}
 requestAnimationFrame(tick);
}
el('ai').addEventListener('loadedmetadata',()=>{el('time').textContent='0:00 / '+tfmt(dur());setSt('伴奏时长 '+tfmt(dur())+'s');});
el('av').addEventListener('error',()=>setSt('人声加载失败: '+el('av').src));
el('ai').addEventListener('error',()=>setSt('伴奏加载失败: '+el('ai').src));
loadV(); apply(); tick();
</script>"""

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send_file(self,path,ctype):
        try: data=open(path,'rb').read()
        except Exception:
            self.send_response(404); self.end_headers(); self.wfile.write(b'not found'); return
        self.send_response(200); self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        u=urllib.parse.urlparse(self.path)
        if u.path=='/':
            d=HTML.encode('utf-8'); self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(d)))
            self.end_headers(); self.wfile.write(d); return
        m={'/audio/vA':ARGS.vA,'/audio/vB':ARGS.vB,'/audio/inst':ARGS.inst}
        if u.path in m: self.send_file(m[u.path], mimetypes.guess_type(m[u.path])[0] or 'audio/wav'); return
        self.send_response(404); self.end_headers()

def main():
    global ARGS
    ap=argparse.ArgumentParser()
    ap.add_argument('--vA',required=True); ap.add_argument('--vB',required=True); ap.add_argument('--inst',required=True)
    ap.add_argument('--port',type=int,default=8897)
    ARGS=ap.parse_args()
    srv=ThreadingHTTPServer(('127.0.0.1',ARGS.port),H)
    print('simple studio: http://127.0.0.1:%d'%ARGS.port,flush=True)
    srv.serve_forever()

if __name__=='__main__': main()
