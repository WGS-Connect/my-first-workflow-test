"""Persistent queue for one audiobook channel.

Each leaf topic folder is one complete YouTube production. A book directory may
contain any number of topic folders. The queue is still synced to Drive using the
original merge/checkpoint protocol.
"""
from __future__ import annotations
import hashlib, time
from pathlib import Path
from src.utils.io import read_json, write_json
from src.utils.locks import file_lock

STATE_FILE = "queue_state.json"
COVER_EXTS={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff','.avif','.svg','.pdf'}
COMPLETED_FILE = "completed_topics.json"


def _lock_path(book: Path) -> Path:
    return book / ".topic_queue.lock"


def topic_dirs(book: Path) -> list[Path]:
    book = Path(book)
    if not book.is_dir(): return []
    out=[]
    for p in sorted(book.iterdir()):
        if not p.is_dir() or p.name.startswith(".") or p.name.lower() in {"__pycache__","_archive"}: continue
        # A topic folder must have a cover, title, or an explicit marker.
        if any(x.is_file() for x in p.iterdir() if x.is_file() and x.name.lower() in {"title.txt","type.txt","description.txt","hashtags.txt"}) or any(x.is_file() and x.suffix.lower() in COVER_EXTS and x.stem.lower() in {'cover','book-cover','book_cover'} for x in p.iterdir()):
            out.append(p)
    return out


def topic_key(topic_dir: Path) -> str:
    return topic_dir.name


def normalize(raw) -> dict:
    data = dict(raw) if isinstance(raw, dict) else {}
    items={}
    for topic, entry in (data.get("topics") or {}).items():
        e=dict(entry); e.setdefault("status","pending"); e.setdefault("attempts",0); e.setdefault("rev",0)
        e.setdefault("updated_at", e.get("last_failed_at") or e.get("claimed_at") or e.get("completed_at") or 0)
        items[topic]=e
    return {"version":4,"topics":items}


def merge_queue(local, remote):
    a,b=normalize(local),normalize(remote); merged={}
    for topic in set(a["topics"])|set(b["topics"]):
        x,y=a["topics"].get(topic),b["topics"].get(topic)
        if x is None or y is None: merged[topic]=dict(x or y); continue
        if x["status"]=="completed" or y["status"]=="completed":
            winner=min((e for e in (x,y) if e["status"]=="completed"),key=lambda e:e.get("completed_at",0) or 1e18)
        else: winner=max((x,y),key=lambda e:(e.get("rev",0),e.get("updated_at",0)))
        entry=dict(winner); entry["attempts"]=max(x.get("attempts",0),y.get("attempts",0)); entry["rev"]=max(x.get("rev",0),y.get("rev",0)); merged[topic]=entry
    return {"version":4,"topics":merged}


def _load(book): return normalize(read_json(Path(book)/STATE_FILE,None))

def _save(book,data):
    write_json(Path(book)/STATE_FILE,data)
    write_json(Path(book)/COMPLETED_FILE, sorted(k for k,v in data["topics"].items() if v["status"]=="completed"))

def _touch(e,now): e["rev"]=int(e.get("rev",0))+1; e["updated_at"]=now

def job_id(book: Path, topic: str) -> str:
    digest=hashlib.sha1(f"{Path(book).name}\0{topic}".encode()).hexdigest()[:10]
    safe="".join(c.lower() if c.isalnum() else "-" for c in f"{Path(book).name}-{topic}").strip("-")[:62]
    return f"audio-{safe}-{digest}"


def claim_book_topic(book, max_attempts=3, stale_seconds=21600, now=None):
    book=Path(book); now=time.time() if now is None else now
    with file_lock(_lock_path(book)):
        data=_load(book); dirs=topic_dirs(book); names=[topic_key(x) for x in dirs]; chosen=None
        for topic in names:
            e=data["topics"].get(topic)
            if e is None: continue
            if e["status"]=="in_progress":
                if now-e.get("updated_at",0)>stale_seconds:
                    e["attempts"]+=1; e["recycled"]=int(e.get("recycled",0))+1; e["last_error"]="recycled stale claim"
                    if e["attempts"]>=max_attempts: e["status"]="failed"; _touch(e,now); continue
                    e.update(claimed_at=now,last_error=None); _touch(e,now)
                elif e.get("attempts",0)>=max_attempts: e["status"]="failed"; _touch(e,now); continue
                chosen=topic; break
        if chosen is None:
            for topic in names:
                e=data["topics"].get(topic,{"status":"pending","attempts":0,"rev":0,"updated_at":now})
                if e.get("status","pending")=="pending":
                    e.update(status="in_progress",job_id=job_id(book,topic),claimed_at=now,last_error=None)
                    _touch(e,now); data["topics"][topic]=e; chosen=topic; break
        _save(book,data); return chosen


def pick_book(root):
    root=Path(root)
    for book in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        dirs=topic_dirs(book)
        if not dirs: continue
        data=_load(book)
        if any(data["topics"].get(topic_key(t),{}).get("status","pending") in ("pending","in_progress") for t in dirs): return book
    return None


def topic_path(book: Path, topic: str) -> Path:
    p=Path(book)/topic
    if not p.is_dir(): raise FileNotFoundError(f"Topic folder not found: {p}")
    return p


def mark_done(book,topic,now=None):
    book=Path(book); now=time.time() if now is None else now
    with file_lock(_lock_path(book)):
        data=_load(book); e=data["topics"].setdefault(topic,{"attempts":0,"rev":0}); e.update(status="completed",completed_at=now,last_error=None); _touch(e,now); _save(book,data)


def mark_failed(book,topic,error,max_attempts=3,now=None):
    book=Path(book); now=time.time() if now is None else now
    with file_lock(_lock_path(book)):
        data=_load(book); e=data["topics"].setdefault(topic,{"attempts":0,"rev":0,"status":"in_progress"}); e["attempts"]=int(e.get("attempts",0))+1; e["last_error"]=str(error)[:2000]; e["last_failed_at"]=now; e["status"]="failed" if e["attempts"]>=max_attempts else "in_progress"; _touch(e,now); _save(book,data); return e["status"]


def requeue(book,topic=None,now=None):
    book=Path(book); now=time.time() if now is None else now; revived=[]
    with file_lock(_lock_path(book)):
        data=_load(book)
        for name,e in data["topics"].items():
            if e["status"]=="failed" and topic in (None,name): e.update(status="pending",attempts=0,last_error=None); _touch(e,now); revived.append(name)
        _save(book,data)
    return revived


def sync_queue(book,drive,channel="audiobook"):
    book=Path(book)
    with file_lock(_lock_path(book)):
        merged=drive.sync_json(("queue",channel,f"{book.name}.json"),book/STATE_FILE,merge_queue); _save(book,normalize(merged)); return merged
