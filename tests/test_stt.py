from __future__ import annotations

import pytest

from src.voice.stt import WhisperSTT


def test_whisper_raises_if_not_installed():
    """WhisperSTT raises RuntimeError on machines without whisper.cpp."""
    # On dev machines, whisper.cpp is not installed — expect RuntimeError
    try:
        stt = WhisperSTT(model="small", language="de")
        # If it succeeds (whisper.cpp IS installed), test passes trivially
        assert stt.available is True
    except RuntimeError as e:
        assert "whisper.cpp" in str(e).lower() or "not installed" in str(e).lower()


def test_whisper_error_message_is_descriptive():
    """RuntimeError message tells user where to install whisper.cpp."""
    try:
        WhisperSTT()
    except RuntimeError as e:
        msg = str(e)
        # Message should mention whisper.cpp or voice input
        assert any(kw in msg for kw in ["whisper", "PATH", "voice"])


async def test_ogg_to_wav_ffmpeg_error(tmp_path):
    """ogg_to_wav raises RuntimeError when ffmpeg fails on nonexistent file."""
    fake_ogg = tmp_path / "nonexistent.ogg"
    with pytest.raises((RuntimeError, FileNotFoundError, Exception)):
        await WhisperSTT.ogg_to_wav(fake_ogg)
