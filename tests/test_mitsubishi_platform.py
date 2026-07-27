"""Tests for MitsubishiPlatform — HAL Platform for Mitsubishi AC units."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.hal.models import DeviceId, DeviceState, DeviceType, SensorClass
from src.hal.platforms.mitsubishi import MitsubishiPlatform


def _make_config(units=None):
    cfg = MagicMock()
    unit_list = []
    if units:
        for name, room, ip in units:
            u = MagicMock()
            u.name = name
            u.room = room
            u.ip = ip
            unit_list.append(u)
    cfg.units = unit_list
    cfg.poll_interval = 60
    return cfg


@pytest.fixture
def config():
    return _make_config(
        [
            ("Wohnzimmer AC", "Wohnzimmer", "192.168.1.20"),
            ("Schlafzimmer AC", "Schlafzimmer", "192.168.1.21"),
        ]
    )


@pytest.fixture
def platform(config):
    return MitsubishiPlatform(config=config)


@pytest.mark.asyncio
async def test_discover_returns_correct_devices(platform, config):
    devices = await platform.discover_devices()
    assert len(devices) == 2
    assert all(d.device_type == DeviceType.CLIMATE for d in devices)
    assert all(d.sensor_class == SensorClass.TEMPERATURE for d in devices)


@pytest.mark.asyncio
async def test_discover_device_capabilities(platform):
    devices = await platform.discover_devices()
    for d in devices:
        assert "turn_on" in d.capabilities
        assert "turn_off" in d.capabilities
        assert "set_temperature" in d.capabilities
        assert "set_mode" in d.capabilities


@pytest.mark.asyncio
async def test_discover_rooms_from_config(platform, config):
    devices = await platform.discover_devices()
    rooms = {d.room for d in devices}
    assert "Wohnzimmer" in rooms
    assert "Schlafzimmer" in rooms


@pytest.mark.asyncio
async def test_read_state_no_adapter(platform):
    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    state = await platform.read_state(device_id)
    assert isinstance(state, DeviceState)
    assert state.state["power"] == "off"


@pytest.mark.asyncio
async def test_read_state_with_adapter(platform):
    await platform.discover_devices()
    mock_adapter = MagicMock()
    mock_adapter.get_status.return_value = {
        "Wohnzimmer": {
            "power": "PowerOnOff.ON",
            "mode": "DriveMode.COOLER",
            "set_temp": 22.0,
            "room_temp": 26.5,
            "power_watt": 450,
        }
    }
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    state = await platform.read_state(device_id)
    mock_adapter.get_status.assert_called_once_with("Wohnzimmer")
    assert state.state["power"] == "on"
    assert state.state["temperature"] == 22.0
    assert state.state["room_temperature"] == 26.5
    assert state.state["power_watt"] == 450


@pytest.mark.asyncio
async def test_send_command_turn_on(platform):
    await platform.discover_devices()
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    await platform.send_command(device_id, "turn_on")
    mock_adapter.set_power.assert_called_once_with("Wohnzimmer", True)


@pytest.mark.asyncio
async def test_send_command_turn_off(platform):
    await platform.discover_devices()
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    await platform.send_command(device_id, "turn_off")
    mock_adapter.set_power.assert_called_once_with("Wohnzimmer", False)


@pytest.mark.asyncio
async def test_send_command_set_temperature(platform):
    await platform.discover_devices()
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    await platform.send_command(device_id, "set_temperature", value=21.0)
    mock_adapter.set_temperature.assert_called_once_with("Wohnzimmer", 21.0)


@pytest.mark.asyncio
async def test_send_command_set_mode(platform):
    await platform.discover_devices()
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    await platform.send_command(device_id, "set_mode", mode="COOLER")
    mock_adapter.set_mode.assert_called_once_with("Wohnzimmer", "COOLER")


@pytest.mark.asyncio
async def test_send_command_unknown_raises(platform):
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    with pytest.raises(ValueError, match="Unknown command"):
        await platform.send_command(device_id, "open_window")


@pytest.mark.asyncio
async def test_send_command_no_adapter_raises(platform):
    device_id = DeviceId(platform="mitsubishi_ac", native_id="Wohnzimmer AC")
    with pytest.raises(RuntimeError, match="not started"):
        await platform.send_command(device_id, "turn_on")


@pytest.mark.asyncio
async def test_platform_name(platform):
    assert platform.name == "mitsubishi_ac"


@pytest.mark.asyncio
async def test_health_check_delegates_to_adapter(platform):
    mock_adapter = AsyncMock()
    mock_adapter.health_check.return_value = True
    platform._adapter = mock_adapter
    result = await platform.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_send_command_uses_room_not_unit_name(platform):
    """Regression: adapter expects room name, not unit name (native_id)."""
    await platform.discover_devices()
    mock_adapter = AsyncMock()
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Schlafzimmer AC")
    await platform.send_command(device_id, "set_temperature", value=23.0)
    # Adapter must receive "Schlafzimmer" (room), not "Schlafzimmer AC" (unit name)
    mock_adapter.set_temperature.assert_called_once_with("Schlafzimmer", 23.0)


@pytest.mark.asyncio
async def test_read_state_uses_room_not_unit_name(platform):
    """Regression: read_state must pass room name to adapter.get_status()."""
    await platform.discover_devices()
    mock_adapter = MagicMock()
    mock_adapter.get_status.return_value = {"Schlafzimmer": {"power": "off"}}
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="mitsubishi_ac", native_id="Schlafzimmer AC")
    await platform.read_state(device_id)
    mock_adapter.get_status.assert_called_once_with("Schlafzimmer")
