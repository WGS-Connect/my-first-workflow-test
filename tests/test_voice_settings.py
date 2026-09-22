from pathlib import Path
from src.narration.tts import read_voice_settings

def test_voice_settings_file(tmp_path):
    p=tmp_path/'voice_settings.txt'; p.write_text('VOICE_A=am_adam\nVOICE_B=af_heart\nBLEND=35\nSPEED=0.91\nPITCH=-1.5\n',encoding='utf-8')
    s=read_voice_settings(p)
    assert s=={'model':'Kokoro-82M','voice_a':'am_adam','voice_b':'af_heart','blend':35.0,'speed':.91,'pitch':-1.5}
