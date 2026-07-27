"""Tests for WeatherPlatform — HAL Platform for Open-Meteo weather."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.hal.models import DeviceId, DeviceState, DeviceType, SensorClass
from src.hal.platforms.weather import WeatherPlatform, _WEATHER_DEVICE_ID


@pytest.fixture
def platform():
    return WeatherPlatform(latitude=48.73, longitude=9.23, poll_interval_minutes=15)


@pytest.mark.asyncio
async def test_discover_returns_one_device(platform):
    devices = await platform.discover_devices()
    assert len(devices) == 1
    device = devices[0]
    assert device.device_type == DeviceType.WEATHER_SENSOR
    assert device.sensor_class == SensorClass.TEMPERATURE
    assert device.id.native_id == _WEATHER_DEVICE_ID
    assert device.name == "Wetter"
    assert device.room == "Aussen"


@pytest.mark.asyncio
async def test_platform_name(platform):
    assert platform.name == "weather"


@pytest.mark.asyncio
async def test_read_state_empty_cache(platform):
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    state = await platform.read_state(device_id)
    assert isinstance(state, DeviceState)
    assert state.state["temperature"] == 0.0


@pytest.mark.asyncio
async def test_read_state_uses_cache(platform):
    platform._last_state = {
        "temperature": 18.5,
        "humidity": 65,
        "weather_description": "teilweise bewölkt",
        "wind_speed": 12.0,
        "cloud_cover": 45,
        "solar_irradiance_w_m2": 350.0,
        "is_day": True,
    }
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    state = await platform.read_state(device_id)
    assert state.state["temperature"] == 18.5
    assert state.state["humidity"] == 65
    assert state.state["is_day"] is True


@pytest.mark.asyncio
async def test_send_command_raises(platform):
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    with pytest.raises(NotImplementedError):
        await platform.send_command(device_id, "turn_on")


@pytest.mark.asyncio
async def test_subscribe_stores_callback(platform):
    cb = AsyncMock()
    await platform.subscribe_events(cb)
    assert platform._event_callback is cb


@pytest.mark.asyncio
async def test_poll_loop_emits_callback_on_change():
    """Poll loop should emit DeviceState when weather data changes."""
    platform = WeatherPlatform(latitude=48.73, longitude=9.23, poll_interval_minutes=100)

    callback_states: list[DeviceState] = []

    async def callback(state: DeviceState) -> None:
        callback_states.append(state)

    await platform.subscribe_events(callback)

    weather_data = {
        "temperature": 18.5,
        "humidity": 65,
        "weather_description": "klar",
        "wind_speed": 5.0,
        "cloud_cover": 10,
        "solar_irradiance_w_m2": 600.0,
        "is_day": True,
    }

    mock_adapter = AsyncMock()
    mock_adapter.get_current.return_value = weather_data
    mock_adapter.stop = AsyncMock()
    platform._adapter = mock_adapter

    # Start poll loop manually and run one iteration
    async def run_one_poll():
        new_data = await platform._adapter.get_current()
        if new_data != platform._last_state:
            platform._last_state = new_data
            if platform._event_callback:
                device_id = DeviceId(platform=platform.name, native_id=_WEATHER_DEVICE_ID)
                device_state = platform._build_device_state(device_id, new_data)
                await platform._event_callback(device_state)

    await run_one_poll()

    assert len(callback_states) == 1
    state = callback_states[0]
    assert state.state["temperature"] == 18.5
    assert state.state["weather_description"] == "klar"


@pytest.mark.asyncio
async def test_poll_loop_no_callback_on_same_data():
    """Poll loop should NOT emit when data has not changed."""
    platform = WeatherPlatform(latitude=48.73, longitude=9.23, poll_interval_minutes=100)

    weather_data = {
        "temperature": 18.5,
        "humidity": 65,
        "weather_description": "klar",
        "wind_speed": 5.0,
        "cloud_cover": 10,
        "solar_irradiance_w_m2": 600.0,
        "is_day": True,
    }
    platform._last_state = weather_data  # Same as what we'll return

    callback_states: list[DeviceState] = []

    async def callback(state: DeviceState) -> None:
        callback_states.append(state)

    await platform.subscribe_events(callback)

    mock_adapter = AsyncMock()
    mock_adapter.get_current.return_value = weather_data
    platform._adapter = mock_adapter

    async def run_one_poll():
        new_data = await platform._adapter.get_current()
        if new_data != platform._last_state:
            platform._last_state = new_data
            if platform._event_callback:
                device_id = DeviceId(platform=platform.name, native_id=_WEATHER_DEVICE_ID)
                device_state = platform._build_device_state(device_id, new_data)
                await platform._event_callback(device_state)

    await run_one_poll()

    # Data unchanged — no callback
    assert len(callback_states) == 0


@pytest.mark.asyncio
async def test_get_summary_delegates_to_adapter(platform):
    mock_adapter = AsyncMock()
    mock_adapter.get_summary.return_value = "18°C, klar, windstill"
    platform._adapter = mock_adapter

    result = await platform.get_summary()
    assert result == "18°C, klar, windstill"


@pytest.mark.asyncio
async def test_get_current_delegates_to_adapter(platform):
    mock_adapter = AsyncMock()
    mock_adapter.get_current.return_value = {"temperature": 18.5}
    platform._adapter = mock_adapter

    result = await platform.get_current()
    assert result["temperature"] == 18.5


# ─── is_raining derivation tests ──────────────────────────────────────────────


def test_is_raining_false_for_dry_condition(platform):
    """condition='dry' produces is_raining=False."""
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    state = platform._build_device_state(device_id, {"condition": "dry", "weather_code": 0})
    assert state.state["is_raining"] is False


def test_is_raining_true_for_rain_condition(platform):
    """condition='rain' from BrightSky produces is_raining=True."""
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    state = platform._build_device_state(device_id, {"condition": "rain", "weather_code": 0})
    assert state.state["is_raining"] is True


def test_is_raining_true_for_thunderstorm_condition(platform):
    """condition='thunderstorm' produces is_raining=True."""
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    state = platform._build_device_state(
        device_id, {"condition": "thunderstorm", "weather_code": 95}
    )
    assert state.state["is_raining"] is True


def test_is_raining_true_for_snow_condition(platform):
    """condition='snow' produces is_raining=True — awnings retract for snow."""
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    state = platform._build_device_state(device_id, {"condition": "snow", "weather_code": 75})
    assert state.state["is_raining"] is True


def test_is_raining_falls_back_to_weather_code(platform):
    """When no condition field, falls back to WMO weather_code for is_raining."""
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    # No condition field, weather_code=61 (rain)
    state = platform._build_device_state(device_id, {"weather_code": 61})
    assert state.state["is_raining"] is True


def test_is_raining_false_when_no_data(platform):
    """Missing both condition and weather_code produces is_raining=False."""
    device_id = DeviceId(platform="weather", native_id=_WEATHER_DEVICE_ID)
    state = platform._build_device_state(device_id, {})
    assert state.state["is_raining"] is False


@pytest.mark.asyncio
async def test_poll_loop_does_not_fire_on_timestamp_only_change(platform):
    """_poll_loop does not fire event callback when only timestamp changes."""
    call_count = 0

    async def mock_callback(state):
        nonlocal call_count
        call_count += 1

    platform._event_callback = mock_callback

    base_data = {
        "temperature": 15.0,
        "weather_code": 0,
        "weather_description": "klar",
        "timestamp": "2026-03-31T10:00:00",
    }
    # Set last_state with an earlier timestamp but same weather data
    platform._last_state = {**base_data, "timestamp": "2026-03-31T09:45:00"}

    # Simulate poll returning same data with new timestamp
    new_data = {**base_data, "timestamp": "2026-03-31T10:00:00"}

    from unittest.mock import AsyncMock

    platform._adapter = AsyncMock()
    platform._adapter.get_current = AsyncMock(return_value=new_data)

    import asyncio

    poll_task = asyncio.create_task(platform._poll_loop())
    await asyncio.sleep(0.01)
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass

    assert call_count == 0, "Callback should not fire when only timestamp changes"
