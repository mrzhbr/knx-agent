"""PresencePlatform — HAL Platform adapter for presence detection.

Supports two backends:
- OwnTracksAdapter (default, backend="owntracks"): MQTT geofence events
- ARPPresenceAdapter (legacy, backend="arp"): ARP table polling

Each tracked phone/device becomes a PRESENCE_SENSOR device.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass
from src.hal.platform import Platform

if TYPE_CHECKING:
    from src.config import BrainConfig, HomeConfig, MQTTConfig, PresenceConfig

logger = logging.getLogger(__name__)


class PresencePlatform(Platform):
    """HAL Platform that wraps a presence adapter (OwnTracks or ARP).

    Each tracked device (phone, etc.) is exposed as a PRESENCE_SENSOR device.
    Presence change events are converted into DeviceState updates.
    """

    _name: str = "presence"

    def __init__(
        self,
        config: "PresenceConfig",
        mqtt_config: "MQTTConfig | None" = None,
        home_config: "HomeConfig | None" = None,
    ) -> None:
        self._config = config
        self._mqtt_config = mqtt_config
        self._home_config = home_config
        self._adapter: Any = None
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None

    @classmethod
    def create_from_config(cls, config: "BrainConfig") -> "PresencePlatform | None":
        """Create a PresencePlatform from the application config.

        Always returns an instance.
        """
        return cls(
            config=config.presence,
            mqtt_config=config.mqtt,
            home_config=config.home,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Anwesenheitserkennung"

    @property
    def error_message(self) -> str:
        return "⚠ Anwesenheitserkennung ist offline — Anwesenheit nicht erkannt."

    async def start(self) -> None:
        """Create and start the backend presence adapter."""
        backend = getattr(self._config, "backend", "arp")
        if backend == "owntracks":
            await self._start_owntracks()
        elif backend == "arp":
            await self._start_arp()
        else:
            raise ValueError(
                f"Unsupported presence backend: {backend!r}. Allowed values: 'owntracks', 'arp'."
            )

        self._adapter.on_change(self._on_presence_change)
        await self._adapter.start()
        tracked_count = (
            len(self._config.owntracks_tracked_users)
            if backend == "owntracks"
            else len(self._config.tracked_devices)
        )
        logger.info(
            "PresencePlatform started (backend=%s): tracking %d devices",
            backend,
            tracked_count,
        )

    async def _start_owntracks(self) -> None:
        """Instantiate OwnTracksAdapter from config.

        Requires both ``mqtt_config`` and ``home_config`` — silently falling back
        to defaults would mis-locate the geofence and report every device as away.
        """
        from src.integrations.owntracks import OwnTracksAdapter

        if self._mqtt_config is None:
            raise ValueError(
                "OwnTracks backend requires mqtt_config (broker host/port). "
                "Configure 'mqtt' in config.yaml or switch backend to 'arp'."
            )
        if self._home_config is None:
            raise ValueError(
                "OwnTracks backend requires home_config (latitude/longitude). "
                "Configure 'home' in config.yaml or switch backend to 'arp'."
            )

        if not self._config.owntracks_tracked_users:
            raise ValueError(
                "OwnTracks backend requires owntracks_tracked_users to be "
                "populated (mapping 'user/device' → internal device id). "
                "See docs/OWNTRACKS.md."
            )

        # Zero coordinates are explicit placeholders and must never be treated
        # as a real home geofence; fail closed rather than reporting people away.
        if self._home_config.latitude == 0.0 and self._home_config.longitude == 0.0:
            raise ValueError(
                "OwnTracks backend requires non-placeholder home coordinates. "
                "Set latitude/longitude in config.yaml under 'home'."
            )

        self._adapter = OwnTracksAdapter(
            mqtt_host=self._mqtt_config.host,
            mqtt_port=self._mqtt_config.port,
            topic_prefix=self._config.owntracks_topic_prefix,
            home_lat=self._home_config.latitude,
            home_lon=self._home_config.longitude,
            geofence_radius_m=self._config.owntracks_geofence_radius_m,
            tracked_users=self._config.owntracks_tracked_users,
            stale_timeout_minutes=self._config.owntracks_stale_timeout_minutes,
            home_region=self._config.owntracks_home_region,
            mqtt_username=self._mqtt_config.username,
            mqtt_password=self._mqtt_config.password,
            mqtt_tls=self._mqtt_config.tls,
        )

    async def _start_arp(self) -> None:
        """Instantiate ARPPresenceAdapter from config."""
        from src.integrations.arp_presence import ARPPresenceAdapter

        self._adapter = ARPPresenceAdapter(config=self._config)

    async def stop(self) -> None:
        if self._adapter:
            await self._adapter.stop()
        logger.info("PresencePlatform stopped")

    async def health_check(self) -> bool:
        """Delegate health check to the underlying adapter."""
        if self._adapter is None:
            return False
        return await self._adapter.health_check()

    async def discover_devices(self) -> list[DeviceInfo]:
        """Return one PRESENCE_SENSOR DeviceInfo per tracked device.

        Source of truth depends on the backend:
        - owntracks: ``owntracks_tracked_users`` — values are the internal device
          ids. Display names are taken from ``tracked_devices[dev_id].name`` if
          available, otherwise the id is reused as the name.
        - arp: ``tracked_devices`` — the legacy dict keyed by internal id.
        """
        backend = getattr(self._config, "backend", "arp")
        devices: list[DeviceInfo] = []

        if backend == "owntracks":
            for dev_id in self._config.owntracks_tracked_users.values():
                device_id = DeviceId(platform=self._name, native_id=dev_id)
                tracked = self._config.tracked_devices.get(dev_id)
                name = tracked.name if tracked is not None else dev_id
                info = DeviceInfo(
                    id=device_id,
                    name=name,
                    device_type=DeviceType.PRESENCE_SENSOR,
                    room="Haus",
                    sensor_class=SensorClass.PRESENCE,
                    platform_data={"backend": backend},
                )
                devices.append(info)
            return devices

        for dev_id, tracked in self._config.tracked_devices.items():
            device_id = DeviceId(platform=self._name, native_id=dev_id)
            info = DeviceInfo(
                id=device_id,
                name=tracked.name,
                device_type=DeviceType.PRESENCE_SENSOR,
                room="Haus",
                sensor_class=SensorClass.PRESENCE,
                platform_data={"mac": getattr(tracked, "mac", ""), "ip": tracked.ip},
            )
            devices.append(info)
        return devices

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Return current presence state for the given device."""
        is_home: bool | None = None
        if self._adapter is not None:
            if hasattr(self._adapter, "presence_snapshot"):
                presence = self._adapter.presence_snapshot()
            else:
                presence = self._adapter.who_is_home()
            is_home = presence.get(device_id.native_id)

        return DeviceState(
            device_id=device_id,
            state={"is_home": is_home, "is_fresh": is_home is not None},
            last_updated=datetime.now(),
            source="presence",
        )

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Store the HAL event callback."""
        self._event_callback = callback

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        raise NotImplementedError("Platform 'presence' is read-only")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _on_presence_change(self, device_id_str: str, is_home: bool) -> None:
        """Called by the presence adapter when a tracked device changes state."""
        device_id = DeviceId(platform=self._name, native_id=device_id_str)

        state = DeviceState(
            device_id=device_id,
            state={"is_home": is_home},
            last_updated=datetime.now(),
            source="presence",
        )

        if self._event_callback:
            try:
                await self._event_callback(state)
            except Exception as e:
                logger.error("PresencePlatform event callback error: %s", e)
