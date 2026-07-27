"""DoorStationPlatform — HAL Platform adapter for Mobotix T24M door station.

Wraps DoorStationAdapter (aiohttp webhook listener) and converts bell
press events into DeviceState updates for HAL's StateStore pipeline.

One DOOR_STATION device is exposed: native_id="door_station_main".
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

    pass

logger = logging.getLogger(__name__)

_DOOR_STATION_ID = "door_station_main"


class DoorStationPlatform(Platform):
    """HAL Platform for the Mobotix T24M door station.

    Bell press events from the webhook are converted into DeviceState
    updates (last_bell timestamp changes). The open_door command is
    forwarded to the adapter's trigger_door_relay().
    """

    _name: str = "door_station"

    def __init__(
        self,
        host: str = "",
        password: str = "",
        webhook_port: int = 8103,
        webhook_host: str = "127.0.0.1",
        webhook_token: str = "",
        bell_camera: str = "",
    ) -> None:
        self._host = host
        self._password = password
        self._webhook_port = webhook_port
        self._webhook_host = webhook_host
        self._webhook_token = webhook_token
        self._adapter: Any = None
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None
        self._last_bell: str = ""
        self._last_bell_camera = bell_camera

    @classmethod
    def create_from_config(cls, config: "BrainConfig") -> "DoorStationPlatform | None":
        """Create a DoorStationPlatform from the application config.

        Always returns an instance.
        """
        return cls(
            host=config.home.door_station_host,
            password=config.home.mobotix_password,
            webhook_host=config.home.door_station_webhook_host,
            webhook_token=config.home.door_station_webhook_token,
            bell_camera=config.home.door_station_camera,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Türstation"

    @property
    def error_message(self) -> str:
        return "⚠ Türstation ist offline."

    async def start(self) -> None:
        """Create and start DoorStationAdapter webhook listener."""
        from src.integrations.door_station import DoorStationAdapter

        self._adapter = DoorStationAdapter(
            host=self._host,
            password=self._password,
            webhook_port=self._webhook_port,
            webhook_host=self._webhook_host,
            webhook_token=self._webhook_token,
            bell_handler=self._on_bell,
        )
        await self._adapter.start()
        logger.info("DoorStationPlatform started (port=%d)", self._webhook_port)

    async def stop(self) -> None:
        if self._adapter:
            await self._adapter.stop()
        logger.info("DoorStationPlatform stopped")

    async def health_check(self) -> bool:
        if self._adapter is None:
            return False
        return await self._adapter.health_check()

    async def discover_devices(self) -> list[DeviceInfo]:
        """Return a single DOOR_STATION device."""
        device_id = DeviceId(platform=self._name, native_id=_DOOR_STATION_ID)
        info = DeviceInfo(
            id=device_id,
            name="Haustür",
            device_type=DeviceType.DOOR_STATION,
            room="Eingang",
            sensor_class=SensorClass.DOOR,
            capabilities={"open_door"},
            platform_data={},
        )
        return [info]

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Return last bell timestamp state."""
        return DeviceState(
            device_id=device_id,
            state={
                "last_bell": self._last_bell,
                "last_bell_camera": self._last_bell_camera,
            },
            last_updated=datetime.now(),
            source="door_station",
        )

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Store the HAL event callback."""
        self._event_callback = callback

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        """Execute door station commands: open_door."""
        if self._adapter is None:
            raise RuntimeError("DoorStationPlatform not started")

        if command == "open_door":
            succeeded = await self._adapter.trigger_door_relay()
            if not succeeded:
                raise RuntimeError("Door relay rejected the command")
        else:
            raise ValueError(f"Unknown command '{command}' for DoorStationPlatform")

    # ── Internal ──────────────────────────────────────────────────────────

    async def _on_bell(self) -> None:
        """Called by DoorStationAdapter when a bell press is received."""
        self._last_bell = datetime.now().isoformat()

        logger.info("DoorStationPlatform: bell press at %s", self._last_bell)

        device_id = DeviceId(platform=self._name, native_id=_DOOR_STATION_ID)
        state = DeviceState(
            device_id=device_id,
            state={
                "last_bell": self._last_bell,
                "last_bell_camera": self._last_bell_camera,
            },
            last_updated=datetime.now(),
            source="door_station",
        )

        if self._event_callback:
            try:
                await self._event_callback(state)
            except Exception as e:
                logger.error("DoorStationPlatform event callback error: %s", e)
