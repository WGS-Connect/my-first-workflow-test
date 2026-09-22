"""Asset discovery and metadata helpers for the new audiobook pipeline."""
from __future__ import annotations
import re
from pathlib import Path
from src.errors import ConfigError

IMAGE_EXTS={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff','.avif'}
VIDEO_EXTS={'.mp4','.mov','.m4v','.mkv','.webm','.avi','.ts'}
AUDIO_EXTS={'.mp3','.wav','.m4a','.aac','.flac','.ogg','.opus'}

def first_matching(folder: Path, prefixes, exts):
    for p in sorted(folder.iterdir() if folder.is_dir() else []):
        if p.is_file() and p.suffix.lower() in exts and p.stem.lower() in prefixes: return p
    return None

def find_cover(topic_dir: Path) -> Path:
    for p in sorted(topic_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS and p.stem.lower() in {'cover','book-cover','book_cover'}: return p
    for p in sorted(topic_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS: return p
    raise ConfigError(f"No book cover image found in {topic_dir}")

def read_optional(topic_dir: Path, name: str) -> str:
    p=topic_dir/name
    return p.read_text(encoding='utf-8').strip() if p.is_file() else ''

def read_spec(topic_dir: Path) -> dict:
    title=read_optional(topic_dir,'title.txt')
    description=read_optional(topic_dir,'description.txt')
    hashtags=read_optional(topic_dir,'hashtags.txt')
    kind=read_optional(topic_dir,'type.txt').lower()
    category=read_optional(topic_dir,'category.txt').lower()
    if kind not in {'book','topic'}: kind=''
    return {'title':title,'description':description,'hashtags':hashtags,'type':kind,'category':category}

def extract_hashtags(raw: str) -> list[str]:
    return [x for x in re.split(r'[\s,]+',raw.strip()) if x.startswith('#')]

def clean_hook(text: str, min_words=3, max_words=6) -> str:
    words=re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text or '')
    return ' '.join(words[:max_words]).upper() if len(words)>=min_words else ''
