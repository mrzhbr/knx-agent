# bot/

## Purpose
Matrix bot gateway. Entry point for all user interactions.

## Key Files
- `matrix.py` — `MatrixBot`: nio async client; `_on_text_message`/`_on_audio_message` handlers; `_dispatch_message` routes to supervisor; `send`/`send_image` for responses; `_USER_MAP` maps Matrix IDs to display names; `set_message_handler`/`set_whisper` for dependency injection

## Conventions
- Ignores own messages
- Only responds to authorized users (family profiles)
- Voice messages: download → `ogg_to_wav` → whisper.cpp → text pipeline
- Images sent via Matrix content upload (detection snapshots)

## Dependencies
- Depends on: `agents/` (supervisor), `voice/` (WhisperSTT), matrix-nio
- Depended on by: `background/` (monitor/reflection send notifications)
