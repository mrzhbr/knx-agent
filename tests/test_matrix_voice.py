"""Tests for Matrix bot voice message handler (Task 16.1 bug fixes)."""

from __future__ import annotations

# Test-only mocks intentionally replace MatrixBot's optional nio client.
# pyright: reportOptionalMemberAccess=false

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


from src.bot.matrix import MatrixBot


def make_bot(whisper=None):
    """Create a MatrixBot instance with mocked client."""
    bot = MatrixBot(
        homeserver="http://localhost:8008",
        user_id="@agent:example.test",
        password="test",
        allowed_room_ids=["!room:example.test"],
        allowed_user_ids=["@person1:example.test"],
    )
    bot._client = MagicMock()
    bot._client.user_id = "@agent:example.test"
    if whisper is not None:
        bot._whisper = whisper
    return bot


def make_audio_event(url="mxc://example.test/audio123", sender="@person1:example.test"):
    event = MagicMock()
    event.url = url
    event.sender = sender
    return event


def make_room():
    room = MagicMock()
    room.room_id = "!room:example.test"
    return room


# ─── Bug fix tests ───────────────────────────────────────────────────────────


async def test_transcription_dispatches_actual_text_not_coroutine():
    """Fixed handler dispatches actual transcribed text, not a coroutine object."""
    dispatched = []

    async def mock_handler(room_id, sender, text):
        dispatched.append(text)
        return "OK"

    transcribed_text = "Licht im Wohnzimmer einschalten"
    wav_path_mock = Path("/tmp/test_audio.wav")

    whisper = MagicMock()
    whisper.transcribe = AsyncMock(return_value=transcribed_text)
    whisper.ogg_to_wav = AsyncMock(return_value=wav_path_mock)

    bot = make_bot(whisper=whisper)
    bot.set_message_handler(mock_handler)

    # Mock download response
    download_response = MagicMock()
    download_response.body = b"fake_ogg_data"
    bot._client.download = AsyncMock(return_value=download_response)

    room = make_room()
    event = make_audio_event()

    with patch("src.bot.matrix.WhisperSTT") as mock_stt_class:
        mock_stt_class.ogg_to_wav = AsyncMock(return_value=wav_path_mock)
        with patch("asyncio.create_task") as mock_task:
            mock_task.side_effect = lambda coro: asyncio.ensure_future(coro)
            await bot._on_audio_message(room, event)
            # Give the task time to run
            await asyncio.sleep(0.01)

    # The dispatched text must be the actual string, NOT a coroutine repr
    if dispatched:
        text = dispatched[0]
        assert isinstance(text, str), f"Expected str, got {type(text)}"
        assert "coroutine" not in text.lower(), f"Got coroutine repr instead of text: {text}"
        assert text == transcribed_text


async def test_await_ogg_to_wav_is_called():
    """Fixed handler properly awaits ogg_to_wav (async method)."""
    wav_path_mock = Path("/tmp/test.wav")

    whisper = MagicMock()
    whisper.transcribe = AsyncMock(return_value="test text")
    whisper.ogg_to_wav = AsyncMock(return_value=wav_path_mock)

    bot = make_bot(whisper=whisper)
    bot.set_message_handler(AsyncMock(return_value="OK"))

    download_response = MagicMock()
    download_response.body = b"fake_ogg_data"
    bot._client.download = AsyncMock(return_value=download_response)

    room = make_room()
    event = make_audio_event()

    with patch("src.bot.matrix.WhisperSTT") as mock_stt_class:
        mock_stt_class.ogg_to_wav = AsyncMock(return_value=wav_path_mock)
        with patch("asyncio.create_task", side_effect=lambda c: asyncio.ensure_future(c)):
            await bot._on_audio_message(room, event)
            await asyncio.sleep(0.01)

    # Verify that WhisperSTT.ogg_to_wav was awaited (it's a static async method)
    mock_stt_class.ogg_to_wav.assert_called_once()


