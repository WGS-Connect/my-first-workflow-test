"""New audiobook visual engine: moving premade backgrounds + fixed person/book."""
from __future__ import annotations
import json, random, subprocess
from pathlib import Path
import yaml
from PIL import Image, ImageDraw, ImageFilter
from src.rendering.ffmpeg import duration_of, media_info, run
from src.errors import ConfigError
from src.rendering.ffmpeg import RenderError
from src.utils.log import get_logger
log=get_logger('audiobook-scene')

IMAGE_EXTS={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff','.avif'}
VIDEO_EXTS={'.mp4','.mov','.m4v','.mkv','.webm','.avi','.ts'}

def _solve_homography(src_pts, dst_pts):
    import numpy as np
    A=[]; b=[]
    for (x,y),(u,v) in zip(dst_pts,src_pts):
        A += [[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]; b += [u,v]
    return np.linalg.solve(np.asarray(A,float),np.asarray(b,float)).tolist()

def make_book_layer(cover, out, canvas_size, quad, side_depth=22, shadow=True):
    cover=Path(cover); out=Path(out); out.parent.mkdir(parents=True,exist_ok=True)
    with Image.open(cover) as im:
        src=im.convert('RGBA')
    w,h=canvas_size
    # Map full source rectangle to the configured destination quadrilateral.
    src_pts=[(0,0),(src.width,0),(src.width,src.height),(0,src.height)]
    coeff=_solve_homography(src_pts,quad)
    warped=src.transform((w,h),Image.Transform.PERSPECTIVE,coeff,Image.Resampling.BICUBIC)
    layer=Image.new('RGBA',(w,h),(0,0,0,0))
    if shadow:
        mask=Image.new('L',(w,h),0); md=ImageDraw.Draw(mask)
        sh=[(x+10,y+12) for x,y in quad]; md.polygon(sh,fill=150); mask=mask.filter(ImageFilter.GaussianBlur(18))
        shadow_im=Image.new('RGBA',(w,h),(0,0,0,0)); shadow_im.putalpha(mask); layer.alpha_composite(shadow_im)
    # subtle right-side book thickness, based on the right edge of the face.
    if side_depth:
        q=quad; dx=side_depth
        side=[q[1],(q[1][0]+dx,q[1][1]+5),(q[2][0]+dx,q[2][1]-8),q[2]]
        d=ImageDraw.Draw(layer); d.polygon(side,fill=(28,24,20,245))
    layer.alpha_composite(warped)
    layer.save(out,'PNG',optimize=True)
    return out

def fit_person(person, out, canvas_size, box):
    person=Path(person); out=Path(out); out.parent.mkdir(parents=True,exist_ok=True)
    with Image.open(person) as im: im=im.convert('RGBA')
    x1,y1,x2,y2=map(int,box); bw,bh=max(1,x2-x1),max(1,y2-y1)
    im.thumbnail((bw,bh),Image.Resampling.LANCZOS)
    layer=Image.new('RGBA',canvas_size,(0,0,0,0))
    layer.alpha_composite(im,(x1+(bw-im.width)//2,y2-im.height))
    layer.save(out,'PNG',optimize=True); return out

def load_layout(path: Path) -> dict:
    if not path.is_file(): raise ConfigError(f"Scene layout missing: {path}")
    raw=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return raw

def _video_signature(p):
    info=media_info(p); streams=info.get('streams',[]); v=next((s for s in streams if s.get('codec_type')=='video'),None)
    return {'codec':v.get('codec_name') if v else None,'width':v.get('width') if v else None,'height':v.get('height') if v else None,'fps':v.get('r_frame_rate') if v else None,'pix_fmt':v.get('pix_fmt') if v else None}

def compatible(a,b): return a==b

def make_background_track(backgrounds, total, out, width, height, fps, crf, preset, order='rotate_all'):
    backgrounds=[Path(x) for x in backgrounds if Path(x).is_file()]
    if not backgrounds: raise RenderError('No premade audiobook background videos found')
    if order=='random': random.shuffle(backgrounds)
    # The first cycle always contains every supplied background. We divide the
    # requested duration across the cycle so a very long first clip cannot hide
    # the other clips. Later cycles repeat in the same order.
    cycle=list(backgrounds); seq=[]; remaining=total; cycle_no=0
    while remaining > 0.001:
        current=cycle[:]
        if cycle_no>0 and order=='random': random.shuffle(current)
        slot=remaining/len(current)
        for b in current:
            take=min(slot,remaining)
            if take<=0: break
            seq.append((b,take)); remaining-=take
        cycle_no+=1
    parts=[]; manifest=Path(out).with_suffix('.background.parts.txt')
    # Stream-copy is retained when every source has matching video parameters.
    sig=_video_signature(seq[0][0]); compatible_all=all(compatible(sig,_video_signature(x)) for x,_ in seq)
    if compatible_all:
        for i,(b,take) in enumerate(seq):
            part=Path(out).with_name(f'{Path(out).stem}.bgpart{i:03d}.mp4')
            run(['ffmpeg','-y','-v','error','-stream_loop','-1','-i',str(b),'-t',f'{take:.3f}','-map','0:v:0','-an','-c','copy',str(part)],f'background part {i}')
            parts.append(part)
        manifest.write_text('\n'.join("file '"+str(x.resolve()).replace("'","'\\''")+"'" for x in parts)+'\n',encoding='utf-8')
        run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(manifest),'-c','copy',str(out)],'background concat')
    else:
        filters=[]; inputs=[]
        for i,(b,take) in enumerate(seq):
            inputs += ['-stream_loop','-1','-i',str(b)]
            filters.append(f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},trim=duration={take:.3f},setpts=PTS-STARTPTS[v{i}]')
        labels=''.join(f'[v{i}]' for i in range(len(seq)))
        filters.append(f'{labels}concat=n={len(seq)}:v=1:a=0[v]')
        run(['ffmpeg','-y','-v','error',*inputs,'-filter_complex',';'.join(filters),'-map','[v]','-t',f'{total:.3f}','-c:v','libx264','-preset',preset,'-crf',str(crf),'-r',str(fps),'-pix_fmt','yuv420p',str(out)],'background normalize/concat')
    for x in parts: x.unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)
    return out

def make_music_track(music_tracks, total, out):
    tracks=[Path(x) for x in music_tracks if Path(x).is_file()]
    if not tracks: return None
    seq=[]; covered=0.0; i=0
    while covered < total + 1:
        x=tracks[i % len(tracks)]; seq.append(x); d=duration_of(x); covered += max(d,0.1); i += 1
    manifest=Path(out).with_suffix('.music.txt')
    manifest.write_text('\n'.join("file '"+str(x.resolve()).replace("'","'\\''")+"'" for x in seq)+'\n',encoding='utf-8')
    concat=Path(out).with_name(Path(out).stem+'.concat.m4a')
    # Normalize only the audio stream; no video is involved.
    run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(manifest),'-vn','-c:a','aac','-b:a','192k',str(concat)],'music concat')
    manifest.unlink(missing_ok=True)
    run(['ffmpeg','-y','-v','error','-i',str(concat),'-t',f'{total:.3f}','-c','copy',str(out)],'music trim')
    concat.unlink(missing_ok=True)
    return Path(out)

