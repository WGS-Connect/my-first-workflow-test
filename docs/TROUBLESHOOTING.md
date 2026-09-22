# Troubleshooting

### No topic found
Make sure each book directory contains topic subdirectories and each topic contains a `cover.*` or `title.txt`.

### Scene looks misaligned
Calibrate `assets/audiobook/scene.yaml` once against the master background. The `person_box` and `book_quad` are fixed for every video.

### Thumbnail looks misaligned
Calibrate `config/thumbnail_layout.yaml` once for your template family.

### Voice not found
Run the voice lab and select a voice supported by your installed Kokoro version. The exact result is saved in `config/voice_settings.txt`.

### Video rendering is slow
The final person/book composite requires one video encode. Background concatenation itself is stream-copy when source streams are compatible.
