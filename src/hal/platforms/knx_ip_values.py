"""Canonical HAL role names and value normalization for native KNX devices."""

from __future__ import annotations

import math

from src.hal.models import DeviceType
from src.hal.platforms.knx_ip_registry import KNXMappedDevice  # type: ignore[import-not-found]

_ROLE_STATE_KEYS = {
    "switch": "OnOff",
    "switch_state": "OnOff",
    "brightness": "Brightness",
    "brightness_state": "Brightness",
    "move": "Up-Down",
    "move_state": "Up-Down",
    "stop": "Step-Up-Down",
    "position": "Position",
    "position_state": "Position",
    "setpoint": "Set-Point",
    "setpoint_state": "Set-Point",
    "value_state": "Binary",
    "scene": "Scene",
    "scene_state": "Scene",
    "trigger": "Trigger",
    "trigger_state": "Trigger",
}


def state_key(device: KNXMappedDevice, role: str) -> str:
    if role == "temperature_state":
        if device.device_type is DeviceType.TEMPERATURE_SENSOR:
            return "temperature"
        return "Current"
    return _ROLE_STATE_KEYS[role]


def brightness(raw: object) -> int:
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise ValueError("brightness requires a numeric value from 0 to 100")
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("brightness requires a finite value from 0 to 100") from exc
    if not 0 <= value <= 100:
        raise ValueError("brightness must be between 0 and 100")
    return value


def binary_value(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in {0, 1}:
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().casefold()
        if normalized in {"0", "false", "off"}:
            return False
        if normalized in {"1", "true", "on"}:
            return True
    raise ValueError("binary value must be an explicit true/false or 1/0 value")


def temperature(raw: int | float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("set_temperature requires a finite numeric value") from exc
    if not math.isfinite(value):
        raise ValueError("set_temperature requires a finite numeric value")
    return value


def normalize_state_value(key: str, value: object) -> str | float | bool | None:
    if value is None:
        return None
    if key == "Up-Down" and isinstance(value, (bool, int)):
        return "1" if bool(value) else "0"
    if isinstance(value, (str, float, bool)):
        return value
    if isinstance(value, int):
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return None
    return str(value)
