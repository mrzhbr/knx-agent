"""CalDAV calendar integration — optional, gracefully degrades if not configured.

Reads upcoming events from a CalDAV server (e.g., Nextcloud).
Cache TTL: 15 minutes (matches WeatherAdapter pattern).

Usage:
    adapter = CalDAVAdapter(config)
    await adapter.start()
    events = await adapter.get_upcoming_events(hours=24)
    prompt_text = adapter.format_for_prompt()
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Cache TTL in seconds (15 minutes)
_CACHE_TTL_SECONDS = 15 * 60


class CalDAVAdapter:
    """Fetches calendar events from a CalDAV server.

    Optional integration — returns empty list when not configured or
    when the caldav library is not installed.

    Usage::

        adapter = CalDAVAdapter(config)
        await adapter.start()
        events = await adapter.get_upcoming_events(hours=24)
        # events: [{"summary": str, "start": str, "end": str, "location": str, ...}]
    """

    name: str = "caldav"

    def __init__(self, config: Any) -> None:
        """
        Args:
            config: CalDAVConfig with url, username, password, calendar_name fields.
        """
        self._config = config
        self._cache: list[dict] | None = None
        self._cache_time: datetime | None = None
        self._client: Any = None
        self._calendar: Any = None

    async def start(self) -> None:
        """Initialize the CalDAV client if configuration is present."""
        if not self._config.url:
            logger.info("CalDAV: no URL configured, adapter inactive")
            return

        try:
            import caldav  # type: ignore[import-not-found]  # noqa: F401

            client_factory: Any = caldav.DAVClient
            self._client = client_factory(
                url=self._config.url,
                username=self._config.username,
                password=self._config.password,
            )
            logger.info("CalDAV client initialized for %s", self._config.url)
        except ImportError:
            logger.warning("CalDAV: 'caldav' library not installed, adapter inactive")
        except Exception as e:
            logger.warning("CalDAV: client initialization failed: %s", e)

    async def stop(self) -> None:
        """Shutdown the CalDAV adapter (no-op for HTTP client)."""
        self._client = None
        self._calendar = None

    async def health_check(self) -> bool:
        """Return True if CalDAV is reachable or not configured (optional integration)."""
        if not self._config.url:
            return True  # Not configured — not a failure
        if self._client is None:
            return False
        try:
            principal = self._client.principal()
            return principal is not None
        except Exception:
            return False

    async def get_upcoming_events(self, hours: int = 24) -> list[dict]:
        """Fetch upcoming calendar events within the next N hours.

        Results are cached for 15 minutes.

        Returns:
            List of dicts with keys: summary, start, end, location, description.
            Returns empty list if CalDAV is not configured or unavailable.
        """
        if not self._config.url or self._client is None:
            return []

        # Check cache
        now = datetime.now()
        if (
            self._cache is not None
            and self._cache_time is not None
            and (now - self._cache_time).total_seconds() < _CACHE_TTL_SECONDS
        ):
            logger.debug("CalDAV: returning cached events")
            return self._cache

        try:
            return await self._fetch_events(hours, now)
        except Exception as e:
            logger.warning("CalDAV: fetch failed, returning stale cache: %s", e)
            if self._cache is not None:
                return self._cache
            return []

    async def _fetch_events(self, hours: int, now: datetime) -> list[dict]:
        """Fetch events from CalDAV server (no caching)."""
        try:
            import caldav  # type: ignore[import-not-found]  # noqa: F401
            from icalendar import Calendar as iCalendar  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("CalDAV: required libraries not installed")
            return []

        try:
            principal = self._client.principal()
            calendars = principal.calendars()

            # Find the configured calendar by name, or use the first one
            target_calendar = None
            for cal in calendars:
                cal_name = getattr(cal, "name", "") or ""
                if (
                    not self._config.calendar_name
                    or self._config.calendar_name.lower() in str(cal_name).lower()
                ):
                    target_calendar = cal
                    break

            if target_calendar is None and calendars:
                target_calendar = calendars[0]

            if target_calendar is None:
                logger.warning("CalDAV: no calendar found")
                self._cache = []
                self._cache_time = now
                return []

            # Search for events in the time window
            start = now
            end = now + timedelta(hours=hours)
            events_raw = target_calendar.date_search(start=start, end=end)

            events: list[dict] = []
            for event_obj in events_raw:
                try:
                    cal_data = iCalendar.from_ical(event_obj.data)
                    for component in cal_data.walk():
                        if component.name == "VEVENT":
                            summary = str(component.get("SUMMARY", ""))
                            dtstart = component.get("DTSTART")
                            dtend = component.get("DTEND")
                            location = str(component.get("LOCATION", ""))
                            description = str(component.get("DESCRIPTION", ""))

                            start_str = ""
                            end_str = ""
                            if dtstart:
                                dt = dtstart.dt
                                if hasattr(dt, "strftime"):
                                    start_str = (
                                        dt.strftime("%H:%M") if hasattr(dt, "hour") else str(dt)
                                    )
                                else:
                                    start_str = str(dt)
                            if dtend:
                                dt = dtend.dt
                                if hasattr(dt, "strftime"):
                                    end_str = (
                                        dt.strftime("%H:%M") if hasattr(dt, "hour") else str(dt)
                                    )
                                else:
                                    end_str = str(dt)

                            events.append(
                                {
                                    "summary": summary,
                                    "start": start_str,
                                    "end": end_str,
                                    "location": location,
                                    "description": description,
                                }
                            )
                except Exception as e:
                    logger.debug("CalDAV: error parsing event: %s", e)

            self._cache = events
            self._cache_time = now
            logger.info("CalDAV: fetched %d events for next %dh", len(events), hours)
            return events

        except Exception as e:
            logger.warning("CalDAV: _fetch_events error: %s", e)
            return []

    def format_for_prompt(self) -> str:
        """Format upcoming events as German-language text for the embodied prompt.

        Returns empty string if no events or not configured.
        """
        if not self._cache:
            return ""

        lines: list[str] = []

        for event in self._cache[:10]:  # Cap to avoid huge prompts
            summary = event.get("summary", "?")
            start = event.get("start", "")
            location = event.get("location", "")

            if start:
                lines.append(f"{start} {summary}" + (f" ({location})" if location else ""))
            else:
                lines.append(summary + (f" ({location})" if location else ""))

        if not lines:
            return ""

        return "Heute: " + ", ".join(lines)
