"""MitsubishiPlatform — HAL Platform adapter for Mitsubishi AC units.

Wraps MitsubishiACAdapter and converts AC state changes into DeviceState
updates for HAL's StateStore pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass
from src.hal.platform import Platform

if TYPE_CHECKING:
    from src.config import BrainConfig, MitsubishiACConfig

logger = logging.getLogger(__name__)


class MitsubishiPlatform(Platform):
    """HAL Platform that wraps MitsubishiACAdapter for climate control.

    Each AC unit becomes a CLIMATE device. Poll-loop state changes are
    detected by comparing adapter.get_status() snapshots.
    """

    _name: str = "mitsubishi_ac"

    def __init__(self, config: "MitsubishiACConfig") -> None:
        self._config = config
        self._adapter: Any = None
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None
        self._name_to_room: dict[str, str] = {}

    @classmethod
    def create_from_config(cls, config: "BrainConfig") -> "MitsubishiPlatform | None":
        """Create a MitsubishiPlatform from the application config.

        Always returns an instance.
        """
        return cls(config=config.mitsubishi_ac)

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Mitsubishi AC"

    @property
    def error_message(self) -> str:
        return "⚠ Mitsubishi AC ist offline."

    async def start(self) -> None:
        """Create and start the underlying MitsubishiACAdapter with stub awareness."""
        from src.integrations.mitsubishi_ac import MitsubishiACAdapter

        self._adapter = MitsubishiACAdapter(
            config=self._config,
        )
        await self._adapter.start()
        logger.info("MitsubishiPlatform started: %d units", len(self._config.units))

    async def stop(self) -> None:
        if self._adapter:
            await self._adapter.stop()
        logger.info("MitsubishiPlatform stopped")

    async def health_check(self) -> bool:
        if self._adapter is None:
            return False
        return await self._adapter.health_check()

    async def discover_devices(self) -> list[DeviceInfo]:
        """Return one CLIMATE DeviceInfo per configured AC unit."""
        devices: list[DeviceInfo] = []
        for unit_cfg in self._config.units:
            device_id = DeviceId(platform=self._name, native_id=unit_cfg.name)
            info = DeviceInfo(
                id=device_id,
                name=unit_cfg.name,
                device_type=DeviceType.CLIMATE,
                room=unit_cfg.room,
                sensor_class=SensorClass.TEMPERATURE,
                capabilities={"set_temperature", "set_mode", "turn_on", "turn_off"},
                platform_data={"ip": unit_cfg.ip},
            )
            self._name_to_room[unit_cfg.name] = unit_cfg.room
            devices.append(info)
        return devices

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Return current AC state from the adapter's cached status."""
        room = self._name_to_room.get(device_id.native_id, device_id.native_id)
        raw_state: dict = {}
        if self._adapter is not None:
            status = self._adapter.get_status(room)
            raw_state = status.get(room, {})

        power_raw = str(raw_state.get("power", "off"))
        mode_raw = str(raw_state.get("mode", "auto"))
        # Adapter uses set_temp / room_temp — we expose as temperature / room_temperature
        set_temp = raw_state.get("set_temp", None)
        room_temp = raw_state.get("room_temp", None)
        power_watt = raw_state.get("power_watt", 0)

        is_on = "PowerOnOff.ON" in power_raw or power_raw.lower() in ("on", "1", "true")
        power_str = "on" if is_on else "off"

        return DeviceState(
            device_id=device_id,
            state={
                "power": power_str,
                "mode": mode_raw,
                "temperature": set_temp,
                "room_temperature": room_temp,
                "power_watt": power_watt,
                "PowerOnOff": power_raw,
            },
            last_updated=datetime.now(),
            source="mitsubishi_ac",
        )

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Store the HAL event callback.

        The MitsubishiACAdapter does not support callbacks — state changes
        are detected via the _NoOpAwareness stub. For now, we store the
        callback for future use. A separate polling wrapper could emit
        DeviceState changes by comparing snapshots; that is not wired here
        because the adapter's internal poll loop already calls _detect_changes().
        """
        self._event_callback = callback

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        """Execute AC commands: turn_on, turn_off, set_temperature, set_mode."""
        if self._adapter is None:
            raise RuntimeError("MitsubishiPlatform not started")

        room = self._name_to_room.get(device_id.native_id, device_id.native_id)
        if command == "turn_on":
            await self._adapter.set_power(room, True)
        elif command == "turn_off":
            await self._adapter.set_power(room, False)
        elif command == "set_temperature":
            value = params.get("value")
            if value is None:
                raise ValueError("set_temperature requires 'value' parameter")
            await self._adapter.set_temperature(room, float(value))
        elif command == "set_mode":
            mode = params.get("mode")
            if mode is None:
                raise ValueError("set_mode requires 'mode' parameter")
            await self._adapter.set_mode(room, str(mode))
        else:
            raise ValueError(f"Unknown command '{command}' for MitsubishiPlatform")
