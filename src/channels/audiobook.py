"""Production audiobook channel.

One topic folder = one YouTube video. The old 50-image AI visual workflow is
intentionally gone. Visuals are assembled from reusable local assets: a moving
background video, a fixed transparent person, and a fixed-perspective book cover.
The existing YouTube client/publish path is retained unchanged.
"""
from __future__ import annotations
import json, random, shutil
from pathlib import Path
from src.channels import topic_queue
from src.channels.common import Runtime, attach_budget, build_runtime, restore_job, target_words
from src.domain.models import Production, RightsResult
from src.errors import ConfigError, InvalidResponseError, RightsBlocked
from src.metadata.generator import generate as generate_metadata
from src.qa.check import validate_video
from src.rendering.audiobook_scene import render_scene
from src.rendering.ffmpeg import concat_audio, duration_of
from src.research.engine import build_research, validate_rights
from src.state.checkpoint import Checkpoint
from src.thumbnail.audiobook import choose_template, compose, load_layout
from src.audiobook_assets import IMAGE_EXTS, VIDEO_EXTS, AUDIO_EXTS, find_cover, read_spec, extract_hashtags
from src.utils.io import read_json, write_json
from src.utils.log import bind, get_logger
from src.youtube.client import YouTube, publish

log=get_logger('audiobook')
LARGE_ARTIFACTS=("audio/*.mp3","narration.mp3","final.mp4","render/*.mp4","render/*.m4a")

def _files(folder, exts):
    p=Path(folder)
    return [x for x in sorted(p.iterdir()) if x.is_file() and x.suffix.lower() in exts] if p.is_dir() else []

def enforce_rights(rights: RightsResult, allow_unknown: bool) -> None:
    if rights.rights_status=='safe': return
    if rights.rights_status=='reject': raise RightsBlocked(f"Rights gate rejected this topic: {rights.notes or 'no reason given'}")
    if allow_unknown: return
    raise RightsBlocked(f"Rights verdict is 'unknown': {rights.notes or 'insufficient evidence'}. Set AUDIOBOOK_ALLOW_UNKNOWN_RIGHTS=true to accept unknown verdicts.")

def _read_text(path): return Path(path).read_text(encoding='utf-8').strip() if Path(path).is_file() else ''

def _classify_and_extract(rt, title, book_dir, explicit_type=''):
    if explicit_type in ('book','topic'): kind=explicit_type
    else:
        raw=rt.llm.json(f'''Classify this audiobook production input. Return ONLY JSON {{"type":"book|topic","book_name":"","reason":""}}.\nTitle: {title}\nBook directory: {book_dir.name}\nUse type=book when the title is about a named existing book and the video should be an original summary/analysis. Use type=topic for a genuinely new/original subject. If type=book, extract the actual book name from the title.''',max_tokens=800,temperature=.05)
        kind=str(raw.get('type','topic')).lower() if isinstance(raw,dict) else 'topic'; kind=kind if kind in ('book','topic') else 'topic'
    raw=rt.llm.json(f'''Extract the underlying book name for this YouTube title. Return ONLY JSON {{"book_name":"","topic":""}}.\nTitle: {title}\nBook directory: {book_dir.name}\nIf this is an original topic and no named book is present, book_name may be empty. Never invent a book.''',max_tokens=500,temperature=.05)
    book_name=str(raw.get('book_name','')).strip() if isinstance(raw,dict) else ''
    topic=str(raw.get('topic','')).strip() if isinstance(raw,dict) else title
    return kind, book_name, topic or title

