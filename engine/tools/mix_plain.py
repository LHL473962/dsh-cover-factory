
# -*- coding: utf-8 -*-
"""最简混音台：两个 <audio>，仅音量滑杆（无 Web Audio），用于确认播放链路。"""
import argparse, urllib.parse, mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ARGS=None
HTML=r"""<!doctype html><meta charset="utf-8"><title>plain</title>
<style>body{background:#0e1220;color:#e8ecf6;font:14px "Microsoft YaHei";padding:20px}
button{background:#3b6cff;color:#fff;border:0;border-radius:8px;padding:8px 14px;margin:4px}
input[type=range]{width:100%}label{display:block;margin:12px 0 2px}
#bar{height:14px;background:#0b1020;border:1px solid #26304a;border-radius:7px;margin:12px 0 4px;position:relative;overflow:hidden}
#fill{height:100%;width:0;background:#3b6cff}#st{color:#9fb0d0;white-space:pre-wrap;margin-top:8px}
</style>
<h1>Plain 播放台</h1>
<button onclick="doPlay()">播放/暂停</button>
<button onclick="solo('v')">只人声</button><button onclick="solo('i')">只伴奏</button>
<button onclick="pick('A')">人声A</button><button onclick="pick('B')">人声B</button>
<div id="bar"><div id="fill"></div></div><div id="st">init</div>
<label>人声音量 <span id="vv">1.0</span></label><input id="v" type="range" min="0" max="1" step="0.01" value="1">
<label>伴奏音量 <span id="vi">1.0</span></label><input id="i" type="range" min="0" max="1" step="0.01" value="1">
<audio id="av" src="/audio/vB" preload="auto" loop></audio>
<audio id="ai" src="/audio/inst" preload="auto" loop></audio>
<script>
const el=id=>document.getElementById(id);let cur='B';
function st(t){el('st').textContent=t;}
function upd(){el('vv').textContent=el('v').value;el('vi').textContent=el('i').value;el('av').volume=el('v').value;el('ai').volume=el('i').value;}
el('v').oninput=upd;el('i').oninput=upd;upd();
function doPlay(){if(el('av').paused){el('av').currentTime=el('ai').currentTime;Promise.all([el('av').play(),el('ai').play()]).then(()=>st('playing')).catch(e=>st('ERR '+e));}else{el('av').pause();el('ai').pause();st('paused');}}
function solo(k){if(k==='v'){el('av').volume=el('v').value;el('ai').volume=0;}else{el('ai').volume=el('i').value;el('av').volume=0;}}
function pick(w){cur=w;el('av').src=(w==='A')?'/audio/vA':'/audio/vB';el('av').load();if(!el('av').paused)el('av').play();}
setInterval(()=>{const a=el('av'),b=el('ai');el('fill').style.width=(b.currentTime/(b.duration||1)*100)+'%';st('av.ready='+a.readyState+' ai.ready='+b.readyState+' t='+b.currentTime.toFixed(1)+' dur='+(b.duration||0).toFixed(1)+' err='+((a.error&&a.error.code)||0)+'/'+((b.error&&b.error.code)||0));},500);
</script>"""
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        u=urllib.parse.urlparse(self.path)
        if u.path=='/':
            d=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(d)));self.end_headers();self.wfile.write(d);return
        m={'/audio/vA':ARGS.vA,'/audio/vB':ARGS.vB,'/audio/inst':ARGS.inst}
        if u.path in m:
            try:data=open(m[u.path],'rb').read()
            except Exception:
                self.send_response(404);self.end_headers();return
            self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(m[u.path])[0] or 'audio/wav');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        self.send_response(404);self.end_headers()
def main():
    global ARGS
    ap=argparse.ArgumentParser();ap.add_argument('--vA',required=True);ap.add_argument('--vB',required=True);ap.add_argument('--inst',required=True);ap.add_argument('--port',type=int,default=8896)
    ARGS=ap.parse_args()
    print('plain: http://127.0.0.1:%d'%ARGS.port,flush=True)
    ThreadingHTTPServer(('127.0.0.1',ARGS.port),H).serve_forever()
if __name__=='__main__': main()
