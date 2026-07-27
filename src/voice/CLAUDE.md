# voice/

## Purpose
Speech-to-text via local whisper.cpp.

## Key Files
- `stt.py` — `WhisperSTT`: wraps whisper.cpp binary; `available` checks binary exists; `transcribe` runs inference; `ogg_to_wav` converts Matrix voice format

## Dependencies
- Depends on: whisper.cpp binary on host
- Depended on by: `bot/matrix.py` (voice message handling)
