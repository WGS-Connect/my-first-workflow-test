"""Production Kokoro TTS provider using the one-time voice-lab settings."""
from __future__ import annotations
import os, subprocess, tempfile
from pathlib import Path
from src.narration.kokoro_tts import KokoroTTS
from src.narration.voice_profiles import get_profile
ROOT=Path(__file__).resolve().parents[2]
SETTINGS=ROOT/'config/voice_settings.txt'

def read_voice_settings(path=SETTINGS):
    data={'MODEL':'Kokoro-82M','VOICE_A':'am_adam','VOICE_B':'af_heart','BLEND':'0','SPEED':'0.94','PITCH':'0'}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k,v=line.split('=',1); data[k.strip().upper()]=v.strip()
    return {'model':data['MODEL'],'voice_a':data['VOICE_A'],'voice_b':data['VOICE_B'],'blend':max(0,min(100,float(data['BLEND']))),'speed':float(data['SPEED']),'pitch':float(data['PITCH'])}

class TTSProvider:
    def __init__(self, profile=None):
        p=get_profile(profile) if profile else None
        s=read_voice_settings()
        if profile and profile not in ('production','audiobook') and not SETTINGS.is_file():
            s.update({'voice_a':p.voice,'speed':p.speed})
        self.settings=s
        self.primary=KokoroTTS(voice=s['voice_a'],speed=s['speed'])
        self.secondary=KokoroTTS(voice=s['voice_b'],speed=s['speed'])

    def _pitch(self, src: Path, dst: Path):
        pitch=self.settings['pitch']
        if abs(pitch)<0.01:
            if src.resolve()!=dst.resolve(): src.replace(dst)
            return
        factor=2**(pitch/12)
        subprocess.run(['ffmpeg','-y','-v','error','-i',str(src),'-af',f'asetrate=24000*{factor:.8f},aresample=24000,atempo={1/factor:.8f}','-c:a','libmp3lame','-b:a',os.getenv('TTS_MP3_BITRATE','192k'),str(dst)],check=True)

    def synthesize(self,text,out_path,voice=None,speed=None):
        out=Path(out_path); out.parent.mkdir(parents=True,exist_ok=True)
        # Explicit voice overrides are useful for tests; production uses the one-time lab selection.
        if voice:
            self.primary.synthesize(text,str(out),voice=voice,speed=speed or self.settings['speed']); return str(out)
        blend=self.settings['blend']
        if blend<=0:
            with tempfile.TemporaryDirectory(prefix='tts-pitch-') as td:
                raw=Path(td)/'raw.mp3'; self.primary.synthesize(text,str(raw),voice=self.settings['voice_a'],speed=speed or self.settings['speed']); self._pitch(raw,out)
        elif blend>=100:
            with tempfile.TemporaryDirectory(prefix='tts-pitch-') as td:
                raw=Path(td)/'raw.mp3'; self.secondary.synthesize(text,str(raw),voice=self.settings['voice_b'],speed=speed or self.settings['speed']); self._pitch(raw,out)
        else:
            with tempfile.TemporaryDirectory(prefix='tts-blend-') as td:
                td=Path(td); a=td/'a.mp3'; b=td/'b.mp3'; ap=td/'ap.mp3'; bp=td/'bp.mp3'
                self.primary.synthesize(text,str(a),voice=self.settings['voice_a'],speed=speed or self.settings['speed'])
                self.secondary.synthesize(text,str(b),voice=self.settings['voice_b'],speed=speed or self.settings['speed'])
                self._pitch(a,ap); self._pitch(b,bp); x=blend/100
                subprocess.run(['ffmpeg','-y','-v','error','-i',str(ap),'-i',str(bp),'-filter_complex',f'[0:a]volume={1-x}[a];[1:a]volume={x}[b];[a][b]amix=inputs=2:duration=longest:normalize=1[aout]','-map','[aout]','-c:a','libmp3lame','-b:a',os.getenv('TTS_MP3_BITRATE','192k'),str(out)],check=True)
        return str(out)

__all__=['TTSProvider','read_voice_settings']
