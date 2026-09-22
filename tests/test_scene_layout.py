from pathlib import Path
from PIL import Image
from src.rendering.audiobook_scene import make_book_layer, fit_person

def test_fixed_book_and_person_layers(tmp_path):
    cover=tmp_path/'cover.webp'; person=tmp_path/'person.png'
    Image.new('RGB',(500,800),'white').save(cover); Image.new('RGBA',(400,800),(255,0,0,255)).save(person)
    b=make_book_layer(cover,tmp_path/'book.png',(1280,720),[[600,80],[1000,70],[1010,650],[600,660]])
    p=fit_person(person,tmp_path/'person-layer.png',(1280,720),[0,40,500,600])
    assert b.exists() and p.exists()
