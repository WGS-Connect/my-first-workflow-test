# Audiobook YouTube Automation — Production Build

This repository is now a **single-channel audiobook production system**. The old finance channel and the old audiobook 50-image visual-generation workflow have been removed.

## Production workflow

```text
one topic folder → optional metadata → rights/content mode → research
→ 60-minute structured script → Kokoro narration → fixed scene
→ exact-duration video + low music → template thumbnail → existing YouTube uploader
```

### Default duration
`AUDIOBOOK_TARGET_MINUTES=60` and `130 WPM` by default. Test mode is intentionally short and uploads privately.

## Topic library

Each topic is its own folder:

```text
assets/audiobook/books/
└── Rich Dad Poor Dad/
    ├── Topic 001/
    │   ├── cover.jpg          # any supported image type
    │   ├── title.txt          # optional; exact YouTube title when present
    │   ├── description.txt    # optional; exact description when present
    │   ├── hashtags.txt       # optional; exact hashtags when present
    │   ├── type.txt           # optional: book OR topic
    │   └── category.txt       # optional thumbnail category
    └── Topic 002/
        └── ...
```

If metadata is missing, AI generates only the missing part. A provided `title.txt` is never rewritten. The title is used to identify the underlying book when possible.

`type.txt=book` produces an original long-form summary/analysis of the named book. `type.txt=topic` produces an original educational script. If `type.txt` is missing, the model classifies the input.

## Fixed video scene assets

The current pipeline expects all scene assets under the audiobook asset root, not at the top-level `assets/` folder. This matches the runtime config in `src/config.py` and the GitHub Actions preflight checks.

```text
assets/
├── audiobook/
│   ├── books/                  # topic folders and source material
│   ├── persons/                # transparent person images
│   ├── backgrounds/            # premade moving background videos
│   ├── music/                  # background music
│   ├── thumbnails/            # finished category templates
│   ├── scene.yaml
│   ├── thumbnail_layout.yaml
│   └── voice_settings.txt
├── thumbnails/                 # legacy/top-level folder; not used by current config
└── ...
```

Important:
- `assets/audiobook/persons` is required by the runtime.
- `assets/audiobook/backgrounds` is required by the runtime.
- `assets/audiobook/books` is required and must contain a book/topic structure such as `assets/audiobook/books/<BOOK>/<TOPIC>/`.
- Top-level `assets/persons`, `assets/backgrounds`, and `assets/music` are not the active runtime layout for this repository.

The bench is **not generated**. It remains part of the premade background scene. The person is composited above the bench, and the book is composited onto the bench at fixed coordinates. Only the background video moves.

## Thumbnail system

You provide finished category backgrounds/templates. The engine does not redesign them. It selects a category template, places the current book cover and the same selected person in fixed areas, and generates a **3–8 word short, curiosity/pain-driven hook** that is not a copy of the title.

Common raster image formats are accepted for covers/templates: JPEG, PNG, WebP, BMP, TIFF and AVIF when Pillow supports it.

## One-time voice setup

Run:

```bash
python scripts/voice_lab.py
```

A local browser UI opens with the Kokoro catalog. You can audition voices, change speed, pitch and A/B blend. Click **Save Production Voice** when satisfied. The result is stored in `config/voice_settings.txt`, which production reads automatically.

`BLEND=0` uses Voice A. A blend such as `BLEND=35` mixes Voice A at 65% with Voice B at 35%. Pitch is in semitones.

## Emotion-aware narration

The script generator deliberately writes natural performance punctuation: commas for breathing, em dashes for emphasis, ellipses for reflective pauses, short sentences for impact, question marks for questioning delivery and paragraph breaks for larger pauses. It does not inject `[sad]`, `[pause]`, SSML or stage directions.

## Fast/hybrid rendering

Background clips are checked for compatible streams and concatenated by stream copy when possible. The first background cycle always includes every supplied clip; later coverage repeats the cycle. If formats differ, only background assembly is normalized. The final person/book composite necessarily encodes once, after which narration/music are attached with the fast mux path.

## YouTube uploader

The existing `src/youtube/client.py` implementation is retained: marker-based duplicate detection, resumable upload, persisted video ID, thumbnail setting and verification remain in place.

Credentials remain `YOUTUBE_TOKEN_JSON_BOOKS` and `YOUTUBE_VISIBILITY_BOOKS`.

## GitHub Actions

Only audiobook workflows remain. Required secrets are `GEMINI_API_KEY` or `GROQ_API_KEY`, `TAVILY_API_KEY` (recommended), `AUDIOBOOK_DRIVE_CREDENTIALS`, `AUDIOBOOK_DRIVE_ROOT_FOLDER_ID`, and `YOUTUBE_TOKEN_JSON_BOOKS`.

The workflow also validates the asset layout before production. If the asset directory structure is missing or incorrect, the job fails early with a clear preflight error instead of continuing into rendering.

## Local validation

```bash
pip install -r requirements-torch.txt
pip install -r requirements.txt
python -m pytest -q -m "not e2e"
python scripts/pipeline.py --channel audiobook --test
```
