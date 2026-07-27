"""Tests for CalendarPlatform — HAL Platform for CalDAV calendar events."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.hal.models import DeviceId, DeviceState, DeviceType
from src.hal.platforms.calendar import CalendarPlatform, _CALENDAR_DEVICE_ID


def _make_config(url: str = ""):
    cfg = MagicMock()
    cfg.url = url
    cfg.username = "user"
    cfg.password = "pass"
    cfg.calendar_name = ""
    return cfg


@pytest.fixture
def config():
    return _make_config(url="https://nextcloud.example.com/dav")


@pytest.fixture
def platform(config):
    return CalendarPlatform(config=config)


@pytest.mark.asyncio
async def test_discover_returns_one_device(platform):
    devices = await platform.discover_devices()
    assert len(devices) == 1
    device = devices[0]
    assert device.device_type == DeviceType.CALENDAR_SENSOR
    assert device.id.native_id == _CALENDAR_DEVICE_ID
    assert device.name == "Kalender"
    assert device.room == "Haus"


@pytest.mark.asyncio
async def test_platform_name(platform):
    assert platform.name == "calendar"


@pytest.mark.asyncio
async def test_read_state_empty_events(platform):
    device_id = DeviceId(platform="calendar", native_id=_CALENDAR_DEVICE_ID)
    state = await platform.read_state(device_id)
    assert isinstance(state, DeviceState)
    assert state.state["events_today"] == 0
    assert state.state["next_event"] == ""


@pytest.mark.asyncio
async def test_read_state_with_events(platform):
    platform._last_events = [
        {"summary": "Arzt", "start": "15:00", "end": "16:00", "location": ""},
        {"summary": "Sport", "start": "18:00", "end": "19:00", "location": ""},
    ]
    device_id = DeviceId(platform="calendar", native_id=_CALENDAR_DEVICE_ID)
    state = await platform.read_state(device_id)
    assert state.state["events_today"] == 2
    assert "Arzt" in state.state["next_event"]
    assert "Arzt" in state.state["events_summary"]


@pytest.mark.asyncio
async def test_send_command_raises(platform):
    device_id = DeviceId(platform="calendar", native_id=_CALENDAR_DEVICE_ID)
    with pytest.raises(NotImplementedError):
        await platform.send_command(device_id, "create_event")


@pytest.mark.asyncio
async def test_subscribe_stores_callback(platform):
    cb = AsyncMock()
    await platform.subscribe_events(cb)
    assert platform._event_callback is cb


@pytest.mark.asyncio
async def test_health_check_no_adapter_returns_true(platform):
    # Platform not started — health_check should return True (not a failure)
    result = await platform.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_format_for_prompt_no_adapter(platform):
    result = platform.format_for_prompt()
    assert result == ""


@pytest.mark.asyncio
async def test_get_upcoming_events_no_adapter(platform):
    result = await platform.get_upcoming_events()
    assert result == []


@pytest.mark.asyncio
async def test_get_upcoming_events_delegates_to_adapter(platform):
    mock_adapter = AsyncMock()
    mock_adapter.get_upcoming_events.return_value = [{"summary": "Meeting", "start": "14:00"}]
    platform._adapter = mock_adapter

    result = await platform.get_upcoming_events(hours=12)
    assert len(result) == 1
    mock_adapter.get_upcoming_events.assert_called_once_with(hours=12)


@pytest.mark.asyncio
async def test_poll_loop_emits_callback_on_change(platform):
    callback_states: list[DeviceState] = []

    async def callback(state: DeviceState) -> None:
        callback_states.append(state)

    await platform.subscribe_events(callback)

    new_events = [{"summary": "Arzt", "start": "15:00", "end": "16:00", "location": ""}]
    mock_adapter = AsyncMock()
    mock_adapter.get_upcoming_events.return_value = new_events
    platform._adapter = mock_adapter

    # Simulate one poll iteration manually
    fetched = await platform._adapter.get_upcoming_events(hours=24)
    if fetched != platform._last_events:
        platform._last_events = fetched
        if platform._event_callback:
            device_id = DeviceId(platform=platform.name, native_id=_CALENDAR_DEVICE_ID)
            state = platform._build_device_state(device_id, fetched)
            await platform._event_callback(state)

    assert len(callback_states) == 1
    assert callback_states[0].state["events_today"] == 1
