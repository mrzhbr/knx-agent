"""Tests for FrigatePlatform — HAL Platform for Frigate NVR."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # type: ignore[import-not-found]

from src.hal.models import DeviceId, DeviceState, DeviceType, SensorClass
from src.hal.platforms.frigate import FrigatePlatform


@pytest.fixture
def platform():
    return FrigatePlatform(
        mqtt_host="localhost",
        mqtt_port=1883,
        frigate_host="localhost",
        frigate_port=5000,
        camera_rooms={"entrance": "Entrance", "driveway": "Outside"},
        entrance_cameras=["entrance"],
    )


@pytest.mark.asyncio
async def test_discover_returns_configured_cameras(platform):
    devices = await platform.discover_devices()
    assert len(devices) == 2
    assert all(d.device_type == DeviceType.CAMERA for d in devices)
    assert all(d.sensor_class == SensorClass.MOTION for d in devices)


@pytest.mark.asyncio
async def test_discover_camera_names_match(platform):
    devices = await platform.discover_devices()
    native_ids = {d.id.native_id for d in devices}
    assert native_ids == {"entrance", "driveway"}


@pytest.mark.asyncio
async def test_discover_camera_platform_data(platform):
    devices = await platform.discover_devices()
    for d in devices:
        assert "camera_name" in d.platform_data
        assert d.platform_data["camera_name"] == d.id.native_id


@pytest.mark.asyncio
async def test_discover_uses_configured_camera_rooms(platform):
    devices = await platform.discover_devices()
    camera_rooms = {d.id.native_id: d.room for d in devices}
    assert camera_rooms == {"entrance": "Entrance", "driveway": "Outside"}


@pytest.mark.asyncio
async def test_read_state_empty_cache(platform):
    device_id = DeviceId(platform="frigate", native_id="entrance")
    state = await platform.read_state(device_id)
    assert isinstance(state, DeviceState)
    assert state.state["last_detection"] == "none"
    assert state.state["is_detecting"] == "false"


@pytest.mark.asyncio
async def test_detection_event_updates_cache_and_calls_callback(platform):
    callback_states: list[DeviceState] = []

    async def callback(state: DeviceState) -> None:
        callback_states.append(state)

    await platform.subscribe_events(callback)

    # Simulate a detection event
    detection = MagicMock()
    detection.camera = "entrance"
    detection.label = "person"
    detection.confidence = 0.95
    detection.zones = ["entrance_zone"]
    detection.timestamp = datetime(2026, 3, 30, 14, 22, 0)

    await platform._on_detection(detection)

    assert len(callback_states) == 1
    state = callback_states[0]
    assert state.device_id.native_id == "entrance"
    assert state.state["last_detection"] == "person"
    assert state.state["is_detecting"] == "true"
    assert "entrance_zone" in str(state.state["zones"])
    assert state.state["confidence"] == 0.95


@pytest.mark.asyncio
async def test_detection_updates_cache(platform):
    detection = MagicMock()
    detection.camera = "driveway"
    detection.label = "car"
    detection.confidence = 0.88
    detection.zones = []
    detection.timestamp = datetime(2026, 3, 30, 10, 0, 0)

    await platform._on_detection(detection)

    device_id = DeviceId(platform="frigate", native_id="driveway")
    state = await platform.read_state(device_id)
    assert state.state["last_detection"] == "car"
    assert state.state["is_detecting"] == "true"


@pytest.mark.asyncio
async def test_subscribe_stores_callback(platform):
    cb = AsyncMock()
    await platform.subscribe_events(cb)
    assert platform._event_callback is cb


@pytest.mark.asyncio
async def test_send_command_raises(platform):
    device_id = DeviceId(platform="frigate", native_id="entrance")
    with pytest.raises(NotImplementedError):
        await platform.send_command(device_id, "turn_on")


@pytest.mark.asyncio
async def test_platform_name():
    platform = FrigatePlatform(mqtt_host="h", mqtt_port=1883, frigate_host="h", frigate_port=5000)
    assert platform.name == "frigate"


@pytest.mark.asyncio
async def test_start_creates_adapter():
    with patch("src.hal.platforms.frigate.FrigatePlatform.start") as mock_start:
        mock_start.return_value = None
        platform = FrigatePlatform(
            mqtt_host="h", mqtt_port=1883, frigate_host="h", frigate_port=5000
        )
        await platform.start()
        mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_get_events_delegates_to_adapter(platform):
    mock_adapter = AsyncMock()
    mock_adapter.get_events.return_value = [{"camera": "driveway", "label": "car"}]
    platform._adapter = mock_adapter

    events = await platform.get_events(hours=4)
    assert len(events) == 1
    mock_adapter.get_events.assert_called_once_with(hours=4)


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
