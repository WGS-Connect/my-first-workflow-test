"""Template-based audiobook thumbnail compositor."""
from __future__ import annotations
import random, re
from pathlib import Path
import yaml
from PIL import Image, ImageDraw, ImageFont
from src.errors import ConfigError

IMAGE_EXTS={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff','.avif','.svg','.pdf'}
FONT_PATHS=("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf","/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf","/System/Library/Fonts/Supplemental/Arial Bold.ttf","C:\\Windows\\Fonts\\arialbd.ttf")

def font(size):
    for p in FONT_PATHS:
        if Path(p).is_file(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def _open_image(path: Path):
    ext=path.suffix.lower()
    if ext=='.svg':
        try:
            import cairosvg
        except ImportError as exc: raise ConfigError('SVG templates/covers require cairosvg') from exc
        from io import BytesIO
        return Image.open(BytesIO(cairosvg.svg2png(url=str(path)))).convert('RGBA')
    if ext=='.pdf':
        try:
            import fitz
        except ImportError as exc: raise ConfigError('PDF templates/covers require PyMuPDF') from exc
        doc=fitz.open(path); page=doc.load_page(0); pix=page.get_pixmap(alpha=True); return Image.frombytes('RGBA',[pix.width,pix.height],pix.samples)
    return Image.open(path).convert('RGBA')

def list_templates(category_dir: Path):
    return [p for p in sorted(category_dir.iterdir()) if p.is_file() and p.suffix.lower() in IMAGE_EXTS]

def choose_template(root: Path, category: str):
    cat=root/category
    if not cat.is_dir(): cat=root/'general'
    choices=list_templates(cat) if cat.is_dir() else []
    if not choices:
        choices=list_templates(root/'general') if (root/'general').is_dir() else []
    if not choices: raise ConfigError(f'No thumbnail templates found under {root}')
    return random.choice(choices)

def _box(v): return tuple(int(x) for x in v)

def _fit_layer(image, box, canvas):
    x1,y1,x2,y2=_box(box); bw,bh=max(1,x2-x1),max(1,y2-y1)
    im=image.copy().convert('RGBA'); im.thumbnail((bw,bh),Image.Resampling.LANCZOS)
    layer=Image.new('RGBA',canvas,(0,0,0,0)); layer.alpha_composite(im,(x1+(bw-im.width)//2,y1+(bh-im.height)//2)); return layer

def _wrap(draw,text,fnt,max_width):
    lines=[]; cur=''
    for word in text.split():
        t=(cur+' '+word).strip()
        if not cur or draw.textlength(t,font=fnt)<=max_width: cur=t
        else: lines.append(cur); cur=word
    if cur: lines.append(cur)
    return '\n'.join(lines)

def compose(template, cover, person, hook, out, layout, category='general'):
    template,cover,person,out=map(Path,(template,cover,person,out))
    if not template.is_file(): raise ConfigError(f'Thumbnail template missing: {template}')
    if not cover.is_file(): raise ConfigError(f'Thumbnail cover missing: {cover}')
    canvas=_open_image(template); size=canvas.size
    cfg=dict(layout.get('default',{})); cfg.update(layout.get(category,{}) or {})
    base=layout.get('base_size',[1280,720]); sx=size[0]/base[0]; sy=size[1]/base[1]
    def sb(box): return [round(box[0]*sx),round(box[1]*sy),round(box[2]*sx),round(box[3]*sy)]
    with _open_image(person) as p: canvas.alpha_composite(_fit_layer(p,sb(cfg.get('person_box',[0,0,0,0])),size))
    with _open_image(cover) as c: canvas.alpha_composite(_fit_layer(c,sb(cfg['book_box']),size))
    draw=ImageDraw.Draw(canvas)
    box=_box(sb(cfg['text_box'])); maxw=box[2]-box[0]; maxh=box[3]-box[1]
    text=re.sub(r'\s+',' ',hook or 'THE BIG IDEA').strip().upper()
    size_px=int(cfg.get('font_size',82)*sx); fill=cfg.get('fill','#FFFFFF'); stroke_fill=cfg.get('stroke_fill','#000000'); stroke=int(cfg.get('stroke_width',3))
    while size_px>=28:
        f=font(size_px); wrapped=_wrap(draw,text,f,maxw); bb=draw.multiline_textbbox((0,0),wrapped,font=f,spacing=int(cfg.get('line_spacing',6)),stroke_width=stroke)
        if bb[2]-bb[0]<=maxw and bb[3]-bb[1]<=maxh: break
        size_px-=2
    draw.multiline_text((box[0],box[1]),wrapped,font=font(size_px),fill=fill,spacing=int(cfg.get('line_spacing',6)),stroke_width=stroke,stroke_fill=stroke_fill)
    out.parent.mkdir(parents=True,exist_ok=True)
    canvas.convert('RGB').save(out,'JPEG',quality=95,optimize=True)
    return out

def load_layout(path):
    raw=yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    if 'default' not in raw: raise ConfigError('thumbnail_layout.yaml must contain default')
    return raw
