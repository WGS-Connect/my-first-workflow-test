from src.utils.io import read_json,write_json

def word_count(s): return len((s or "").split())

def generate_chapter(gemini,path,number,title,context,target_words):
    existing=read_json(path)
    if existing and existing.get('status')=='completed' and word_count(existing.get('script',''))>100:return existing
    p=f"""Write chapter {number}: {title} for a long-form ORIGINAL educational audiobook. Target about {target_words} words.
Research/context: {context.get('research',{})}
Previous continuity: {context.get('previous_summary','')}

RETENTION AND DELIVERY:
- Begin with a compelling spoken transition from the previous chapter.
- Use natural spoken narration, not an essay.
- Use curiosity, pain, stakes, a question, a surprising contrast, a story/example, or a practical challenge where appropriate.
- Use punctuation as acting cues for Kokoro: commas for breathing, em dashes for emphasis, ellipses (...) for reflective pauses, short sentences for impact, question marks for genuine questions, exclamation marks sparingly, and paragraph breaks for larger pauses.
- Never write labels such as [pause], [sad], [angry], [excited], SSML, stage directions, or emotion names. Make emotion visible through natural wording and punctuation.
- Avoid repetitive filler, fake quotations, unsupported statistics, and copied book wording.
- End by creating a natural bridge into the next chapter.

Return JSON: title, script, summary, important_concepts, unresolved_ideas, terminology, transition, word_count."""
    d=gemini.json(p); d.update(chapter_number=number,status='completed',word_count=word_count(d.get('script',''))); write_json(path,d); return d
