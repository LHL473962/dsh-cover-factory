
# -*- coding: utf-8 -*-
"""混音实时试听台（Web Audio，浏览器端实时处理）。
用法: python mix_live.py --vA a.wav --vB b.wav --inst inst.wav [--ir ir.wav] [--port 8898]
"""
import argparse, os, urllib.parse, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None

HTML = r"""<!doctype html><meta charset="utf-8"><title>混音实时台</title>
<style>
body{background:#0e1220;color:#e8ecf6;font:14px/1.6 "Microsoft YaHei",sans-serif;margin:0;padding:20px}
h1{font-size:20px;margin:0 0 2px}.sub{color:#8a96b4;margin-bottom:14px}
.row{display:flex;gap:16px;flex-wrap:wrap}.card{background:#161c2e;border:1px solid #26304a;border-radius:12px;padding:14px;flex:1;min-width:340px}
label{display:block;margin:8px 0 2px;color:#b8c4dc}input[type=range]{width:100%}
.val{color:#6ea8ff;font-weight:700}
button{background:#3b6cff;color:#fff;border:0;border-radius:8px;padding:8px 13px;cursor:pointer;margin:6px 6px 0 0}
button.ghost{background:#252f47}button.on{background:#22c55e}
#bar{height:14px;background:#0b1020;border:1px solid #26304a;border-radius:7px;margin:12px 0 4px;cursor:pointer;position:relative;overflow:hidden}
#fill{height:100%;width:0;background:linear-gradient(90deg,#3b6cff,#7aa2ff)}
#time{color:#8a96b4;font-size:12px}
</style>
<h1>混音实时台</h1><div class="sub">滑杆即时生效（Web Audio），循环播放。切换 A/B 只换人声。</div>
<div class="row">
 <div class="card">
  <button id="play" onclick="toggle()">▶ 播放</button>
  <button class="ghost" onclick="pick('A')" id="bA">人声 A</button>
  <button class="ghost" onclick="pick('B')" id="bB">人声 B</button>
  <button class="ghost" onclick="solo('v')" id="bSv">只人声</button>
  <button class="ghost" onclick="solo('i')" id="bSi">只伴奏</button>
  <div id="bar" onclick="seek(event)"><div id="fill"></div></div>
  <div id="time">0:00 / 0:00</div>
  <div style="color:#8a96b4;margin-top:6px;font-size:12px" id="st"></div>
 </div>
 <div class="card">
  <label>人声增益 <span class="val" id="o_vocal">0.0 dB</span></label><input type="range" id="vocal" min="-8" max="8" step="0.5" value="0">
  <label>伴奏衰减 <span class="val" id="o_bed">0.0 dB</span></label><input type="range" id="bed" min="-6" max="6" step="0.5" value="0">
  <label>混响湿 <span class="val" id="o_wet">0.12</span></label><input type="range" id="wet" min="0" max="0.5" step="0.01" value="0.12">
  <label>人声 3k 存在感 <span class="val" id="o_pres">2.0 dB</span></label><input type="range" id="pres" min="-3" max="8" step="0.5" value="2">
  <label>人声 9k 空气感 <span class="val" id="o_air">1.2 dB</span></label><input type="range" id="air" min="-3" max="6" step="0.5" value="1.2">
  <label>人声低频 260Hz <span class="val" id="o_warm">-1.2 dB</span></label><input type="range" id="warm" min="-6" max="2" step="0.2" value="-1.2">
  <label>总线压缩比 <span class="val" id="o_comp">1.3</span></label><input type="range" id="comp" min="1" max="4" step="0.1" value="1.3">
  <label>输出增益 <span class="val" id="o_master">0.0 dB</span></label><input type="range" id="master" min="-12" max="6" step="0.5" value="0">
  <div style="color:#8a96b4;margin-top:8px">调不通先点“只人声”，确认人声轨有声音。</div>
 </div>
</div>
<script>
window.onerror=function(m,src,l,c,e){var st=document.getElementById('st');if(st)st.textContent='JS错误: '+m+' @'+l;};
let ctx,bufs={},nodes=null,playing=false,current='B',offset=0,startCtx=0;
const el=id=>document.getElementById(id);
const fmt={vocal:v=>v.toFixed(1)+' dB',bed:v=>v.toFixed(1)+' dB',wet:v=>v.toFixed(2),pres:v=>v.toFixed(1)+' dB',air:v=>v.toFixed(1)+' dB',warm:v=>v.toFixed(1)+' dB',comp:v=>v.toFixed(1),master:v=>v.toFixed(1)+' dB'};
const ids=Object.keys(fmt);
function show(){for(const k of ids)el('o_'+k).textContent=fmt[k](parseFloat(el(k).value));}
ids.forEach(k=>el(k).addEventListener('input',()=>{show();apply();}));show();
function dur(){return bufs.inst?bufs.inst.duration:0;}
function tfmt(t){t=Math.max(0,t);return Math.floor(t/60)+':'+('0'+Math.floor(t%60)).slice(-2);}
async function load(url){const r=await fetch(url);const ab=await r.arrayBuffer();return await ctx.decodeAudioData(ab);}
async function init(){
 if(ctx)return;
 ctx=new (window.AudioContext||window.webkitAudioContext)();
 el('st').textContent='加载音频…';
 bufs.vA=await load('/audio/vA');bufs.vB=await load('/audio/vB');bufs.inst=await load('/audio/inst');bufs.ir=await load('/audio/ir');
 const pk=(b)=>{let m=0;for(let c=0;c<b.numberOfChannels;c++){const d=b.getChannelData(c);for(let i=0;i<d.length;i+=100){const v=Math.abs(d[i]);if(v>m)m=v;}}return (20*Math.log10(m||1e-9)).toFixed(1);};
 el('st').textContent='就绪｜人声A '+tfmt(bufs.vA.duration)+'s 峰值'+pk(bufs.vA)+'dB｜人声B '+tfmt(bufs.vB.duration)+'s 峰值'+pk(bufs.vB)+'dB';
 el('time').textContent='0:00 / '+tfmt(dur());
}
function build(off){
 const c=ctx;
 const mk=(type,f,q,g)=>{const b=c.createBiquadFilter();b.type=type;b.frequency.value=f;if(q)b.q||(b.Q.value=q);if(g!==undefined)b.gain.value=g;return b;};
 const mix=c.createGain();
 const src=c.createBufferSource(); src.buffer=bufs[current]; src.loop=true;
 const hp=c.createBiquadFilter();hp.type='highpass';hp.frequency.value=45;
 const warm=c.createBiquadFilter();warm.type='peaking';warm.frequency.value=260;warm.Q.value=1.4;warm.gain.value=+el('warm').value;
 const pres=c.createBiquadFilter();pres.type='peaking';pres.frequency.value=3000;pres.Q.value=1.2;pres.gain.value=+el('pres').value;
 const air=c.createBiquadFilter();air.type='peaking';air.frequency.value=9000;air.Q.value=1.0;air.gain.value=+el('air').value;
 const vg=c.createGain(); vg.gain.value=muteV?0:Math.pow(10,+el('vocal').value/20);
 const conv=c.createConvolver(); conv.buffer=bufs.ir;
 const dry=c.createGain(); dry.gain.value=1;
 const wet=c.createGain(); wet.gain.value=(+el('wet').value)*0.9;
 src.connect(hp).connect(warm).connect(pres).connect(air).connect(vg);
 vg.connect(dry).connect(mix); vg.connect(conv).connect(wet).connect(mix);
 const isrc=c.createBufferSource(); isrc.buffer=bufs.inst; isrc.loop=true;
 const ig=c.createGain(); ig.gain.value=muteI?0:Math.pow(10,-(+el('bed').value)/20);
 const duck=c.createBiquadFilter();duck.type='peaking';duck.frequency.value=2800;duck.Q.value=1.2;duck.gain.value=-1;
 isrc.connect(ig).connect(duck).connect(mix);
 const comp=c.createDynamicsCompressor(); comp.threshold.value=-15; comp.ratio.value=+el('comp').value; comp.attack.value=0.02; comp.release.value=0.25;
 const lim=c.createDynamicsCompressor(); lim.threshold.value=-2; lim.ratio.value=20; lim.attack.value=0.003; lim.release.value=0.05;
 const master=c.createGain(); master.gain.value=Math.pow(10,+el('master').value/20);
 mix.connect(comp).connect(lim).connect(master).connect(c.destination);
 nodes={src,isrc,vg,ig,comp,master,warm,pres,air,wet,dry,sv:1,si:1};
 const t=c.currentTime+0.05; src.start(t,off); isrc.start(t,off); startCtx=t; offset=off;
 raf();
}
function raf(){if(!playing||!nodes)return;const t=((ctx.currentTime-startCtx)+offset)%Math.max(dur(),0.01);el('fill').style.width=(t/dur()*100)+'%';el('time').textContent=tfmt(t)+' / '+tfmt(dur());requestAnimationFrame(raf);}
function apply(){if(!nodes)return;const t=ctx.currentTime;
 nodes.vg.gain.setTargetAtTime(muteV?0:Math.pow(10,+el('vocal').value/20),t,0.02);
 nodes.ig.gain.setTargetAtTime(muteI?0:Math.pow(10,-(+el('bed').value)/20),t,0.02);
 nodes.warm.gain.setTargetAtTime(+el('warm').value,t,0.02);
 nodes.pres.gain.setTargetAtTime(+el('pres').value,t,0.02);
 nodes.air.gain.setTargetAtTime(+el('air').value,t,0.02);
 nodes.comp.ratio.setTargetAtTime(+el('comp').value,t,0.05);
 nodes.master.gain.setTargetAtTime(Math.pow(10,+el('master').value/20),t,0.05);
 nodes.wet.gain.setTargetAtTime(+el('wet').value*0.9,t,0.05);}
async function toggle(){await init(); if(playing){stop();return;} build(offset); playing=true; el('play').textContent='⏸ 暂停'; el('st').textContent='播放中：人声 '+current+'，循环';}
function stop(){if(nodes){try{nodes.src.stop();nodes.isrc.stop();}catch(e){} nodes=null;} playing=false; el('play').textContent='▶ 播放';}
function pick(which){current=which; el('bA').className=which==='A'?'on':'ghost'; el('bB').className=which==='B'?'on':'ghost'; if(playing){stop();toggle();}}
let muteV=false,muteI=false;
function solo(kind){
 if(kind==='v'){ if(muteV){muteV=false;muteI=false;} else {muteV=false;muteI=true;} }
 else { if(muteI){muteI=false;muteV=false;} else {muteI=false;muteV=true;} }
 document.getElementById('bSv').className=muteI?'on':'ghost';
 document.getElementById('bSi').className=muteV?'on':'ghost';
 apply();
}
function seek(e){const r=el('bar').getBoundingClientRect();const p=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));const off=p*dur();if(playing){stop();build(off);playing=true;el('play').textContent='⏸ 暂停';}else{offset=off;el('fill').style.width=(p*100)+'%';el('time').textContent=tfmt(off)+' / '+tfmt(dur());}}
pick('B');
</script>"""

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send_file(self, path, ctype):
        try:
            data = open(path,"rb").read()
        except Exception:
            self.send_response(404); self.end_headers(); self.wfile.write(b"not found"); return
        self.send_response(200); self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            d=HTML.encode("utf-8"); self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(d)))
            self.end_headers(); self.wfile.write(d); return
        m = {"/audio/vA":ARGS.vA,"/audio/vB":ARGS.vB,"/audio/inst":ARGS.inst,"/audio/ir":ARGS.ir}
        if u.path in m:
            self.send_file(m[u.path], mimetypes.guess_type(m[u.path])[0] or "audio/wav"); return
        self.send_response(404); self.end_headers()

def main():
    global ARGS
    ap=argparse.ArgumentParser()
    ap.add_argument("--vA",required=True); ap.add_argument("--vB",required=True)
    ap.add_argument("--inst",required=True)
    ap.add_argument("--ir",default=r"D:/VoiceData/house/plugins/dsh-cover-factory/engine/assets/room_ir.wav")
    ap.add_argument("--port",type=int,default=8898)
    ARGS=ap.parse_args()
    srv=ThreadingHTTPServer(("127.0.0.1",ARGS.port),H)
    print("live studio: http://127.0.0.1:%d"%ARGS.port,flush=True)
    srv.serve_forever()

if __name__=="__main__": main()
