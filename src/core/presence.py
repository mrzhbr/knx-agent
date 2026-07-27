from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _is_hal(obj: Any) -> bool:
    """Return True if obj is a HAL instance (has get_devices_by_type)."""
    return obj is not None and hasattr(obj, "get_devices_by_type")


class PresenceManager:
    """Fuses HAL presence sensors + window sensors + camera zone data.

    Three data sources:
    1. HAL PRESENCE_SENSOR devices: who is home (by IP ping via PresencePlatform)
    2. Window sensors: open windows/doors (cached from HAL StateStore)
    3. Camera zones: recent entrance activity (with 5-minute expiry)

    Also exposes open_windows as a list[str] for guardrail use.
    Window cache is populated via connect_hal() which must be called AFTER
    populate_initial_state() completes.

    Constructor accepts:
        hal: HAL instance (new API — used when PresencePlatform is wired)
        arp_presence: ARPPresenceAdapter (legacy fallback — kept for backward compat)

    If only a positional arg is given (old tests: PresenceManager(arp)), it binds
    to the first keyword parameter. The code detects this via _is_hal().
    """

    def __init__(
        self,
        hal: Any = None,
        arp_presence: Any = None,
    ) -> None:
        # hal= may actually be an ARPPresenceAdapter in old tests (positional arg compat)
        if _is_hal(hal):
            self._hal: Any = hal
            self._arp: Any = arp_presence
        else:
            # Old usage: PresenceManager(arp_adapter)
            self._hal = None
            self._arp = hal if hal is not None else arp_presence

        # Window state is tracked per device so one closing sensor cannot erase
        # another open sensor in the same room. None means unknown/failed read.
        self._open_windows: list[str] = []  # legacy compatibility/display cache
        self._window_states: dict[str, bool | None] = {}
        self._window_labels: dict[str, str] = {}
        # Camera zone tracking: camera -> {zones: list[str], timestamp: datetime}
        self._camera_zones: dict[str, dict] = {}
        self._camera_zone_expiry = timedelta(minutes=5)

    async def connect_hal(self, hal: Any | None = None) -> None:
        """Sync window cache from StateStore and register ongoing listener.

        Must be called AFTER hal.populate_initial_state() so StateStore is warm.

        Args:
            hal: HAL instance (optional if already passed to __init__)
        """
        if hal is not None and _is_hal(hal):
            self._hal = hal
        if self._hal is None:
            return

        from src.hal.models import SensorClass

        # Step 1: One-time sync of current window state from StateStore
        self._open_windows = []
        self._window_states = {}
        self._window_labels = {}
        sensors = self._hal.get_sensors_by_class(SensorClass.WINDOW)
        for adapter in sensors:
            device_key = adapter.info.id.key
            self._window_labels[device_key] = f"{adapter.info.name} ({adapter.info.room})"
            try:
                state = await adapter.read_state()
                val = state.state.get("Binary", state.state.get("is_on"))
                self._window_states[device_key] = (
                    None if val is None else str(val).lower() in {"1", "true"}
                )
            except Exception as exc:
                logger.warning("connect_hal: failed to read %s — %s", adapter.info.name, exc)
                self._window_states[device_key] = None
        self._refresh_open_windows()

        # Step 2: Register StateStore listener for ongoing updates
        self._hal.state_store.on_change(self._on_window_state_change)
        logger.info(
            "PresenceManager: HAL connected, %d open windows found", len(self._open_windows)
        )

    async def _on_window_state_change(
        self,
        info: Any,
        old_state: dict | None,
        new_state: dict,
        source: str,
    ) -> None:
        """StateStore listener: updates _open_windows cache for window sensors."""
        from src.hal.models import SensorClass

        if info.sensor_class != SensorClass.WINDOW:
            return

        device_key = info.id.key
        self._window_labels[device_key] = f"{info.name} ({info.room})"
        raw_value = new_state.get("Binary", new_state.get("is_on"))
        self._window_states[device_key] = (
            None if raw_value is None else str(raw_value).lower() in {"1", "true"}
        )
        self._refresh_open_windows()

    def _refresh_open_windows(self) -> None:
        self._open_windows = [
            self._window_labels.get(device_key, device_key)
            for device_key, is_open in self._window_states.items()
            if is_open
        ]

    def nobody_home(self) -> bool:
        """True if no tracked presence sensor reports is_home=True.

        Sync implementation — delegates to legacy ARPPresenceAdapter if available.
        For HAL-based presence, use async_nobody_home() instead.
        """
        # Legacy path (sync-compatible)
        if self._arp is not None and hasattr(self._arp, "nobody_home"):
            return self._arp.nobody_home()

        # HAL path: can't be called synchronously — return safe default
        # Guardrails should prefer the async path when available.
        return False

    @property
    def open_windows(self) -> list[str]:
        """List of individual open windows/doors for user output and guardrails."""
        return list(self._open_windows)

    @property
    def window_state_known(self) -> bool:
        """Whether every configured window sensor has a known state."""
        return bool(self._window_states) and all(
            state is not None for state in self._window_states.values()
        )

    @property
    def windows_safe_for_alarm(self) -> bool:
        return self.window_state_known and not any(self._window_states.values())

    def has_open_windows(self) -> list[str]:
        """Returns list of room display names with open windows/doors."""
        return list(self._open_windows)

    def who_is_home(self) -> dict[str, bool]:
        """Return {device_name: is_home} for tracked presence devices (sync)."""
        if self._arp is not None and hasattr(self._arp, "who_is_home"):
            return self._arp.who_is_home()
        return {}

    async def async_who_is_home(self) -> dict[str, bool]:
        """Async version: return {device_name: is_home} for all presence sensors."""
        from src.hal.models import DeviceType

        result: dict[str, bool] = {}
        if self._hal is not None:
            read_failed = False
            presence_devices = self._hal.get_devices_by_type(DeviceType.PRESENCE_SENSOR)
            for adapter in presence_devices:
                try:
                    state = await adapter.read_state()
                    raw_value = state.state.get("is_home")
                    is_fresh = state.state.get("is_fresh", raw_value is not None)
                    if raw_value is None or not is_fresh:
                        read_failed = True
                        continue
                    result[adapter.info.name] = str(raw_value).lower() in {"true", "1"}
                except Exception as exc:
                    logger.warning("async_who_is_home: %s — %s", adapter.info.name, exc)
                    read_failed = True
            if read_failed:
                return {}  # Unknown presence must fail closed for security decisions.
            if result:
                return result

        if self._arp is not None and hasattr(self._arp, "who_is_home"):
            return self._arp.who_is_home()
        return {}

    async def async_nobody_home(self) -> bool:
        """Async version: True if no presence sensor reports is_home=True."""
        who = await self.async_who_is_home()
        if not who:
            return False  # No devices → assume home (safe fallback)
        return not any(who.values())

    def update_camera_zone(self, camera: str, zones: list[str]) -> None:
        """Update camera zone activity.

        Called by StateStore listener (_on_camera_state_for_presence) in main.py.
        Zone data expires after 5 minutes.
        """
        self._camera_zones[camera] = {
            "zones": zones,
            "timestamp": datetime.now(),
        }
        logger.debug("Camera zone updated: camera=%s zones=%s", camera, zones)

    def get_entrance_activity(self) -> list[str]:
        """Return cameras with recent person detections (not yet expired)."""
        now = datetime.now()
        active = []
        for camera, data in self._camera_zones.items():
            age = now - data["timestamp"]
            if age < self._camera_zone_expiry:
                active.append(camera)
        return active

    def format_for_prompt(self) -> str:
        """German-language presence + window + entrance summary."""
        parts: list[str] = []

        # Presence summary
        if self._arp is not None and hasattr(self._arp, "format_for_prompt"):
            parts.append(self._arp.format_for_prompt())
        elif self._hal is not None:
            parts.append("Anwesenheit: Abfrage via HAL (async erforderlich)")

        # Window/door sensors
        open_windows = self.has_open_windows()
        if open_windows:
            parts.append(f"Offene Fenster/Türen: {', '.join(open_windows)}")
        else:
            if self._hal:
                parts.append("Alle Fenster/Türen geschlossen.")

        # Camera entrance activity
        active_cameras = self.get_entrance_activity()
        if active_cameras:
            cameras_str = ", ".join(active_cameras)
            parts.append(f"Kamera-Aktivität (letzte 5 Min.): {cameras_str}")

        return "\n".join(parts)
