from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.core.guardrails import RequestSource, request_security_context
from src.tools.security import (
    alarm_control,
    door_open,
    get_camera_events,
    get_security_status,
    set_context,
)
import src.tools.security as security_module
from src.tools.context import ToolContext
from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass


def _make_device_id(native_id: str) -> DeviceId:
    return DeviceId(platform="mock", native_id=native_id)


def _make_sensor_adapter(
    native_id: str,
    name: str,
    room: str,
    sensor_class: SensorClass,
    binary_val: str = "0",
) -> MagicMock:
    adapter = MagicMock()
    adapter.device_id = _make_device_id(native_id)
    adapter.info = DeviceInfo(
        id=_make_device_id(native_id),
        name=name,
        device_type=DeviceType.BINARY_SENSOR,
        room=room,
        sensor_class=sensor_class,
    )
    adapter.read_state = AsyncMock(
        return_value=DeviceState(
            device_id=_make_device_id(native_id),
            state={"Binary": binary_val},
            last_updated=datetime.now(timezone.utc),
        )
    )
    return adapter


class AdminProfile:
    trust_level = "admin"


class MockHAL:
    def __init__(self, alarm_val: str = "0", window_val: str = "0") -> None:
        self._door_devices: list = []
        self.alarm = _make_sensor_adapter(
            "alarm-1", "Alarm Bereit", "Haus", SensorClass.ALARM_READINESS, alarm_val
        )
        self.window = _make_sensor_adapter(
            "window-1", "Schlafzimmer Fenster", "Schlafzimmer", SensorClass.WINDOW, window_val
        )
        self.executed: list[tuple] = []

    def get_sensors_by_class(self, sensor_class: SensorClass) -> list:
        if sensor_class == SensorClass.ALARM_READINESS:
            return [self.alarm]
        if sensor_class == SensorClass.WINDOW:
            return [self.window]
        return []

    def get_devices_by_type(self, device_type: DeviceType) -> list:
        if device_type == DeviceType.DOOR_STATION and self._door_devices:
            return self._door_devices
        return []

    async def execute(self, device_id: DeviceId, command: str, **params) -> None:
        self.executed.append((device_id, command, params))


class MockCameraPlatform:
    async def get_events(self, hours: int = 24) -> list:
        return [
            {"camera": "entrance", "label": "person"},
            {"camera": "entrance", "label": "person"},
            {"camera": "driveway", "label": "car"},
        ]


class MockDoorStationPlatform:
    def __init__(self) -> None:
        self.relay_triggered = False

    async def trigger_door_relay(self) -> None:
        self.relay_triggered = True

    async def discover_devices(self) -> list:
        device_id = DeviceId(platform="door_station", native_id="door_station_main")
        info = DeviceInfo(
            id=device_id,
            name="Haustür",
            device_type=DeviceType.DOOR_STATION,
            room="Eingang",
            capabilities={"open_door"},
        )
        return [info]

    async def send_command(self, device_id, command: str, **params) -> None:
        if command == "open_door":
            await self.trigger_door_relay()


def _make_ctx(
    hal,
    camera_platform=None,
    door_station_platform=None,
    *,
    window_state_known: bool = True,
) -> ToolContext:
    # Inject platforms into HAL mock so get_platform() works
    platforms_dict = {}
    if camera_platform is not None:
        platforms_dict["frigate"] = camera_platform
    if door_station_platform is not None:
        platforms_dict["door_station"] = door_station_platform
    hal.get_platform = lambda name: platforms_dict.get(name)
    presence_manager = MagicMock()
    presence_manager.async_nobody_home = AsyncMock(return_value=True)
    presence_manager.window_state_known = window_state_known
    presence_manager.open_windows = []
    return ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


def make_deps(with_camera: bool = False) -> MockHAL:
    hal = MockHAL()
    camera_platform = MockCameraPlatform() if with_camera else None
    set_context(_make_ctx(hal, camera_platform=camera_platform))
    return hal


