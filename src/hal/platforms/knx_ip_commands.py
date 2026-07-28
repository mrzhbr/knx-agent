"""Pure HAL-command planning for native KNX devices."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.hal.models import DeviceType
from src.hal.platforms.knx_ip_registry import KNXMappedDevice  # type: ignore[import-not-found]
from src.hal.platforms.knx_ip_values import (  # type: ignore[import-not-found]
    binary_value,
    brightness,
    temperature,
)


@dataclass(frozen=True, slots=True)
class KNXWriteIntent:
    role: str
    value: object


DPTResolver = Callable[[KNXMappedDevice, str], str | None]
_LIGHT_TYPES = {DeviceType.LIGHT, DeviceType.SWITCH, DeviceType.DIMMABLE_LIGHT}
_SWITCHABLE_TYPES = _LIGHT_TYPES | {DeviceType.HEATING}


def plan_command(
    device: KNXMappedDevice,
    command: str,
    params: Mapping[str, Any],
    dpt_for: DPTResolver,
) -> tuple[KNXWriteIntent, ...]:
    """Validate a complete command before returning any physical write intents."""
    if command == "turn_on" and device.device_type in _SWITCHABLE_TYPES:
        if "switch" not in device.addresses:
            raise ValueError(f"KNX device {device.name!r} has no writable switch role")
        writes = [KNXWriteIntent("switch", True)]
        if "brightness" in params:
            if "brightness" not in device.addresses:
                raise ValueError(f"KNX device {device.name!r} has no writable brightness role")
            writes.append(KNXWriteIntent("brightness", brightness(params["brightness"])))
        return tuple(writes)

    if command == "turn_off" and device.device_type in _SWITCHABLE_TYPES:
        if "switch" not in device.addresses:
            raise ValueError(f"KNX device {device.name!r} has no writable switch role")
        return (KNXWriteIntent("switch", False),)

    if command == "set_brightness" and device.device_type is DeviceType.DIMMABLE_LIGHT:
        return (KNXWriteIntent("brightness", brightness(params.get("value"))),)

    if device.device_type is DeviceType.BLIND:
        blind_commands = {
            "open": KNXWriteIntent("move", False),
            "close": KNXWriteIntent("move", True),
            "stop": KNXWriteIntent("stop", True),
        }
        if command in blind_commands:
            return (blind_commands[command],)

    if command == "set_temperature" and device.device_type is DeviceType.HEATING:
        temperature_value = params.get("value")
        if not isinstance(temperature_value, (int, float)) or isinstance(temperature_value, bool):
            raise ValueError("set_temperature requires a numeric value")
        return (KNXWriteIntent("setpoint", temperature(temperature_value)),)

    if command == "trigger" and device.device_type is DeviceType.SCENE:
        if device.scene_number is None:
            raise ValueError(f"Scene {device.name!r} has no scene_number")
        scene_value: object = device.scene_number
        if dpt_for(device, "scene") == "18.001":
            scene_value = {"scene_number": device.scene_number, "learn": False}
        return (KNXWriteIntent("scene", scene_value),)

    if command == "trigger" and device.device_type is DeviceType.TRIGGER:
        return (KNXWriteIntent("trigger", True),)

    if command == "set_binary" and device.device_type is DeviceType.SWITCH:
        return (KNXWriteIntent("switch", binary_value(params.get("value"))),)

    raise ValueError(f"Unsupported command {command!r} for KNX device {device.name!r}")
