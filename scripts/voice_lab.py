#!/usr/bin/env python3
"""One-time interactive Kokoro voice audition lab.

Run from the repository root:
    python scripts/voice_lab.py

It opens a local browser page. Pick Voice A/B, speed, pitch and blend, generate
short previews, then click "Save production voice". That writes
config/voice_settings.txt, which the production pipeline reads automatically.
"""
from __future__ import annotations
import json, os, subprocess, tempfile, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import yaml
ROOT=Path(__file__).resolve().parents[1]; CFG=ROOT/'config/voice_lab.yaml'; OUT=ROOT/'work/voice_lab'; SETTINGS=ROOT/'config/voice_settings.txt'

def cfg(): return yaml.safe_load(CFG.read_text(encoding='utf-8')) or {}

def synth(voice,text,speed,out):
    from src.narration.kokoro_tts import KokoroTTS
    KokoroTTS(voice=voice,speed=float(speed)).synthesize(text,str(out),voice=voice,speed=float(speed))

def postprocess(src,out,pitch=0):
    pitch=float(pitch); out=Path(out)
    if abs(pitch)<0.01:
        Path(src).replace(out); return
    factor=2**(pitch/12)
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(src),'-af',f'asetrate=24000*{factor:.8f},aresample=24000,atempo={1/factor:.8f}', '-c:a','libmp3lame','-b:a','192k',str(out)],check=True)

def blend(a,b,out,blend):
    blend=max(0,min(100,float(blend)))/100
    if blend<=0: Path(a).replace(out); return
    if blend>=1: Path(b).replace(out); return
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(a),'-i',str(b),'-filter_complex',f'[0:a]volume={1-blend}[a];[1:a]volume={blend}[b];[a][b]amix=inputs=2:duration=longest:normalize=1[aout]','-map','[aout]','-c:a','libmp3lame','-b:a','192k',str(out)],check=True)

def make_preview(q):
    c=cfg(); OUT.mkdir(parents=True,exist_ok=True)
    text=(q.get('text') or c.get('sample_text','')).strip(); va=q.get('voice_a','am_adam'); vb=q.get('voice_b','af_heart'); speed=float(q.get('speed',.94)); pitch=float(q.get('pitch',0)); blend_pct=float(q.get('blend',0))
    key=f'{va}_{vb}_{speed:.2f}_{pitch:.1f}_{blend_pct:.1f}'.replace('.','_').replace('-','m'); final=OUT/f'preview_{key}.mp3'
    if final.exists(): return final
    with tempfile.TemporaryDirectory(prefix='voice-lab-',dir=OUT) as td:
        td=Path(td); a=td/'a.mp3'; b=td/'b.mp3'; ap=td/'ap.mp3'; bp=td/'bp.mp3'
        synth(va,text,speed,a); postprocess(a,ap,pitch)
        if blend_pct>0: synth(vb,text,speed,b); postprocess(b,bp,pitch); blend(ap,bp,final,blend_pct)
        else: ap.replace(final)
    return final

HTML='''<!doctype html><html><head><meta charset="utf-8"><title>Kokoro Voice Lab</title><style>body{font-family:Arial;max-width:1000px;margin:30px auto;background:#10141b;color:#eee;padding:20px}select,input,textarea,button{padding:10px;margin:5px;background:#1c2430;color:#fff;border:1px solid #465}label{display:block;margin-top:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.card{background:#151d28;padding:18px;border-radius:12px}button{cursor:pointer}.voices{columns:2}</style></head><body><h1>Kokoro One-Time Voice Lab</h1><p>Audition all voices, speed, pitch and A/B blend. When satisfied, save the production settings.</p><div class="card"><label>Sample text<textarea id="text" rows="4" style="width:95%">__TEXT__</textarea></label><div class="grid"><label>Voice A<select id="a">__VOICES__</select></label><label>Voice B<select id="b">__VOICES__</select></label><label>Speed <input id="speed" type="number" step="0.01" min="0.75" max="1.20" value="0.94"></label><label>Pitch (semitones) <input id="pitch" type="number" step="0.5" min="-6" max="6" value="0"></label><label>Blend A→B (%) <input id="blend" type="number" step="5" min="0" max="100" value="0"></label></div><button onclick="preview()">Generate / Play Preview</button><button onclick="save()">Save Production Voice</button><p id="msg"></p><audio id="player" controls style="width:100%"></audio></div><div class="card"><h2>Voices</h2><div class="voices">__CATALOG__</div></div><script>async function preview(){msg.textContent='Generating...';let q=new URLSearchParams({text:text.value,voice_a:a.value,voice_b:b.value,speed:speed.value,pitch:pitch.value,blend:blend.value});let r=await fetch('/preview?'+q);let j=await r.json();if(!j.ok){msg.textContent=j.error;return}player.src=j.url;player.play();msg.textContent='Preview ready.'}async function save(){let q=new URLSearchParams({voice_a:a.value,voice_b:b.value,speed:speed.value,pitch:pitch.value,blend:blend.value});let r=await fetch('/save?'+q);let j=await r.json();msg.textContent=j.ok?'Saved to config/voice_settings.txt':'Error: '+j.error}</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u=urlparse(self.path); q={k:v[0] for k,v in parse_qs(u.query).items()}
        try:
            if u.path=='/':
                c=cfg(); opts=''.join(f'<option>{v}</option>' for v in c['voices']); cat='<br>'.join(c['voices']); html=HTML.replace('__TEXT__',c.get('sample_text','')).replace('__VOICES__',opts).replace('__CATALOG__',cat); self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(html.encode()); return
            if u.path=='/preview':
                p=make_preview(q); payload={'ok':True,'url':'/audio/'+p.name}; self._json(payload); return
            if u.path=='/save':
                SETTINGS.write_text(f"# One-time production narrator\nMODEL=Kokoro-82M\nVOICE_A={q.get('voice_a','am_adam')}\nVOICE_B={q.get('voice_b','af_heart')}\nBLEND={q.get('blend','0')}\nSPEED={q.get('speed','0.94')}\nPITCH={q.get('pitch','0')}\n",encoding='utf-8'); self._json({'ok':True}); return
            if u.path.startswith('/audio/'):
                p=OUT/Path(u.path).name
                if not p.is_file(): self.send_error(404); return
                self.send_response(200); self.send_header('Content-Type','audio/mpeg'); self.end_headers(); self.wfile.write(p.read_bytes()); return
            self.send_error(404)
        except Exception as e: self._json({'ok':False,'error':str(e)})
    def _json(self,obj):
        raw=json.dumps(obj).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(raw)
    def log_message(self,*args): pass

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',8765),Handler); print('Voice Lab: http://127.0.0.1:8765'); threading.Timer(.5,lambda:webbrowser.open('http://127.0.0.1:8765')).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
