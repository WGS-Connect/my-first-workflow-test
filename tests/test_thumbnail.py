from pathlib import Path
from PIL import Image
from src.thumbnail.audiobook import compose

def test_thumbnail_composition(tmp_path):
    template=tmp_path/'template.webp'; cover=tmp_path/'cover.tiff'; person=tmp_path/'person.png'; out=tmp_path/'out.jpg'
    Image.new('RGB',(1280,720),'gray').save(template)
    Image.new('RGB',(400,600),'white').save(cover)
    Image.new('RGBA',(300,600),(0,0,0,0)).save(person)
    layout={'default':{'book_box':[40,80,430,650],'person_box':[0,70,390,720],'text_box':[470,55,1240,665],'font_size':82,'line_spacing':6,'stroke_width':3,'fill':'#fff','stroke_fill':'#000'}}
    compose(template,cover,person,'CONTROL YOURSELF FIRST',out,layout)
    assert out.exists() and out.stat().st_size>1000