def _make_outline(rt, title, book_name, mode, research, target_words, test=False):
    n=4 if test else max(8,min(12,round(target_words/700)))
    instruction='Create an original long-form summary/analysis of the named book in your own words; do not reproduce its expression.' if mode=='summary' else 'Create an original long-form educational audiobook script about the topic, with a coherent argument and practical value.'
    return rt.llm.json(f'''{instruction}\nYouTube title: {title}\nUnderlying book: {book_name or 'none'}\nTarget total words: {target_words}\nResearch: {json.dumps(research,ensure_ascii=False)[:45000]}\nCreate exactly {n} connected chapters. The opening must be a strong hook based on pain, stakes, curiosity or a powerful question. Later chapters should escalate ideas, use examples, counterintuitive points and practical frameworks, and the final chapter should resolve the opening promise. Use natural spoken narration. Return ONLY a JSON array of {{"number":1,"title":"","summary":"","target_words":...}}.''',max_tokens=6000,temperature=.3)

def produce(root='work',test=False,runtime:Runtime|None=None)->str:
    rt=runtime or build_runtime('audiobook',root,test); cfg=rt.settings.audiobook; books_root=Path(cfg.books_root)
    if not books_root.is_dir(): raise ConfigError(f'Audiobook books directory not found: {books_root}')
    if rt.drive:
        for b in sorted(p for p in books_root.iterdir() if p.is_dir()):
            try: topic_queue.sync_queue(b,rt.drive)
            except Exception as exc: log.warning('queue sync failed',book=b.name,error=str(exc)[:200])
    book=topic_queue.pick_book(books_root)
    if not book: raise ConfigError(f'No pending audiobook topic folder found under {books_root}')
    topic=topic_queue.claim_book_topic(book,cfg.max_attempts,cfg.stale_hours*3600)
    if not topic: raise ConfigError(f'No claimable topic in {book}')
    topic_dir=topic_queue.topic_path(book,topic); job_id=topic_queue.job_id(book,topic); job=rt.jobs_dir/job_id; bind(job=job_id)
    if rt.drive: topic_queue.sync_queue(book,rt.drive)
    if not (job/'state.json').is_file(): restore_job(rt,job_id,job)
    attach_budget(rt,job); cp=Checkpoint(job,job_id,'audiobook',rt.drive)
    if rt.drive: rt.drive.set_active_job(job_id,channel='audiobook',topic=topic,book=book.name)
    try:
        cover=find_cover(topic_dir); spec=read_spec(topic_dir)
        title=spec['title'] or topic_dir.name
        # ---- source --------------------------------------------------------
        if not cp.is_done('source'):
            cp.begin('source'); kind,book_name,subject=_classify_and_extract(rt,title,book,spec['type'])
            rights=validate_rights(rt.llm,rt.searcher,f'{book_name + " - " if book_name else ""}{subject}')
            enforce_rights(rights,cfg.allow_unknown_rights)
            production=Production(channel='audiobook',job_id=job_id,topic=title,book=book_name or book.name,mode='summary' if kind=='book' else 'original',rights=rights)
            write_json(job/'production.json',production.model_dump()); write_json(job/'source_spec.json',{'type':kind,'book_name':book_name,'subject':subject,'title':title,'topic_dir':str(topic_dir),'cover':cover.name}); shutil.copy2(cover,job/f'cover{cover.suffix.lower()}'); cp.commit('source',artifacts=['production.json','source_spec.json'])
        production=Production.model_validate(read_json(job/'production.json')); enforce_rights(production.rights,cfg.allow_unknown_rights) if production.rights else None
        source=read_json(job/'source_spec.json')
        # ---- research ------------------------------------------------------
        if not cp.is_done('research'):
            cp.begin('research'); research_subject=f"{source.get('book_name') or ''} {source.get('subject') or title}".strip(); write_json(job/'research.json',build_research(rt.llm,rt.searcher,research_subject)); cp.commit('research',artifacts=['research.json'])
        research=read_json(job/'research.json')
        # ---- script --------------------------------------------------------
        if not cp.is_done('script'):
            cp.begin('script'); minutes=cfg.test_minutes if test else cfg.target_minutes; words=minutes*cfg.wpm
            outline=_make_outline(rt,title,source.get('book_name',''),production.mode,research,words,test)
            if not isinstance(outline,list) or not outline: raise InvalidResponseError('Invalid audiobook chapter outline')
            chdir=job/'chapters'; chdir.mkdir(exist_ok=True); from src.chapters.generator import generate_chapter,word_count
            ctx={'research':research,'previous_summary':'','previous_transition':''}
            for ch in outline:
                num=int(ch.get('number',len(list(chdir.glob('*.json')))+1)); path=chdir/f'{num:02d}.json'; data=generate_chapter(rt.llm,path,num,str(ch.get('title') or f'Chapter {num}'),ctx,int(ch.get('target_words') or max(500,words//len(outline)))); ctx={'research':research,'previous_summary':data.get('summary',''),'previous_transition':data.get('transition','')}
            scripts=[read_json(p) for p in sorted(chdir.glob('*.json'))]; final='\n\n'.join(x.get('script','') for x in scripts); (job/'final_script.txt').write_text(final,encoding='utf-8'); write_json(job/'final_script.json',{'word_count':word_count(final),'chapters':scripts,'target_minutes':minutes,'wpm':cfg.wpm}); cp.commit('script',artifacts=['final_script.txt','final_script.json'])
        # ---- audio ---------------------------------------------------------
        narration=job/'narration.mp3'
        if not cp.is_done('audio'):
            cp.begin('audio'); from src.narration.engine import generate_all; from src.narration.tts import TTSProvider
            files=generate_all(TTSProvider(cfg.tts_profile),job/'chapters',job/'audio'); concat_audio(files,narration); cp.commit('audio',artifacts=['narration.mp3'])
        # ---- fixed scene ---------------------------------------------------
        final=job/'final.mp4'
        persons=_files(cfg.path(cfg.persons_dir),IMAGE_EXTS); backgrounds=_files(cfg.path(cfg.backgrounds_dir),VIDEO_EXTS); music=_files(cfg.path(cfg.music_dir),AUDIO_EXTS)
        if not persons: raise ConfigError(f'No person PNG/image files in {cfg.path(cfg.persons_dir)}')
        if not backgrounds: raise ConfigError(f'No premade background videos in {cfg.path(cfg.backgrounds_dir)}')
        if not cp.is_done('visuals'):
            cp.begin('visuals')
            if not music: log.warning('no background music files; narration will be used alone')
            person=random.choice(persons)
            write_json(job/'visual_selection.json',{'person':person.name,'backgrounds':[x.name for x in backgrounds],'music':[x.name for x in music]})
            cp.commit('visuals',artifacts=['visual_selection.json'])
        if not cp.is_done('render'):
            cp.begin('render')
            sel=read_json(job/'visual_selection.json'); person=Path(cfg.path(cfg.persons_dir))/sel['person']
            backgrounds=[Path(cfg.path(cfg.backgrounds_dir))/x for x in sel['backgrounds'] if (Path(cfg.path(cfg.backgrounds_dir))/x).is_file()]
            music=[Path(cfg.path(cfg.music_dir))/x for x in sel.get('music',[]) if (Path(cfg.path(cfg.music_dir))/x).is_file()]
            render_scene(narration,cover,person,backgrounds,music,final,cfg.path(cfg.scene_layout_file),rt.settings.render.width,rt.settings.render.height,rt.settings.render.fps,rt.settings.render.crf,rt.settings.render.preset,cfg.music_volume,cfg.background_order,job/'render')
            cp.commit('render',artifacts=['final.mp4'])
        # ---- QA ------------------------------------------------------------
        if not cp.is_done('qa'):
            cp.begin('qa'); expected=duration_of(narration); result=validate_video(final,rt.settings.render.width,rt.settings.render.height,expected); write_json(job/'qa.json',result.model_dump());
            if not result.ok: raise InvalidResponseError('Audiobook QA failed: '+', '.join(result.errors))
            cp.commit('qa',artifacts=['qa.json'])
        # ---- metadata + thumbnail -----------------------------------------
        thumbnail=job/'thumbnail.jpg'
        if not cp.is_done('package'):
            cp.begin('package'); raw=read_json(job/'metadata.json',None) or {}
            description_file=spec['description']; hashtags_file=spec['hashtags']; title_file=spec['title']
            if not raw:
                generated=generate_metadata(rt.llm,title,(job/'final_script.txt').read_text(encoding='utf-8'),channel='audiobook',book=source.get('book_name') or book.name)
                raw=generated
            if title_file: raw['title']=title_file
            if description_file: raw['description']=description_file
            if hashtags_file: raw['hashtags']=extract_hashtags(hashtags_file)
            # Hashtags are kept in the description as well; YouTube's uploader remains untouched.
            tags=' '.join(raw.get('hashtags') or [])
            if tags and tags not in raw.get('description',''): raw['description']=(raw.get('description','').rstrip()+"\n\n"+tags).strip()
            if not raw.get('title'): raw['title']=title
            write_json(job/'metadata.json',raw)
            category=spec['category'] or str(rt.llm.json(f'''Classify this audiobook topic into ONE category from: motivation, self_discipline, finance, mindset, psychology, success, relationships, spirituality, productivity, biography, general. Return ONLY JSON {{"category":""}}. Title: {title}''',max_tokens=200,temperature=.0).get('category','general')).lower()
            template=choose_template(cfg.path(cfg.thumbnail_templates_dir),category); layout=load_layout(cfg.path(cfg.thumbnail_layout_file)); person=Path(cfg.path(cfg.persons_dir))/read_json(job/'visual_selection.json')['person']
            hook=raw.get('thumbnail_hook','')
            if not hook:
                generated_hook=rt.llm.json(f'''Create ONE thumbnail hook for this audiobook. Return ONLY JSON {{"hook":""}}. It must be SHORT, curiosity/pain-driven, not a copy of the title, 3 to 6 words, strong but honest. Title: {title}''',max_tokens=300,temperature=.5); hook=str(generated_hook.get('hook','')).strip()
            raw['thumbnail_hook']=hook
            write_json(job/'metadata.json',raw); compose(template,cover,person,hook,thumbnail,layout,category); cp.commit('package',artifacts=['metadata.json','thumbnail.jpg'])
        meta=read_json(job/'metadata.json')
        # ---- upload: intentionally uses the original YouTube client/publish implementation unchanged ----
        yt_state=job/'youtube_result.json'
        def save_youtube(data): write_json(yt_state,data); cp.sync()
        if not cp.is_done('upload'):
            cp.begin('upload'); yt=YouTube('YOUTUBE_TOKEN_JSON_BOOKS',rt.settings,rt.budget,rt.env); publish(yt,job_id,final,thumbnail,meta,rt.env.get('YOUTUBE_VISIBILITY_BOOKS','private'),yt_state,save_youtube,existing=read_json(yt_state,None)); cp.commit('upload',artifacts=['youtube_result.json'])
        if not cp.is_done('thumbnail'): cp.begin('thumbnail'); cp.commit('thumbnail',artifacts=[])
        if not cp.is_done('verify'): cp.begin('verify'); cp.commit('verify',artifacts=[])
        if not cp.is_done('cleanup'):
            cp.begin('cleanup'); topic_queue.mark_done(book,topic); 
            if rt.drive:
                topic_queue.sync_queue(book,rt.drive); rt.drive.clear_active_job(job_id); rt.drive.delete_remote(job_id,LARGE_ARTIFACTS)
            cp.commit('cleanup',artifacts=[])
        cp.complete(video_id=read_json(yt_state).get('video_id')); log.info('audiobook job complete',video_id=read_json(yt_state).get('video_id')); return job_id
    except Exception as exc:
        status=topic_queue.mark_failed(book,topic,exc,cfg.max_attempts)
        if rt.drive:
            try: topic_queue.sync_queue(book,rt.drive)
            except Exception: pass
        cp.fail(exc,permanent=isinstance(exc,(RightsBlocked,ConfigError))); log.error('audiobook job failed',error=str(exc)[:300],topic_status=status); raise
