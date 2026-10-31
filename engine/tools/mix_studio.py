
# -*- coding: utf-8 -*-
"""混音试听台：本地网页滑杆调参，实时渲染 mp3，用于穷举对比。

用法：
  python engine/tools/mix_studio.py --vocal A.wav --inst instrumental.wav [--port 8899]
浏览器打开 http://127.0.0.1:8899
"""
import argparse, os, subprocess, tempfile, urllib.parse, json, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FF = os.environ.get("CF_FFMPEG") or r"D:/VoiceData/tools/bin/ffmpeg.exe"
ARGS = None
CACHE = {}

HTML = """<!doctype html><meta charset="utf-8"><title>混音试听台</title>
<style>
body{background:#0e1220;color:#e8ecf6;font:14px/1.6 "Microsoft YaHei",sans-serif;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#8a96b4;margin-bottom:18px}
.row{display:flex;flex-wrap:wrap;gap:18px}
.card{background:#161c2e;border:1px solid #26304a;border-radius:12px;padding:16px;flex:1;min-width:320px}
label{display:block;margin:10px 0 2px;color:#b8c4dc}
input[type=range]{width:100%}
.val{color:#6ea8ff;font-weight:700}
button{background:#3b6cff;color:#fff;border:0;border-radius:8px;padding:9px 14px;cursor:pointer;margin:8px 6px 0 0}
button.ghost{background:#252f47}
audio{width:100%;margin-top:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:10px}
.gitem{background:#101627;border:1px solid #223050;border-radius:8px;padding:8px}
.gitem b{color:#9fc0ff}
code{color:#ffd479}
</style>
<h1>混音试听台</h1><div class="sub">调参 → 点“生成并播放”，约 2–4 秒。参数可保存成 JSON 贴回引擎。</div>
<div class="row">
 <div class="card">
  <label>人声增益 <span class="val" id="out_vocal">0.0 dB</span></label><input type="range" id="vocal" min="-8" max="6" step="0.5" value="0">
  <label>伴奏衰减 <span class="val" id="out_bed">0.0 dB</span></label><input type="range" id="bed" min="-6" max="2" step="0.5" value="0">
  <label>混响湿 <span class="val" id="out_wet">0.12</span></label><input type="range" id="wet" min="0" max="0.4" step="0.02" value="0.12">
  <label>人声加宽 <span class="val" id="out_widen">1.0</span></label><input type="range" id="widen" min="1" max="2" step="0.05" value="1.0">
  <label>人声 3k 存在感 <span class="val" id="out_pres">2.0 dB</span></label><input type="range" id="pres" min="0" max="6" step="0.5" value="2">
  <label>人声 9k 空气感 <span class="val" id="out_air">1.2 dB</span></label><input type="range" id="air" min="0" max="4" step="0.5" value="1.2">
  <label>总线压缩比 <span class="val" id="out_comp">1.3</span></label><input type="range" id="comp" min="1" max="3" step="0.1" value="1.3">
  <label>目标响度 <span class="val" id="out_lufs">-14 LUFS</span></label><input type="range" id="lufs" min="-18" max="-10" step="0.5" value="-14">
  <button onclick="play()">生成并播放</button>
  <button class="ghost" onclick="grid()">生成 9 宫格</button>
  <button class="ghost" onclick="copyCfg()">复制当前参数</button>
 </div>
 <div class="card">
  <audio id="au" controls></audio>
  <div id="info" style="color:#8a96b4;margin-top:8px"></div>
  <div class="grid" id="grid"></div>
 </div>
</div>
<script>
const ids=['vocal','bed','wet','widen','pres','air','comp','lufs'];
const fmt={vocal:v=>v.toFixed(1)+' dB',bed:v=>v.toFixed(1)+' dB',wet:v=>v.toFixed(2),widen:v=>v.toFixed(2),pres:v=>v.toFixed(1)+' dB',air:v=>v.toFixed(1)+' dB',comp:v=>v.toFixed(1),lufs:v=>v+' LUFS'};
function read(){const o={};for(const i of ids){const el=document.getElementById(i);o[i]=parseFloat(el.value);document.getElementById('out_'+i).textContent=fmt[i](o[i]);}return o;}
function q(o){return ids.map(k=>k+'='+o[k]).join('&');}
function play(){const o=read();const au=document.getElementById('au');au.src='/render?'+q(o)+'&t='+Date.now();au.play();document.getElementById('info').textContent='渲染中…';fetch('/info?'+q(o)).then(r=>r.text()).then(t=>document.getElementById('info').textContent=t);}
function copyCfg(){const o=read();navigator.clipboard.writeText(JSON.stringify(o));document.getElementById('info').textContent='参数已复制';}
function grid(){const box=document.getElementById('grid');box.innerHTML='';const base=read();
 const presets=[[-1,0],[0,-2],[1,-3]];const wets=[0,0.12];
 for(const [vg,bed] of presets){for(const wet of wets){const o=Object.assign({},base,{vocal:vg,bed:bed,wet:wet});
  const d=document.createElement('div');d.className='gitem';
  d.innerHTML='<b>人声 '+vg+' / 伴奏 '+bed+' / 湿 '+wet+'</b><audio controls src="/render?'+q(o)+'&t='+Date.now()+'"></audio>';
  box.appendChild(d);}}
}
ids.forEach(i=>document.getElementById(i).addEventListener('input',read));read();
</script>"""

