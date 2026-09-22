# Setup

1. Install Python 3.12, FFmpeg and espeak-ng.
2. Install `requirements-torch.txt` then `requirements.txt`.
3. Put topic folders under `assets/audiobook/books/<book>/<topic>/`.
4. Put transparent people in `assets/audiobook/persons/`.
5. Put premade bench/background videos in `assets/audiobook/backgrounds/`.
6. Put music in `assets/audiobook/music/`.
7. Put category templates in `assets/audiobook/thumbnails/<category>/`.
8. Calibrate `assets/audiobook/scene.yaml` and `config/thumbnail_layout.yaml` once.
9. Run `python scripts/voice_lab.py` once and save the narrator settings.
10. Configure YouTube and Drive secrets for GitHub Actions.

Local test:

```bash
python -m pytest -q -m "not e2e"
python scripts/pipeline.py --channel audiobook --test
```
