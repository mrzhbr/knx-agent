"""Tests for DoorStationPlatform — HAL Platform for Mobotix T24M door station."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest  # type: ignore[import-not-found]

from src.hal.models import DeviceId, DeviceState, DeviceType
from src.hal.platforms.door_station import DoorStationPlatform, _DOOR_STATION_ID


@pytest.fixture
def platform():
    return DoorStationPlatform(
        host="192.0.2.100",
        password="test-placeholder",
        webhook_port=8103,
        bell_camera="entrance",
    )


@pytest.mark.asyncio
async def test_discover_returns_one_device(platform):
    devices = await platform.discover_devices()
    assert len(devices) == 1
    device = devices[0]
    assert device.device_type == DeviceType.DOOR_STATION
    assert device.id.native_id == _DOOR_STATION_ID
    assert device.name == "Haustür"
    assert device.room == "Eingang"
    assert "open_door" in device.capabilities


@pytest.mark.asyncio
async def test_platform_name(platform):
    assert platform.name == "door_station"


@pytest.mark.asyncio
async def test_read_state_initial(platform):
    device_id = DeviceId(platform="door_station", native_id=_DOOR_STATION_ID)
    state = await platform.read_state(device_id)
    assert isinstance(state, DeviceState)
    assert state.state["last_bell"] == ""
    assert state.state["last_bell_camera"] == "entrance"


@pytest.mark.asyncio
async def test_bell_event_updates_state(platform):
    await platform._on_bell()
    device_id = DeviceId(platform="door_station", native_id=_DOOR_STATION_ID)
    state = await platform.read_state(device_id)
    assert state.state["last_bell"] != ""
    assert state.state["last_bell_camera"] == "entrance"


@pytest.mark.asyncio
async def test_bell_event_calls_callback(platform):
    callback_states: list[DeviceState] = []

    async def callback(state: DeviceState) -> None:
        callback_states.append(state)

    await platform.subscribe_events(callback)
    await platform._on_bell()

    assert len(callback_states) == 1
    state = callback_states[0]
    assert state.device_id.native_id == _DOOR_STATION_ID
    assert state.state["last_bell"] != ""


@pytest.mark.asyncio
async def test_subscribe_stores_callback(platform):
    cb = AsyncMock()
    await platform.subscribe_events(cb)
    assert platform._event_callback is cb


@pytest.mark.asyncio
async def test_send_command_open_door(platform):
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="door_station", native_id=_DOOR_STATION_ID)
    await platform.send_command(device_id, "open_door")
    mock_adapter.trigger_door_relay.assert_called_once()


@pytest.mark.asyncio
async def test_send_command_relay_failure_raises(platform):
    mock_adapter = AsyncMock()
    mock_adapter.trigger_door_relay.return_value = False
    platform._adapter = mock_adapter
    device_id = DeviceId(platform="door_station", native_id=_DOOR_STATION_ID)

    with pytest.raises(RuntimeError, match="relay"):
        await platform.send_command(device_id, "open_door")


@pytest.mark.asyncio
async def test_send_command_unknown_raises(platform):
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="door_station", native_id=_DOOR_STATION_ID)
    with pytest.raises(ValueError, match="Unknown command"):
        await platform.send_command(device_id, "turn_on")


@pytest.mark.asyncio
async def test_send_command_no_adapter_raises(platform):
    device_id = DeviceId(platform="door_station", native_id=_DOOR_STATION_ID)
    with pytest.raises(RuntimeError, match="not started"):
        await platform.send_command(device_id, "open_door")


@pytest.mark.asyncio
async def test_health_check_delegates_to_adapter(platform):
    mock_adapter = AsyncMock()
    mock_adapter.health_check.return_value = True
    platform._adapter = mock_adapter
    result = await platform.health_check()
    assert result


@pytest.mark.asyncio
async def test_health_check_no_adapter(platform):
    result = await platform.health_check()
    assert not result
