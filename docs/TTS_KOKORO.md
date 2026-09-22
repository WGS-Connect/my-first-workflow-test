# Kokoro voice lab

Run `python scripts/voice_lab.py`. The browser UI reads `config/voice_lab.yaml`, lists the Kokoro catalog, synthesizes previews, supports speed/pitch and A/B blend, and writes the selected configuration to `config/voice_settings.txt`.

The production TTS provider reads that file automatically. No Python code edit is required after the one-time selection.
