from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass


def test_device_type_is_str() -> None:
    assert DeviceType.LIGHT == "light"
    assert isinstance(DeviceType.LIGHT, str)


def test_sensor_class_is_str() -> None:
    assert SensorClass.WINDOW == "window"
    assert isinstance(SensorClass.WINDOW, str)


def test_device_id_key() -> None:
    device_id = DeviceId(platform="gira_x1", native_id="abc")
    assert device_id.key == "gira_x1:abc"


def test_device_id_hashable() -> None:
    device_id = DeviceId(platform="gira_x1", native_id="abc")
    d = {device_id: "value"}
    assert d[device_id] == "value"


def test_device_id_frozen() -> None:
    device_id = DeviceId(platform="gira_x1", native_id="abc")
    with pytest.raises(Exception):
        device_id.platform = "other"  # type: ignore[misc]


def test_device_info_defaults() -> None:
    device_id = DeviceId(platform="gira_x1", native_id="123")
    info = DeviceInfo(
        id=device_id,
        name="Deckenlampe",
        device_type=DeviceType.LIGHT,
        room="Wohnzimmer",
    )
    assert info.floor == ""
    assert info.sensor_class == SensorClass.GENERIC
    assert info.capabilities == set()
    assert info.is_group is False
    assert info.platform_data == {}


def test_device_info_with_capabilities() -> None:
    device_id = DeviceId(platform="gira_x1", native_id="456")
    info = DeviceInfo(
        id=device_id,
        name="Dimmer",
        device_type=DeviceType.DIMMABLE_LIGHT,
        room="Schlafzimmer",
        capabilities={"brightness", "color_temp"},
    )
    assert "brightness" in info.capabilities
    assert "color_temp" in info.capabilities


def test_device_state_serialization() -> None:
    device_id = DeviceId(platform="gira_x1", native_id="789")
    now = datetime.now(timezone.utc)
    state = DeviceState(
        device_id=device_id,
        state={"on": True, "brightness": 80.0},
        last_updated=now,
    )
    dumped = state.model_dump()
    assert dumped["state"]["on"] is True
    assert dumped["state"]["brightness"] == 80.0


def test_device_state_source_default() -> None:
    device_id = DeviceId(platform="gira_x1", native_id="abc")
    state = DeviceState(
        device_id=device_id,
        state={},
        last_updated=datetime.now(timezone.utc),
    )
    assert state.source == "external"


# --- Tests for new DeviceType enum members (Task 1.2) ---


def test_new_device_types_exist() -> None:
    assert DeviceType.PRESENCE_SENSOR == "presence_sensor"
    assert DeviceType.WEATHER_SENSOR == "weather_sensor"
    assert DeviceType.DOOR_STATION == "door_station"
    assert DeviceType.CALENDAR_SENSOR == "calendar_sensor"


def test_new_device_types_are_str() -> None:
    assert isinstance(DeviceType.PRESENCE_SENSOR, str)
    assert isinstance(DeviceType.WEATHER_SENSOR, str)
    assert isinstance(DeviceType.DOOR_STATION, str)
    assert isinstance(DeviceType.CALENDAR_SENSOR, str)


def test_sensor_class_presence_exists() -> None:
    assert SensorClass.PRESENCE == "presence"
    assert isinstance(SensorClass.PRESENCE, str)
