"""Matrix bot client for KNX Agent.

Uses matrix-nio AsyncClient. Runs sync_forever in a background asyncio task
so it doesn't block the main event loop.

Voice handling: detect m.audio attachments, download, convert via ffmpeg,
transcribe via WhisperSTT if available.
"""

from __future__ import annotations

import asyncio
import io
import logging
import secrets
import tempfile
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from nio import (  # type: ignore[import-not-found]
    AsyncClient,
    Event,
    MatrixRoom,
    RoomMessageAudio,
    RoomMessageText,
)

try:
    from src.voice.stt import WhisperSTT as _WhisperSTT
except ImportError:
    _WhisperSTT = None  # type: ignore[assignment, misc]

WhisperSTT = _WhisperSTT  # Module-level reference for patching in tests

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Matrix user ID -> family profile name mapping
# Populated from config at startup via set_user_map()
_USER_MAP: dict[str, str] = {}


class MatrixBot:
    """Matrix bot client for receiving and sending messages.

    Lifecycle:
    - Instantiate with server credentials
    - Call set_message_handler() to register the brain callback
    - Call start() — this logs in and launches sync_forever as a background task
    - Call stop() for graceful shutdown
    """

    name: str = "matrix_bot"

    def __init__(
        self,
        homeserver: str,
        user_id: str,
        password: str,
        allowed_room_ids: list[str],
        allowed_user_ids: list[str] | None = None,
    ) -> None:
        self._homeserver = homeserver
        self._user_id = user_id
        self._password = password
        self._allowed_room_ids = {room_id.lower() for room_id in allowed_room_ids}
        self._allowed_user_ids = {user_id.lower() for user_id in (allowed_user_ids or [])}
        self._client: AsyncClient | None = None
        self._sync_task: asyncio.Task | None = None
        self._message_handler: Callable | None = None
        self._whisper: Any | None = None

    def set_message_handler(self, callback: Callable) -> None:
        """Register the brain processing callback.

        The callback receives (room_id: str, sender: str, text: str) and
        should return a str response.
        """
        self._message_handler = callback

    def set_whisper(self, whisper: Any) -> None:
        """Inject WhisperSTT for voice transcription (optional)."""
        self._whisper = whisper

    async def start(self) -> None:
        """Login to Matrix and start sync_forever in a background task."""
        self._client = AsyncClient(self._homeserver, self._user_id)

        response = await self._client.login(self._password)
        if hasattr(response, "access_token"):
            logger.info("Matrix bot logged in as %s", self._user_id)
        else:
            logger.error("Matrix login failed: %s", response)
            raise RuntimeError(f"Matrix login failed: {response}")

        # Initial sync to skip message history — get next_batch token first
        sync_response = await self._client.sync(timeout=10000, full_state=True)
        if hasattr(sync_response, "next_batch"):
            logger.info("Matrix initial sync done, skipping %s old events", "historical")
        else:
            logger.warning("Matrix initial sync returned no next_batch: %s", sync_response)

        # Register event callbacks AFTER initial sync so old messages are ignored
        self._client.add_event_callback(self._on_text_event, RoomMessageText)
        self._client.add_event_callback(self._on_audio_event, RoomMessageAudio)

        # Run sync_forever in background — it blocks, so wrap in task
        self._sync_task = asyncio.create_task(
            self._client.sync_forever(timeout=30000, full_state=True)
        )
        logger.info("Matrix sync_forever started")

    async def stop(self) -> None:
        """Stop syncing and close connection."""
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError as cancellation:
                del cancellation

        if self._client:
            await self._client.close()
            self._client = None
        logger.info("Matrix bot stopped")

    async def health_check(self) -> bool:
        return self._client is not None and self._sync_task is not None

    async def send(self, room_id: str, message: str) -> None:
        """Send a text message to a Matrix room."""
        if self._client is None:
            logger.error("MatrixBot.send called before start()")
            return
        try:
            await self._client.room_send(
                room_id,
                "m.room.message",
                {"msgtype": "m.text", "body": message},
            )
        except Exception as e:
            logger.error("Failed to send Matrix message: %s", e)

    async def send_image(self, room_id: str, image_bytes: bytes, filename: str) -> None:
        """Upload and send an image to a Matrix room."""
        if self._client is None:
            return
        try:
            await self._client.upload(
                io.BytesIO(image_bytes),
                content_type="image/jpeg",
                filename=filename,
            )
            # For real upload, use client.upload with byte stream
            # This is a simplified version — full implementation needs BytesIO
            logger.info("Image upload placeholder for %s", filename)
        except Exception as e:
            logger.error("Failed to send Matrix image: %s", e)

    async def _on_text_event(self, room: MatrixRoom, event: Event) -> None:
        if isinstance(event, RoomMessageText):
            await self._on_text_message(room, event)

    async def _on_audio_event(self, room: MatrixRoom, event: Event) -> None:
        if isinstance(event, RoomMessageAudio):
            await self._on_audio_message(room, event)

    def _is_authorized(self, room_id: str, sender: str) -> bool:
        """Require exact room and sender allowlist matches; empty lists deny all."""
        return (
            bool(self._allowed_room_ids)
            and bool(self._allowed_user_ids)
            and room_id.lower() in self._allowed_room_ids
            and sender.lower() in self._allowed_user_ids
        )

    async def _on_text_message(self, room: Any, event: RoomMessageText) -> None:
        """Handle incoming text messages after fail-closed authorization."""
        if self._client and event.sender == self._client.user_id:
            return
        if not self._is_authorized(room.room_id, event.sender):
            logger.warning("Rejected unauthorized Matrix text event")
            return

        text = event.body.strip()
        if not text:
            return

        logger.info("Accepted Matrix text event")

        if self._message_handler:
            asyncio.create_task(self._dispatch_message(room.room_id, event.sender, text))

    async def _on_audio_message(self, room: Any, event: RoomMessageAudio) -> None:
        """Handle incoming voice/audio messages — transcribe via Whisper if available.

        Fixes:
        - Awaits ogg_to_wav() and transcribe() (both are async methods)
        - Checks response.body for download success
        - Cleans up both ogg and wav temp files in finally block
        """
        if self._client and event.sender == self._client.user_id:
            return
        if not self._is_authorized(room.room_id, event.sender):
            logger.warning("Rejected unauthorized Matrix audio event")
            return

        if self._whisper is None:
            logger.debug("Whisper not configured, ignoring audio from %s", event.sender)
            return

        client = self._client
        whisper_class = WhisperSTT
        if client is None or whisper_class is None:
            return

        wav_path: Path | None = None
        try:
            url = event.url
            if url.startswith("mxc://"):
                response = await client.download(mxc=url)
                audio_data = getattr(response, "body", None)
                if not isinstance(audio_data, bytes):
                    logger.warning("Audio download failed")
                    return

                with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                    f.write(audio_data)
                    ogg_path = Path(f.name)

                try:
                    wav_path = await whisper_class.ogg_to_wav(ogg_path)
                    text = await self._whisper.transcribe(wav_path)
                    logger.info("Whisper transcription completed (nonempty=%s)", bool(text))
                    if text and self._message_handler:
                        asyncio.create_task(
                            self._dispatch_message(room.room_id, event.sender, text)
                        )
                finally:
                    ogg_path.unlink(missing_ok=True)
                    if wav_path is not None:
                        wav_path.unlink(missing_ok=True)
        except Exception as e:
            logger.error("Audio message handling failed: %s", e)

    async def _dispatch_message(self, room_id: str, sender: str, text: str) -> None:
        """Dispatch message to brain handler and send response."""
        if self._message_handler is None:
            return
        try:
            response = await self._message_handler(room_id, sender, text)
            if response:
                await self.send(room_id, response)
        except Exception:
            correlation_id = secrets.token_hex(4)
            logger.exception("Message handler error (reference=%s)", correlation_id)
            await self.send(
                room_id,
                f"Fehler bei der Verarbeitung. Referenz: {correlation_id}",
            )
