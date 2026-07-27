from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class WhisperSTT:
    """Whisper STT via whisper.cpp subprocess.

    Deployment-only dependency. Raises RuntimeError at construction if whisper.cpp
    is not installed. On a dev machine without whisper.cpp: raises RuntimeError on init.

    Usage::

        stt = WhisperSTT(model="small", language="de")
        wav = await WhisperSTT.ogg_to_wav(Path("voice.ogg"))
        text = await stt.transcribe(wav)
    """

    def __init__(self, model: str = "small", language: str = "de") -> None:
        self._model = model
        self._language = language
        self._whisper_bin: str | None = None

        # Check for whisper.cpp binary (various install locations)
        for candidate in ["whisper", "whisper.cpp", "main"]:
            path = shutil.which(candidate)
            if path:
                self._whisper_bin = path
                break

        if self._whisper_bin is None:
            raise RuntimeError(
                "whisper.cpp is not installed. "
                "Install whisper.cpp and ensure the binary is in PATH."
            )
        logger.info("WhisperSTT initialized with binary: %s", self._whisper_bin)

    @property
    def available(self) -> bool:
        """True if whisper.cpp binary is available."""
        return self._whisper_bin is not None

    async def transcribe(self, audio_path: Path) -> str:
        """Transcribe a .wav file to text using whisper.cpp subprocess.

        Args:
            audio_path: Path to a 16kHz mono .wav file.

        Returns:
            Transcribed text string (may be empty on failure).
        """
        if self._whisper_bin is None:
            raise RuntimeError("whisper.cpp not available")

        cmd = [
            self._whisper_bin,
            "-m",
            self._model,
            "-l",
            self._language,
            "-f",
            str(audio_path),
            "--output-txt",
            "--no-timestamps",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error("whisper.cpp error: %s", stderr.decode(errors="replace"))
            return ""

        return stdout.decode(errors="replace").strip()

    @staticmethod
    async def ogg_to_wav(ogg_path: Path) -> Path:
        """Convert .ogg to .wav (16kHz, mono) using ffmpeg subprocess.

        Args:
            ogg_path: Path to input .ogg file.

        Returns:
            Path to the converted .wav file (same directory, .wav extension).

        Raises:
            RuntimeError: If ffmpeg is not available or conversion fails.
        """
        wav_path = ogg_path.with_suffix(".wav")
        cmd = [
            "ffmpeg",
            "-i",
            str(ogg_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
            "-y",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode(errors='replace')}")

        return wav_path
