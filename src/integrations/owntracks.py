from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import ssl
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import aiomqtt  # type: ignore[import-not-found]

# How often the background task re-checks for stale devices.
_STALE_CHECK_INTERVAL_SECONDS = 60
# Hysteresis factor: once marked home, a location update must be this many
# geofence radii away before we flip to "away". Prevents GPS jitter from
# overriding an explicit transition enter event.
_LOCATION_AWAY_HYSTERESIS = 1.5

logger = logging.getLogger(__name__)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in meters between two lat/lon coordinates."""
    R = 6_371_000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    # Clamp to [0, 1] — floating-point rounding can push `a` slightly outside
    # the valid range for near-antipodal points, which would crash sqrt(1 - a).
    a = max(0.0, min(1.0, a))
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class OwnTracksAdapter:
    """Presence adapter that subscribes to OwnTracks MQTT geofence events.

    Transition events (enter/leave) are the primary presence signal.
    Location updates serve as fallback via haversine distance calculation.
    Stale timeout marks devices as away if no message arrives within the configured window.
    """

    name: str = "owntracks"

    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        topic_prefix: str,
        home_lat: float,
        home_lon: float,
        geofence_radius_m: int,
        tracked_users: dict[str, str],
        stale_timeout_minutes: int,
        home_region: str = "Home",
        mqtt_username: str = "",
        mqtt_password: str = "",
        mqtt_tls: bool = False,
    ) -> None:
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._topic_prefix = topic_prefix
        self._home_lat = home_lat
        self._home_lon = home_lon
        self._geofence_radius_m = geofence_radius_m
        self._tracked_users = tracked_users  # "user/device" -> device_id
        self._stale_timeout_minutes = stale_timeout_minutes
        self._home_region = home_region
        self._mqtt_username = mqtt_username
        self._mqtt_password = mqtt_password
        self._mqtt_tls = mqtt_tls

        self._presence: dict[str, bool] = {}
        self._last_seen: dict[str, datetime] = {}
        self._connected: bool = False
        self._change_callbacks: list[Callable[[str, bool], Any]] = []
        self._mqtt_task: asyncio.Task | None = None
        self._stale_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the MQTT subscription loop and the periodic stale check."""
        self._mqtt_task = asyncio.create_task(self._mqtt_loop())
        self._stale_task = asyncio.create_task(self._stale_loop())
        logger.info(
            "OwnTracksAdapter started, tracking %d users on %s:%d",
            len(self._tracked_users),
            self._mqtt_host,
            self._mqtt_port,
        )

    async def stop(self) -> None:
        """Cancel background tasks."""
        for task in (self._mqtt_task, self._stale_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError as cancellation:
                del cancellation
        self._connected = False
        logger.info("OwnTracksAdapter stopped")

    async def health_check(self) -> bool:
        """Return True if currently connected to MQTT broker."""
        return self._connected

    def on_change(self, callback: Callable) -> None:
        """Register a callback for presence state changes.

        Callback signature: async cb(device_id: str, is_home: bool)
        """
        self._change_callbacks.append(callback)

    def presence_snapshot(self) -> dict[str, bool | None]:
        """Return fresh presence values; unseen or stale devices are ``None``."""
        now = datetime.now(timezone.utc)
        snapshot: dict[str, bool | None] = {}
        for device_id in self._tracked_users.values():
            last_seen = self._last_seen.get(device_id)
            value = self._presence.get(device_id)
            if last_seen is None or value is None:
                snapshot[device_id] = None
                continue
            age_minutes = (now - last_seen).total_seconds() / 60
            snapshot[device_id] = None if age_minutes > self._stale_timeout_minutes else value
        return snapshot

    def who_is_home(self) -> dict[str, bool]:
        """Return only fresh, known presence values."""
        return {
            device_id: value
            for device_id, value in self.presence_snapshot().items()
            if value is not None
        }

    def nobody_home(self) -> bool:
        """True only when every configured device is freshly known to be away."""
        snapshot = self.presence_snapshot()
        return bool(snapshot) and all(
            value is not None and not value for value in snapshot.values()
        )

    def format_for_prompt(self) -> str:
        """German-language presence summary for LLM prompts."""
        if not self._tracked_users:
            return "Keine Geräte konfiguriert."
        snapshot = self.presence_snapshot()
        if not snapshot or all(value is None for value in snapshot.values()):
            return "Anwesenheit: noch keine frischen Daten empfangen."
        home = [dev_id for dev_id, present in snapshot.items() if present]
        away = [
            device_id
            for device_id, present in snapshot.items()
            if present is not None and not present
        ]
        unknown = [dev_id for dev_id, present in snapshot.items() if present is None]
        parts = []
        if home:
            parts.append(f"Zuhause: {', '.join(home)}")
        if away:
            parts.append(f"Abwesend: {', '.join(away)}")
        if unknown:
            parts.append(f"Unbekannt/veraltet: {', '.join(unknown)}")
        return "\n".join(parts)

    # ── MQTT Loop ─────────────────────────────────────────────────────────────

    async def _mqtt_loop(self) -> None:
        """Main MQTT subscription loop with automatic reconnection."""
        while True:
            try:
                tls_context = ssl.create_default_context() if self._mqtt_tls else None
                async with aiomqtt.Client(
                    self._mqtt_host,
                    self._mqtt_port,
                    username=self._mqtt_username or None,
                    password=self._mqtt_password or None,
                    tls_context=tls_context,
                ) as mqtt:
                    self._connected = True
                    logger.info(
                        "OwnTracks MQTT connected to %s:%d", self._mqtt_host, self._mqtt_port
                    )
                    await mqtt.subscribe(f"{self._topic_prefix}/+/+")
                    async for message in mqtt.messages:
                        await self._on_message(str(message.topic), message.payload)
            except asyncio.CancelledError as cancellation:
                del cancellation
                break
            except Exception as e:
                self._connected = False
                logger.warning("OwnTracks MQTT connection lost: %s — reconnecting in 5s", e)
                await asyncio.sleep(5)
        self._connected = False

    async def _stale_loop(self) -> None:
        """Periodically mark devices as away if no message arrived in time.

        Runs independently of MQTT traffic so a fully-silent broker still triggers
        the stale-timeout safety net. Errors are logged and followed by a backoff
        so a persistent fault can't turn into a tight log-spamming loop.
        """
        while True:
            try:
                await asyncio.sleep(_STALE_CHECK_INTERVAL_SECONDS)
                await self._check_stale()
            except asyncio.CancelledError as cancellation:
                del cancellation
                break
            except Exception as e:
                logger.error("OwnTracks stale check failed: %s", e)
                await asyncio.sleep(_STALE_CHECK_INTERVAL_SECONDS)

    # ── Message Dispatch ──────────────────────────────────────────────────────

    async def _on_message(self, topic: str, payload: bytes) -> None:
        """Parse an incoming MQTT message and dispatch to handler."""
        try:
            data = json.loads(payload)
        except Exception:
            logger.warning("OwnTracks: malformed JSON from %s", topic)
            return

        # OwnTracks publishes to {prefix}/{user}/{device}; for both location and
        # transition payloads the last two segments identify the device. Any prefix
        # depth is fine — we always look at the trailing two segments.
        parts = topic.split("/")
        if len(parts) < 3:
            return

        user_device = f"{parts[-2]}/{parts[-1]}"
        device_id = self._tracked_users.get(user_device)
        if device_id is None:
            return  # Unknown user/device — silently ignore

        self._last_seen[device_id] = datetime.now(timezone.utc)

        msg_type = data.get("_type", "")
        if msg_type == "transition":
            await self._handle_transition(device_id, data)
        elif msg_type == "location":
            await self._handle_location(device_id, data)
        # Other types (waypoint, etc.) are silently ignored

    async def _handle_transition(self, device_id: str, data: dict) -> None:
        """Handle OwnTracks transition event (enter/leave geofence).

        Both enter and leave events are scoped to the configured home region —
        users with multiple geofences (Work, Gym, etc.) must not have those
        regions flip home presence.
        """
        event = data.get("event", "")
        desc = data.get("desc", "")
        if desc != self._home_region:
            return
        if event == "enter":
            await self._update_presence(device_id, True)
        elif event == "leave":
            await self._update_presence(device_id, False)

    async def _handle_location(self, device_id: str, data: dict) -> None:
        """Handle OwnTracks location update — fallback presence via haversine.

        Hysteresis: a device that is currently marked home stays home unless the
        new location is well outside the geofence (radius * _LOCATION_AWAY_HYSTERESIS).
        This prevents GPS jitter from flipping an explicit transition enter event.
        Devices in unknown state get their initial value from the location.
        """
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            return
        dist = _haversine(lat, lon, self._home_lat, self._home_lon)
        current = self._presence.get(device_id)
        if current is None:
            # First location update — set initial state from raw distance.
            await self._update_presence(device_id, dist < self._geofence_radius_m)
        elif current:
            # Already home — only flip away if clearly outside the radius.
            if dist > self._geofence_radius_m * _LOCATION_AWAY_HYSTERESIS:
                await self._update_presence(device_id, False)
        else:
            # Already away — flip home as soon as we're inside the radius.
            if dist < self._geofence_radius_m:
                await self._update_presence(device_id, True)

    async def _check_stale(self) -> None:
        """Log stale devices; freshness is represented as unknown, never as away."""
        now = datetime.now(timezone.utc)
        for device_id in list(self._tracked_users.values()):
            last = self._last_seen.get(device_id)
            if last is not None:
                delta_minutes = (now - last).total_seconds() / 60
                if delta_minutes > self._stale_timeout_minutes:
                    logger.warning(
                        "OwnTracks: %s stale (%.0f min), treating presence as unknown",
                        device_id,
                        delta_minutes,
                    )

    # ── State Mutation ────────────────────────────────────────────────────────

    async def _update_presence(self, device_id: str, is_home: bool) -> None:
        """Update presence state and fire change callbacks if state changed."""
        was_home = self._presence.get(device_id)
        self._presence[device_id] = is_home
        if was_home != is_home:
            action = "Zuhause" if is_home else "Abwesend"
            logger.info("OwnTracks: %s ist jetzt %s", device_id, action)
            await self._fire_change(device_id, is_home)

    async def _fire_change(self, device_id: str, is_home: bool) -> None:
        """Fire registered callbacks when presence changes."""
        for cb in self._change_callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb(device_id, is_home)
                else:
                    cb(device_id, is_home)
            except Exception as e:
                logger.error("OwnTracks presence callback error: %s", e)
