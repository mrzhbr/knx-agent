"""CalendarPlatform — HAL Platform adapter for CalDAV calendar events.

Wraps CalDAVAdapter (optional — gracefully degrades when not configured)
and polls for calendar changes every 15 minutes.

One CALENDAR_SENSOR device is exposed: native_id="calendar_main".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass
from src.hal.platform import Platform

if TYPE_CHECKING:
    from src.config import BrainConfig, CalDAVConfig

logger = logging.getLogger(__name__)

_CALENDAR_DEVICE_ID = "calendar_main"
_POLL_INTERVAL_SECONDS = 15 * 60  # 15 minutes


class CalendarPlatform(Platform):
    """HAL Platform for CalDAV calendar data.

    Optional integration — gracefully degrades when CalDAV is not configured.
    Polls every 15 minutes and emits DeviceState changes when events change.
    """

    _name: str = "calendar"

    def __init__(self, config: "CalDAVConfig") -> None:
        self._config = config
        self._adapter: Any = None
        self._poll_task: asyncio.Task | None = None
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None
        self._last_events: list[dict] = []

    @classmethod
    def create_from_config(cls, config: "BrainConfig") -> "CalendarPlatform | None":
        """Create a CalendarPlatform from the application config.

        Returns None when CalDAV URL is not configured (optional platform).
        """
        if not config.caldav.url:
            return None
        return cls(config=config.caldav)

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Kalender"

    @property
    def error_message(self) -> str:
        return "⚠ Kalender ist offline — keine Kalenderdaten verfügbar."

    async def start(self) -> None:
        """Create and start CalDAVAdapter, begin poll loop."""
        from src.integrations.calendar import CalDAVAdapter

        self._adapter = CalDAVAdapter(config=self._config)
        await self._adapter.start()
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("CalendarPlatform started")

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._adapter:
            await self._adapter.stop()
        logger.info("CalendarPlatform stopped")

    async def health_check(self) -> bool:
        if self._adapter is None:
            return True  # not started yet — not a failure
        return await self._adapter.health_check()

    async def discover_devices(self) -> list[DeviceInfo]:
        """Return a single CALENDAR_SENSOR device."""
        device_id = DeviceId(platform=self._name, native_id=_CALENDAR_DEVICE_ID)
        info = DeviceInfo(
            id=device_id,
            name="Kalender",
            device_type=DeviceType.CALENDAR_SENSOR,
            room="Haus",
            sensor_class=SensorClass.GENERIC,
            platform_data={},
        )
        return [info]

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Return calendar state from cached events."""
        return self._build_device_state(device_id, self._last_events)

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Store the HAL event callback. Poll loop calls it on changes."""
        self._event_callback = callback

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        raise NotImplementedError("Platform 'calendar' is read-only")

    # ── Public API ────────────────────────────────────────────────────────

    async def get_upcoming_events(self, hours: int = 24) -> list[dict]:
        """Return upcoming calendar events."""
        if self._adapter is None:
            return []
        return await self._adapter.get_upcoming_events(hours=hours)

    def format_for_prompt(self) -> str:
        """Format upcoming events as German-language text."""
        if self._adapter is None:
            return ""
        return self._adapter.format_for_prompt()

    # ── Internal ──────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Periodically fetch calendar events and emit state changes."""
        while True:
            try:
                new_events = await self._adapter.get_upcoming_events(hours=24)
                if new_events != self._last_events:
                    self._last_events = new_events
                    if self._event_callback:
                        device_id = DeviceId(platform=self._name, native_id=_CALENDAR_DEVICE_ID)
                        state = self._build_device_state(device_id, new_events)
                        await self._event_callback(state)
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Calendar poll error: %s", e)
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def _build_device_state(self, device_id: DeviceId, events: list[dict]) -> DeviceState:
        """Convert event list to DeviceState."""
        events_today = len(events)
        next_event = ""
        if events:
            first = events[0]
            summary = first.get("summary", "")
            start = first.get("start", "")
            next_event = f"{summary} {start}".strip() if start else summary

        events_summary = ", ".join(
            f"{e.get('start', '')} {e.get('summary', '')}".strip() for e in events[:10]
        )

        return DeviceState(
            device_id=device_id,
            state={
                "next_event": next_event,
                "events_today": events_today,
                "events_summary": events_summary,
            },
            last_updated=datetime.now(),
            source="calendar",
        )
