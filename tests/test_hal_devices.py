from __future__ import annotations

from datetime import datetime, timezone

from src.hal.devices import (
    BlindAdapter,
    ClimateAdapter,
    LightAdapter,
    SensorAdapter,
    create_adapter,
)
from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType
from src.hal.platform import Platform


class MockPlatform(Platform):
    """In-memory mock platform for testing."""

    def __init__(self) -> None:
        self._commands: list[tuple] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    @property
    def error_message(self) -> str:
        return "⚠ Mock ist offline."

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def discover_devices(self) -> list[DeviceInfo]:
        return []

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        return DeviceState(
            device_id=device_id,
            state={},
            last_updated=datetime.now(timezone.utc),
        )

    async def send_command(self, device_id: DeviceId, command: str, **params) -> None:
        self._commands.append((device_id, command, params))


def _make_device(device_type: DeviceType, native_id: str = "1") -> DeviceInfo:
    return DeviceInfo(
        id=DeviceId(platform="mock", native_id=native_id),
        name="Test",
        device_type=device_type,
        room="Testraum",
    )


async def test_light_turn_on() -> None:
    platform = MockPlatform()
    adapter = LightAdapter(_make_device(DeviceType.LIGHT), platform)
    await adapter.turn_on()
    assert platform._commands[0][1] == "turn_on"
    assert platform._commands[0][2] == {}


async def test_light_turn_on_with_brightness() -> None:
    platform = MockPlatform()
    adapter = LightAdapter(_make_device(DeviceType.LIGHT), platform)
    await adapter.turn_on(brightness=75)
    assert platform._commands[0][1] == "turn_on"
    assert platform._commands[0][2]["brightness"] == 75


async def test_light_turn_off() -> None:
    platform = MockPlatform()
    adapter = LightAdapter(_make_device(DeviceType.LIGHT), platform)
    await adapter.turn_off()
    assert platform._commands[0][1] == "turn_off"


async def test_light_set_brightness() -> None:
    platform = MockPlatform()
    adapter = LightAdapter(_make_device(DeviceType.DIMMABLE_LIGHT), platform)
    await adapter.set_brightness(50)
    assert platform._commands[0][1] == "set_brightness"
    assert platform._commands[0][2]["value"] == 50


async def test_blind_open() -> None:
    platform = MockPlatform()
    adapter = BlindAdapter(_make_device(DeviceType.BLIND), platform)
    await adapter.open()
    assert platform._commands[0][1] == "open"


async def test_blind_close() -> None:
    platform = MockPlatform()
    adapter = BlindAdapter(_make_device(DeviceType.BLIND), platform)
    await adapter.close()
    assert platform._commands[0][1] == "close"


async def test_blind_stop() -> None:
    platform = MockPlatform()
    adapter = BlindAdapter(_make_device(DeviceType.BLIND), platform)
    await adapter.stop()
    assert platform._commands[0][1] == "stop"


async def test_climate_set_temperature() -> None:
    platform = MockPlatform()
    adapter = ClimateAdapter(_make_device(DeviceType.CLIMATE), platform)
    await adapter.set_temperature(21.5)
    assert platform._commands[0][1] == "set_temperature"
    assert platform._commands[0][2]["value"] == 21.5


async def test_climate_turn_on_off() -> None:
    platform = MockPlatform()
    adapter = ClimateAdapter(_make_device(DeviceType.HEATING), platform)
    await adapter.turn_on()
    await adapter.turn_off()
    assert platform._commands[0][1] == "turn_on"
    assert platform._commands[1][1] == "turn_off"


def test_sensor_read_only() -> None:
    platform = MockPlatform()
    adapter = SensorAdapter(_make_device(DeviceType.BINARY_SENSOR), platform)
    assert not hasattr(adapter, "turn_on")
    assert not hasattr(adapter, "turn_off")
    assert not hasattr(adapter, "send_command")
    assert hasattr(adapter, "read_state")


def test_create_adapter_routing() -> None:
    platform = MockPlatform()
    assert isinstance(create_adapter(_make_device(DeviceType.LIGHT), platform), LightAdapter)
    assert isinstance(
        create_adapter(_make_device(DeviceType.DIMMABLE_LIGHT), platform), LightAdapter
    )
    assert isinstance(
        create_adapter(_make_device(DeviceType.COLORED_LIGHT), platform), LightAdapter
    )
    assert isinstance(create_adapter(_make_device(DeviceType.BLIND), platform), BlindAdapter)
    assert isinstance(create_adapter(_make_device(DeviceType.CLIMATE), platform), ClimateAdapter)
    assert isinstance(create_adapter(_make_device(DeviceType.HEATING), platform), ClimateAdapter)
    assert isinstance(
        create_adapter(_make_device(DeviceType.BINARY_SENSOR), platform), SensorAdapter
    )


async def test_colored_light_set_color() -> None:
    platform = MockPlatform()
    from src.hal.devices import ColoredLightAdapter

    adapter = ColoredLightAdapter(_make_device(DeviceType.COLORED_LIGHT), platform)
    await adapter.set_color(255, 128, 0)
    assert platform._commands[0][1] == "set_color"
    assert platform._commands[0][2]["red"] == 255
    assert platform._commands[0][2]["green"] == 128
    assert platform._commands[0][2]["blue"] == 0
    assert "white" not in platform._commands[0][2]