async def test_get_security_status():
    make_deps()
    result = await get_security_status()
    assert "Sicherheitsstatus" in result
    assert "Alarm" in result


async def test_get_security_status_shows_closed_windows():
    make_deps()
    result = await get_security_status()
    assert "geschlossen" in result.lower() or "Alarm" in result


async def test_get_security_status_shows_open_window():
    hal = MockHAL(window_val="1")
    set_context(_make_ctx(hal))
    result = await get_security_status()
    assert "Offen" in result or "offen" in result.lower()


async def test_get_camera_events_no_camera():
    make_deps(with_camera=False)
    result = await get_camera_events()
    assert "nicht konfiguriert" in result


async def test_get_camera_events_with_camera():
    make_deps(with_camera=True)
    result = await get_camera_events(hours=24)
    assert "3" in result or "entrance" in result


async def test_alarm_control_arm():
    hal = make_deps()
    with request_security_context(user_profile=AdminProfile(), source=RequestSource.USER):
        result = await alarm_control("arm")
    assert "scharf gestellt" in result
    assert any(cmd == "set_binary" and p.get("value") == "1" for _, cmd, p in hal.executed)


async def test_alarm_control_disarm():
    hal = make_deps()
    with request_security_context(
        user_profile=AdminProfile(),
        source=RequestSource.USER,
        confirmed_actions={"alarm_disarm"},
    ):
        result = await alarm_control("disarm")
    assert "unscharf gestellt" in result
    assert any(cmd == "set_binary" and p.get("value") == "0" for _, cmd, p in hal.executed)


async def test_alarm_control_invalid_action():
    make_deps()
    result = await alarm_control("invalid")
    assert "Unbekannte Aktion" in result


async def test_alarm_arm_denied_when_window_state_unknown():
    hal = MockHAL()
    set_context(_make_ctx(hal, window_state_known=False))
    with request_security_context(user_profile=AdminProfile(), source=RequestSource.USER):
        result = await alarm_control("arm")
    assert "Fensterstatus ist unbekannt" in result
    assert hal.executed == []


async def test_automation_cannot_control_alarm():
    hal = make_deps()
    with request_security_context(user_profile=AdminProfile(), source=RequestSource.AUTOMATION):
        result = await alarm_control("arm")
    assert "verweigert" in result
    assert hal.executed == []


async def test_door_open_fails_closed_without_request_context():
    hal = MockHAL()
    door = MagicMock()
    door.device_id = DeviceId(platform="door_station", native_id="door_station_main")
    hal._door_devices = [door]
    set_context(_make_ctx(hal))

    result = await door_open()

    assert "verweigert" in result
    assert hal.executed == []


async def test_no_ctx_returns_error():
    security_module._ctx = None
    result = await get_security_status()
    assert "nicht verfügbar" in result


async def test_door_open_via_hal_device():
    """door_open() executes via HAL when a DOOR_STATION device is registered."""
    hal = MockHAL()
    door_id = DeviceId(platform="door_station", native_id="door_station_main")
    # get_devices_by_type returns DeviceAdapter objects which have .device_id
    mock_door_adapter = MagicMock()
    mock_door_adapter.device_id = door_id
    hal._door_devices = [mock_door_adapter]
    set_context(_make_ctx(hal))

    with request_security_context(
        user_profile=AdminProfile(),
        source=RequestSource.USER,
        confirmed_actions={"door_open"},
    ):
        result = await door_open()
    assert "geöffnet" in result.lower() or "Tür" in result
    assert any(cmd == "open_door" for _, cmd, _ in hal.executed)


async def test_door_open_returns_not_found_without_door_station():
    """door_open() returns 'nicht verfügbar' when no door_station is set and no HAL devices."""
    hal = MockHAL()
    set_context(_make_ctx(hal, door_station_platform=None))

    with request_security_context(
        user_profile=AdminProfile(),
        source=RequestSource.USER,
        confirmed_actions={"door_open"},
    ):
        result = await door_open()
    assert "nicht" in result.lower() or "Keine" in result