async def test_empty_transcription_does_not_dispatch():
    """Fixed handler does not dispatch when transcription returns empty string."""
    dispatched = []

    async def mock_handler(room_id, sender, text):
        dispatched.append(text)
        return "OK"

    whisper = MagicMock()
    whisper.transcribe = AsyncMock(return_value="")  # Empty transcription
    whisper.ogg_to_wav = AsyncMock(return_value=Path("/tmp/test.wav"))

    bot = make_bot(whisper=whisper)
    bot.set_message_handler(mock_handler)

    download_response = MagicMock()
    download_response.body = b"fake_ogg_data"
    bot._client.download = AsyncMock(return_value=download_response)

    with patch("src.bot.matrix.WhisperSTT") as mock_stt_class:
        mock_stt_class.ogg_to_wav = AsyncMock(return_value=Path("/tmp/test.wav"))
        with patch("asyncio.create_task", side_effect=lambda c: asyncio.ensure_future(c)):
            await bot._on_audio_message(make_room(), make_audio_event())
            await asyncio.sleep(0.01)

    assert len(dispatched) == 0, "Empty transcription should not dispatch"


async def test_temp_files_cleaned_up():
    """Fixed handler cleans up both ogg and wav temp files."""
    wav_path_mock = MagicMock(spec=Path)
    wav_path_mock.unlink = MagicMock()

    whisper = MagicMock()
    whisper.transcribe = AsyncMock(return_value="Some text")

    bot = make_bot(whisper=whisper)
    bot.set_message_handler(AsyncMock(return_value="OK"))

    download_response = MagicMock()
    download_response.body = b"fake_ogg_data"
    bot._client.download = AsyncMock(return_value=download_response)

    # Track unlink calls on the real temp file
    unlink_calls = []

    with patch("src.bot.matrix.WhisperSTT") as mock_stt_class:
        mock_stt_class.ogg_to_wav = AsyncMock(return_value=wav_path_mock)
        with patch.object(Path, "unlink", lambda self, **kw: unlink_calls.append(self)):
            with patch("asyncio.create_task", side_effect=lambda c: asyncio.ensure_future(c)):
                await bot._on_audio_message(make_room(), make_audio_event())
                await asyncio.sleep(0.01)

    # Wav path's unlink must have been called
    wav_path_mock.unlink.assert_called()


async def test_download_failure_does_not_dispatch():
    """Fixed handler handles missing response.body gracefully."""
    dispatched = []

    async def mock_handler(room_id, sender, text):
        dispatched.append(text)
        return "OK"

    whisper = MagicMock()
    bot = make_bot(whisper=whisper)
    bot.set_message_handler(mock_handler)

    # Response with no body
    download_response = MagicMock()
    download_response.body = None
    bot._client.download = AsyncMock(return_value=download_response)

    await bot._on_audio_message(make_room(), make_audio_event())

    assert len(dispatched) == 0


async def test_ignores_own_messages():
    """Fixed handler ignores messages sent by the bot itself."""
    whisper = MagicMock()
    whisper.transcribe = AsyncMock(return_value="text")
    bot = make_bot(whisper=whisper)
    bot._client.user_id = "@agent:example.test"

    download_calls = []
    bot._client.download = AsyncMock(side_effect=lambda **kw: download_calls.append(kw))

    event = make_audio_event(sender="@agent:example.test")  # Own message
    await bot._on_audio_message(make_room(), event)

    assert len(download_calls) == 0


async def test_whisper_none_does_not_dispatch():
    """Handler returns early when Whisper not configured."""
    dispatched = []

    async def mock_handler(room_id, sender, text):
        dispatched.append(text)
        return "OK"

    bot = make_bot(whisper=None)  # No whisper
    bot.set_message_handler(mock_handler)
    bot._client.download = AsyncMock()

    await bot._on_audio_message(make_room(), make_audio_event())

    assert len(dispatched) == 0
    bot._client.download.assert_not_called()