def render(params):
    vocal = float(params.get("vocal", 0))
    bed = float(params.get("bed", 0))
    wet = float(params.get("wet", 0.12))
    widen = float(params.get("widen", 1.0))
    pres = float(params.get("pres", 2))
    air = float(params.get("air", 1.2))
    comp = float(params.get("comp", 1.3))
    lufs = float(params.get("lufs", -14))
    key = json.dumps([vocal, bed, wet, widen, pres, air, comp, lufs])
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    out = os.path.join(tempfile.gettempdir(), "mixstudio_" + h + ".mp3")
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        return out
    ir = ARGS.ir
    vchain = ("[0:a]aresample=44100,highpass=f=45,"
              "equalizer=f=260:width_type=q:width=1.4:g=-1.2,"
              "equalizer=f=3000:width_type=q:width=1.2:g=%s,"
              "equalizer=f=9000:width_type=q:width=1.0:g=%s,volume=%sdB" % (pres, air, vocal))
    if wet > 0.001:
        vchain += (",asplit=2[v1][vs];[vs][2:a]afir=dry=0:wet=1[w0];"
                   "[w0]extrastereo=m=%s,volume=%s[w];[v1][w]amix=inputs=2:normalize=0" % (widen, wet))
    else:
        vchain += ",extrastereo=m=%s" % widen
    vchain += "[v]"
    ichain = ("[1:a]aresample=44100,volume=%sdB,"
              "equalizer=f=2800:width_type=q:width=1.2:g=-1.0,extrastereo=m=1.15[i]" % bed)
    graph = (vchain + ";" + ichain +
             ";[v][i]amix=inputs=2:duration=longest:normalize=0,"
             "acompressor=threshold=-15dB:ratio=%s:attack=20:release=250,"
             "aformat=channel_layouts=stereo[out]" % comp)
    tmp = out + ".tmp.wav"
    cmd = [FF, "-y", "-hide_banner", "-loglevel", "error",
           "-i", ARGS.vocal, "-i", ARGS.inst, "-i", ir,
           "-filter_complex", graph, "-map", "[out]", "-ac", "2", tmp]
    subprocess.run(cmd, check=True)
    probe = subprocess.run([FF, "-hide_banner", "-nostats", "-i", tmp, "-af", "ebur128", "-f", "null", "-"],
                           capture_output=True, text=True, errors="ignore")
    cur = None
    for line in probe.stderr.splitlines():
        if "I:" in line and "LUFS" in line:
            cur = float(line.split("I:")[1].split("LUFS")[0].strip())
    gain = (lufs - (cur if cur is not None else -14.0))
    subprocess.run([FF, "-y", "-hide_banner", "-loglevel", "error", "-i", tmp,
                    "-af", "volume=%.2fdB,alimiter=limit=-2dB:level=false" % gain,
                    "-codec:a", "libmp3lame", "-b:a", "320k", out], check=True)
    os.remove(tmp)
    return out

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/":
            data = HTML.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if u.path == "/info":
            try:
                out = render(q)
                txt = "已生成：%s（%d KB）参数：%s" % (os.path.basename(out), os.path.getsize(out)//1024, json.dumps(q, ensure_ascii=False))
            except Exception as e:
                txt = "失败：" + str(e)[:200]
            data = txt.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        if u.path == "/render":
            try:
                out = render(q)
                data = open(out, "rb").read()
                self.send_response(200); self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as e:
                self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode()[:500])
            return
        self.send_response(404); self.end_headers()

def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocal", required=True)
    ap.add_argument("--inst", required=True)
    ap.add_argument("--ir", default=r"D:/VoiceData/house/plugins/dsh-cover-factory/engine/assets/room_ir.wav")
    ap.add_argument("--port", type=int, default=8899)
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), H)
    print("mix studio: http://127.0.0.1:%d" % ARGS.port, flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