def render_scene(audio, cover, person, backgrounds, music_tracks, out, layout_path, width=1920,height=1080,fps=30,crf=20,preset='veryfast',music_volume=.06,background_order='rotate_all',work_dir=None):
    work=Path(work_dir) if work_dir else Path(out).parent/'render'; work.mkdir(parents=True,exist_ok=True)
    layout=load_layout(Path(layout_path)); total=duration_of(audio)
    person_box=layout.get('person_box',[0,80,680,820]); book_quad=layout.get('book_quad',[[760,150],[1180,120],[1180,930],[760,960]])
    book_layer=make_book_layer(cover,work/'book_layer.png',(width,height),book_quad,int(layout.get('book_side_depth',22)),bool(layout.get('book_shadow',True)))
    person_layer=fit_person(person,work/'person_layer.png',(width,height),person_box)
    bg=make_background_track(backgrounds,total,work/'background.mp4',width,height,fps,crf,preset,background_order)
    scene=work/'scene.mp4'
    # Background contains the bench. Person is composited first, then book, so the book sits on the bench.
    graph='[0:v][1:v]overlay=0:0[p];[p][2:v]overlay=0:0[v]'
    run(['ffmpeg','-y','-v','error','-i',str(bg),'-i',str(person_layer),'-i',str(book_layer),'-filter_complex',graph,'-map','[v]','-t',f'{total:.3f}','-an','-c:v','libx264','-preset',preset,'-crf',str(crf),'-r',str(fps),'-pix_fmt','yuv420p',str(scene)],'scene composite')
    # Select/rotate one or more music beds, with a low-volume sidechain duck under narration.
    music=make_music_track(music_tracks,total,work/'music.m4a') if music_tracks else None
    final=Path(out); final.parent.mkdir(parents=True,exist_ok=True)
    from src.rendering.ffmpeg import mux
    mux(scene,audio,final,str(music) if music else None,music_volume,crf,preset,duration=total)
    log.info('new audiobook scene rendered',seconds=round(duration_of(final),2),backgrounds=len(backgrounds))
    return final
