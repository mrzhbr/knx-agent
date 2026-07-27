from __future__ import annotations

from .models import DeviceId, DeviceInfo, DeviceState, DeviceType
from .platform import Platform


class DeviceAdapter:
    """Base class for all device adapters. Holds DeviceInfo and a reference to Platform."""

    def __init__(self, info: DeviceInfo, platform: Platform) -> None:
        self.info = info
        self.platform = platform

    @property
    def device_id(self) -> DeviceId:
        return self.info.id

    async def read_state(self) -> DeviceState:
        """Delegate to platform.read_state()."""
        return await self.platform.read_state(self.device_id)


class LightAdapter(DeviceAdapter):
    """Adapter for light and dimmable light devices."""

    async def turn_on(self, brightness: int | None = None) -> None:
        """Turn on the light, optionally at a specific brightness."""
        params: dict = {}
        if brightness is not None:
            params["brightness"] = brightness
        await self.platform.send_command(self.device_id, "turn_on", **params)

    async def turn_off(self) -> None:
        """Turn off the light."""
        await self.platform.send_command(self.device_id, "turn_off")

    async def set_brightness(self, value: int) -> None:
        """Set the light brightness (0-100)."""
        await self.platform.send_command(self.device_id, "set_brightness", value=value)


class ColoredLightAdapter(LightAdapter):
    """Adapter for RGBW colored light devices.

    Inherits turn_on, turn_off, set_brightness from LightAdapter and adds
    RGBW color control via the set_color method. Maps to Gira X1 DimmerRGBW
    channel datapoints Red (index 2), Green (index 3), Blue (index 4), White (index 5).
    Values are 0-255 integers per GPA Project Interface Documentation.
    """

    async def set_color(self, red: int, green: int, blue: int, white: int | None = None) -> None:
        """Set RGBW color for a colored light device.

        Args:
            red: Red channel value 0-255.
            green: Green channel value 0-255.
            blue: Blue channel value 0-255.
            white: Optional white channel value 0-255. Only sent if provided.
        """
        params: dict = {"red": red, "green": green, "blue": blue}
        if white is not None:
            params["white"] = white
        await self.platform.send_command(self.device_id, "set_color", **params)


class BlindAdapter(DeviceAdapter):
    """Adapter for blind/shutter devices."""

    async def open(self) -> None:
        """Open the blind (dispatches 'open' command)."""
        await self.platform.send_command(self.device_id, "open")

    async def close(self) -> None:
        """Close the blind (dispatches 'close' command)."""
        await self.platform.send_command(self.device_id, "close")

    async def stop(self) -> None:
        """Stop blind movement."""
        await self.platform.send_command(self.device_id, "stop")


class ClimateAdapter(DeviceAdapter):
    """Adapter for climate/heating devices."""

    async def set_temperature(self, value: float) -> None:
        """Set the target temperature."""
        await self.platform.send_command(self.device_id, "set_temperature", value=value)

    async def set_mode(self, mode: str) -> None:
        """Set the climate mode (e.g., 'heat', 'cool', 'auto')."""
        await self.platform.send_command(self.device_id, "set_mode", mode=mode)

    async def turn_on(self) -> None:
        """Turn on the climate device."""
        await self.platform.send_command(self.device_id, "turn_on")

    async def turn_off(self) -> None:
        """Turn off the climate device."""
        await self.platform.send_command(self.device_id, "turn_off")


class SensorAdapter(DeviceAdapter):
    """Read-only adapter for sensor devices. No command methods."""


class CameraAdapter(SensorAdapter):
    """Typed SensorAdapter subclass for camera devices.

    Read-only. Purpose is type identification only. Tools that need snapshots
    or events must access FrigatePlatform directly.
    """


class PresenceSensorAdapter(SensorAdapter):
    """Read-only adapter for presence sensor devices."""

    async def is_home(self) -> bool | None:
        """Return True if presence detected, False if not, None if state unavailable."""
        state = await self.read_state()
        val = state.state.get("present", state.state.get("is_home"))
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("1", "true", "yes")


class WeatherSensorAdapter(SensorAdapter):
    """Read-only adapter for weather sensor devices."""

    async def get_temperature(self) -> float | None:
        """Return current temperature in degrees, or None if unavailable."""
        state = await self.read_state()
        val = state.state.get("temperature", state.state.get("Temperature"))
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None


class DoorStationDeviceAdapter(DeviceAdapter):
    """Adapter for door station devices.

    Named DoorStationDeviceAdapter (not DoorStationAdapter) to avoid collision
    with src/integrations/door_station.py:DoorStationAdapter.
    """

    async def open_door(self) -> None:
        """Send open_door command to the door station."""
        await self.platform.send_command(self.device_id, "open_door")


_DEVICE_TYPE_TO_ADAPTER: dict[DeviceType, type[DeviceAdapter]] = {
    DeviceType.LIGHT: LightAdapter,
    DeviceType.DIMMABLE_LIGHT: LightAdapter,
    DeviceType.COLORED_LIGHT: ColoredLightAdapter,
    DeviceType.SWITCH: LightAdapter,
    DeviceType.BLIND: BlindAdapter,
    DeviceType.CLIMATE: ClimateAdapter,
    DeviceType.HEATING: ClimateAdapter,
    DeviceType.BINARY_SENSOR: SensorAdapter,
    DeviceType.SCENE: DeviceAdapter,
    DeviceType.TRIGGER: DeviceAdapter,
    DeviceType.CAMERA: CameraAdapter,
    DeviceType.PRESENCE_SENSOR: PresenceSensorAdapter,
    DeviceType.WEATHER_SENSOR: WeatherSensorAdapter,
    DeviceType.DOOR_STATION: DoorStationDeviceAdapter,
    DeviceType.CALENDAR_SENSOR: SensorAdapter,
}


def create_adapter(info: DeviceInfo, platform: Platform) -> DeviceAdapter:
    """Factory: return the appropriate adapter subclass based on device type."""
    adapter_cls = _DEVICE_TYPE_TO_ADAPTER.get(info.device_type, DeviceAdapter)
    return adapter_cls(info, platform)