async def test_colored_light_set_color_with_white() -> None:
    platform = MockPlatform()
    from src.hal.devices import ColoredLightAdapter

    adapter = ColoredLightAdapter(_make_device(DeviceType.COLORED_LIGHT), platform)
    await adapter.set_color(255, 0, 0, white=200)
    assert platform._commands[0][1] == "set_color"
    assert platform._commands[0][2]["white"] == 200


async def test_colored_light_inherits_brightness() -> None:
    platform = MockPlatform()
    from src.hal.devices import ColoredLightAdapter

    adapter = ColoredLightAdapter(_make_device(DeviceType.COLORED_LIGHT), platform)
    await adapter.set_brightness(75)
    assert platform._commands[0][1] == "set_brightness"
    assert platform._commands[0][2]["value"] == 75


def test_create_adapter_routing_colored_light() -> None:
    from src.hal.devices import ColoredLightAdapter

    platform = MockPlatform()
    adapter = create_adapter(_make_device(DeviceType.COLORED_LIGHT), platform)
    assert isinstance(adapter, ColoredLightAdapter)


# --- Tests for new DeviceAdapters (Task 1.3) ---


def test_create_adapter_new_types() -> None:
    from src.hal.devices import (
        CameraAdapter,
        DoorStationDeviceAdapter,
        PresenceSensorAdapter,
        WeatherSensorAdapter,
    )

    platform = MockPlatform()
    assert isinstance(create_adapter(_make_device(DeviceType.CAMERA), platform), CameraAdapter)
    assert isinstance(
        create_adapter(_make_device(DeviceType.PRESENCE_SENSOR), platform), PresenceSensorAdapter
    )
    assert isinstance(
        create_adapter(_make_device(DeviceType.WEATHER_SENSOR), platform), WeatherSensorAdapter
    )
    assert isinstance(
        create_adapter(_make_device(DeviceType.DOOR_STATION), platform),
        DoorStationDeviceAdapter,
    )


def _make_platform_with_state(state: dict) -> "MockPlatformWithState":
    """Create a MockPlatform that returns a specific state from read_state()."""
    return MockPlatformWithState(state)


class MockPlatformWithState(Platform):
    """MockPlatform variant that returns a preconfigured state from read_state()."""

    def __init__(self, state: dict) -> None:
        self._state = state
        self._commands: list[tuple] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    @property
    def error_message(self) -> str:
        return "⚠ Mock ist offline."

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def discover_devices(self) -> list[DeviceInfo]:
        return []

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        return DeviceState(
            device_id=device_id,
            state=self._state,
            last_updated=datetime.now(timezone.utc),
        )

    async def send_command(self, device_id: DeviceId, command: str, **params) -> None:
        self._commands.append((device_id, command, params))


async def test_presence_sensor_is_home_true() -> None:
    from src.hal.devices import PresenceSensorAdapter

    platform = _make_platform_with_state({"present": True})
    adapter = PresenceSensorAdapter(_make_device(DeviceType.PRESENCE_SENSOR), platform)
    result = await adapter.is_home()
    assert result is True


async def test_presence_sensor_is_home_false() -> None:
    from src.hal.devices import PresenceSensorAdapter

    platform = _make_platform_with_state({"present": False})
    adapter = PresenceSensorAdapter(_make_device(DeviceType.PRESENCE_SENSOR), platform)
    result = await adapter.is_home()
    assert result is False


async def test_presence_sensor_is_home_none_when_no_state() -> None:
    from src.hal.devices import PresenceSensorAdapter

    platform = _make_platform_with_state({})
    adapter = PresenceSensorAdapter(_make_device(DeviceType.PRESENCE_SENSOR), platform)
    result = await adapter.is_home()
    assert result is None


async def test_weather_sensor_get_temperature() -> None:
    from src.hal.devices import WeatherSensorAdapter

    platform = _make_platform_with_state({"temperature": 21.5})
    adapter = WeatherSensorAdapter(_make_device(DeviceType.WEATHER_SENSOR), platform)
    result = await adapter.get_temperature()
    assert result == 21.5


async def test_weather_sensor_get_temperature_none_when_no_state() -> None:
    from src.hal.devices import WeatherSensorAdapter

    platform = _make_platform_with_state({})
    adapter = WeatherSensorAdapter(_make_device(DeviceType.WEATHER_SENSOR), platform)
    result = await adapter.get_temperature()
    assert result is None


async def test_door_station_open_door() -> None:
    from src.hal.devices import DoorStationDeviceAdapter

    platform = MockPlatform()
    adapter = DoorStationDeviceAdapter(_make_device(DeviceType.DOOR_STATION), platform)
    await adapter.open_door()
    assert len(platform._commands) == 1
    assert platform._commands[0][1] == "open_door"


def test_camera_adapter_is_sensor_adapter() -> None:
    from src.hal.devices import CameraAdapter, SensorAdapter

    assert issubclass(CameraAdapter, SensorAdapter)
