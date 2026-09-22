from pathlib import Path
from src.channels.topic_queue import topic_dirs, pick_book, claim_book_topic, mark_done

def make_topic(root,name,cover=True):
    p=root/name; p.mkdir(parents=True); (p/'title.txt').write_text(name,encoding='utf-8')
    if cover: (p/'cover.jpg').write_bytes(b'x')
    return p

def test_topic_folders_are_individual_queue_items(tmp_path):
    book=tmp_path/'Book'; book.mkdir(); make_topic(book,'Topic A'); make_topic(book,'Topic B')
    assert [p.name for p in topic_dirs(book)]==['Topic A','Topic B']
    assert pick_book(tmp_path)==book
    first=claim_book_topic(book)
    assert first=='Topic A'
    mark_done(book,first)
    assert claim_book_topic(book)=='Topic B'
