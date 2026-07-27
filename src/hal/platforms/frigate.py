"""FrigatePlatform — HAL Platform adapter for Frigate NVR.

Wraps the existing FrigateAdapter (MQTT + REST client) and converts
detection events into DeviceState updates for HAL's StateStore pipeline.

Configured cameras are modelled as CAMERA devices. Detection events become
state changes on the appropriate camera device.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass
from src.hal.platform import Platform

if TYPE_CHECKING:
    from src.config import BrainConfig

logger = logging.getLogger(__name__)


class FrigatePlatform(Platform):
    """HAL Platform that wraps FrigateAdapter for configured cameras.

    Detection events from Frigate MQTT are converted into DeviceState
    updates on the camera device. The internal FrigateAdapter is not
    exposed — callers use get_snapshot() and get_events() instead.
    """

    _name: str = "frigate"

    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        frigate_host: str,
        frigate_port: int,
        camera_rooms: dict[str, str] | None = None,
        entrance_cameras: list[str] | None = None,
        mqtt_username: str = "",
        mqtt_password: str = "",
        mqtt_tls: bool = False,
    ) -> None:
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._frigate_host = frigate_host
        self._frigate_port = frigate_port
        self._mqtt_username = mqtt_username
        self._mqtt_password = mqtt_password
        self._mqtt_tls = mqtt_tls
        self._camera_rooms = dict(camera_rooms or {})
        self._entrance_cameras = list(entrance_cameras or [])
        self._adapter: Any = None
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None
        # Internal cache: camera_name -> latest detection state dict
        self._detection_cache: dict[str, dict] = {}

    @classmethod
    def create_from_config(cls, config: "BrainConfig") -> "FrigatePlatform | None":
        """Create a FrigatePlatform from deployment configuration."""
        return cls(
            mqtt_host=config.mqtt.host,
            mqtt_port=config.mqtt.port,
            frigate_host=config.frigate.host,
            frigate_port=config.frigate.port,
            camera_rooms=config.frigate.camera_rooms,
            entrance_cameras=config.frigate.entrance_cameras,
            mqtt_username=config.mqtt.username,
            mqtt_password=config.mqtt.password,
            mqtt_tls=config.mqtt.tls,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Frigate"

    @property
    def error_message(self) -> str:
        return "⚠ Frigate ist offline — keine Kameradaten verfügbar."

    async def start(self) -> None:
        """Create and start the underlying FrigateAdapter."""
        from src.integrations.frigate import FrigateAdapter

        self._adapter = FrigateAdapter(
            mqtt_host=self._mqtt_host,
            mqtt_port=self._mqtt_port,
            frigate_host=self._frigate_host,
            frigate_port=self._frigate_port,
            gira_x1=None,
            event_handler=self._on_detection,
            mqtt_username=self._mqtt_username,
            mqtt_password=self._mqtt_password,
            mqtt_tls=self._mqtt_tls,
            entrance_cameras=self._entrance_cameras,
        )
        await self._adapter.start()
        logger.info("FrigatePlatform started: %d cameras", len(self._camera_rooms))

    async def stop(self) -> None:
        if self._adapter:
            await self._adapter.stop()
        logger.info("FrigatePlatform stopped")

    async def health_check(self) -> bool:
        if self._adapter is None:
            return False
        return await self._adapter.health_check()

    async def discover_devices(self) -> list[DeviceInfo]:
        """Return one DeviceInfo object per configured camera."""
        devices: list[DeviceInfo] = []
        for camera_name, room in self._camera_rooms.items():
            device_id = DeviceId(platform=self._name, native_id=camera_name)
            info = DeviceInfo(
                id=device_id,
                name=camera_name.replace("_", " ").title(),
                device_type=DeviceType.CAMERA,
                room=room,
                sensor_class=SensorClass.MOTION,
                platform_data={"camera_name": camera_name},
            )
            devices.append(info)
        return devices

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Return DeviceState from internal detection cache for the given camera."""
        camera_name = device_id.native_id
        cached = self._detection_cache.get(camera_name, {})
        return DeviceState(
            device_id=device_id,
            state={
                "last_detection": cached.get("last_detection", "none"),
                "confidence": cached.get("confidence", 0.0),
                "zones": cached.get("zones", ""),
                "last_event_time": cached.get("last_event_time", ""),
                "is_detecting": cached.get("is_detecting", "false"),
            },
            last_updated=datetime.now(),
            source="frigate",
        )

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Store the HAL event callback. Detection events will call it."""
        self._event_callback = callback

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        raise NotImplementedError("Platform 'frigate' is read-only")

    # ── Public API for tools ──────────────────────────────────────────────

    async def get_snapshot(self, camera: str) -> bytes:
        """Fetch latest JPEG snapshot from Frigate for the given camera."""
        if self._adapter is None:
            raise RuntimeError("FrigatePlatform not started")
        return await self._adapter.get_snapshot(camera)

    async def get_events(self, hours: int = 24) -> list[dict]:
        """Query Frigate event history (REST API)."""
        if self._adapter is None:
            return []
        return await self._adapter.get_events(hours=hours)

    # ── Internal ──────────────────────────────────────────────────────────

    async def _on_detection(self, detection: Any) -> None:
        """Called by FrigateAdapter for every new detection event."""
        camera_name = getattr(detection, "camera", "unknown")
        label = getattr(detection, "label", "none")
        confidence = getattr(detection, "confidence", 0.0)
        zones = getattr(detection, "zones", [])
        timestamp = getattr(detection, "timestamp", datetime.now())

        if isinstance(timestamp, datetime):
            ts_str = timestamp.isoformat()
        else:
            ts_str = str(timestamp)

        zones_str = ",".join(zones) if zones else ""

        # Update detection cache
        self._detection_cache[camera_name] = {
            "last_detection": label,
            "confidence": confidence,
            "zones": zones_str,
            "last_event_time": ts_str,
            "is_detecting": "true",
        }

        # Build DeviceId for this camera
        device_id = DeviceId(platform=self._name, native_id=camera_name)

        state = DeviceState(
            device_id=device_id,
            state={
                "last_detection": label,
                "confidence": confidence,
                "zones": zones_str,
                "last_event_time": ts_str,
                "is_detecting": "true",
            },
            last_updated=datetime.now(),
            source="frigate",
        )

        if self._event_callback:
            try:
                await self._event_callback(state)
            except Exception as e:
                logger.error("FrigatePlatform event callback error: %s", e)
