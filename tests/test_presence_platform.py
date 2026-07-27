"""Tests for PresencePlatform — HAL Platform for presence detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # type: ignore[import-not-found]

from src.hal.models import DeviceId, DeviceState, DeviceType, SensorClass
from src.hal.platforms.presence import PresencePlatform


def _make_config(devices: dict | None = None, backend: str = "arp"):
    """Build a mock PresenceConfig."""
    cfg = MagicMock()
    tracked = {}
    if devices:
        for dev_id, (name, ip) in devices.items():
            d = MagicMock()
            d.name = name
            d.ip = ip
            tracked[dev_id] = d
    cfg.tracked_devices = tracked
    cfg.poll_interval_seconds = 30
    cfg.away_threshold = 3
    cfg.backend = backend
    cfg.owntracks_topic_prefix = "owntracks"
    cfg.owntracks_home_region = "Home"
    cfg.owntracks_geofence_radius_m = 150
    cfg.owntracks_stale_timeout_minutes = 30
    cfg.owntracks_tracked_users = {"person1/iphone": "person1_phone"}
    return cfg


def _make_mqtt_config():
    cfg = MagicMock()
    cfg.host = "localhost"
    cfg.port = 1883
    return cfg


def _make_home_config():
    cfg = MagicMock()
    cfg.latitude = 52.5200
    cfg.longitude = 13.4050
    return cfg


@pytest.fixture
def config():
    return _make_config(
        {
            "person1_iphone": ("Person 1 iPhone", "192.168.1.10"),
            "person2_iphone": ("Person 2 iPhone", "192.168.1.11"),
        }
    )


@pytest.fixture
def platform(config):
    return PresencePlatform(config=config)


@pytest.mark.asyncio
async def test_discover_returns_correct_devices(platform, config):
    devices = await platform.discover_devices()
    assert len(devices) == 2
    assert all(d.device_type == DeviceType.PRESENCE_SENSOR for d in devices)
    assert all(d.sensor_class == SensorClass.PRESENCE for d in devices)
    assert all(d.room == "Haus" for d in devices)


@pytest.mark.asyncio
async def test_discover_device_names_from_config(platform, config):
    devices = await platform.discover_devices()
    names = {d.name for d in devices}
    assert "Person 1 iPhone" in names
    assert "Person 2 iPhone" in names


@pytest.mark.asyncio
async def test_discover_platform_data(platform, config):
    devices = await platform.discover_devices()
    for d in devices:
        assert "ip" in d.platform_data


@pytest.mark.asyncio
async def test_read_state_no_adapter_returns_unknown(platform):
    device_id = DeviceId(platform="presence", native_id="person1_iphone")
    state = await platform.read_state(device_id)
    assert isinstance(state, DeviceState)
    assert state.state["is_home"] is None
    assert not state.state["is_fresh"]


@pytest.mark.asyncio
async def test_read_state_with_adapter(platform):
    mock_adapter = MagicMock()
    mock_adapter.presence_snapshot.return_value = {
        "person1_iphone": True,
        "person2_iphone": False,
    }
    platform._adapter = mock_adapter

    device_id = DeviceId(platform="presence", native_id="person1_iphone")
    state = await platform.read_state(device_id)
    assert state.state["is_home"]

    device_id2 = DeviceId(platform="presence", native_id="person2_iphone")
    state2 = await platform.read_state(device_id2)
    assert not state2.state["is_home"]


@pytest.mark.asyncio
async def test_presence_change_calls_callback(platform):
    callback_states: list[DeviceState] = []

    async def callback(state: DeviceState) -> None:
        callback_states.append(state)

    await platform.subscribe_events(callback)
    await platform._on_presence_change("person1_iphone", True)

    assert len(callback_states) == 1
    state = callback_states[0]
    assert state.device_id.native_id == "person1_iphone"
    assert state.state["is_home"]


@pytest.mark.asyncio
async def test_presence_change_away(platform):
    callback_states: list[DeviceState] = []

    async def callback(state: DeviceState) -> None:
        callback_states.append(state)

    await platform.subscribe_events(callback)
    await platform._on_presence_change("person2_iphone", False)

    assert len(callback_states) == 1
    assert not callback_states[0].state["is_home"]


@pytest.mark.asyncio
async def test_subscribe_stores_callback(platform):
    cb = AsyncMock()
    await platform.subscribe_events(cb)
    assert platform._event_callback is cb


@pytest.mark.asyncio
async def test_send_command_raises(platform):
    device_id = DeviceId(platform="presence", native_id="person1_iphone")
    with pytest.raises(NotImplementedError):
        await platform.send_command(device_id, "turn_on")


@pytest.mark.asyncio
async def test_platform_name(platform):
    assert platform.name == "presence"


# ── Backend selection tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_with_owntracks_backend():
    """When backend='owntracks', OwnTracksAdapter is instantiated."""
    cfg = _make_config(backend="owntracks")
    mqtt_cfg = _make_mqtt_config()
    home_cfg = _make_home_config()
    p = PresencePlatform(config=cfg, mqtt_config=mqtt_cfg, home_config=home_cfg)

    mock_adapter = AsyncMock()
    mock_adapter.on_change = MagicMock()

    with patch("src.hal.platforms.presence.PresencePlatform._start_owntracks") as mock_start:

        async def set_adapter():
            p._adapter = mock_adapter

        mock_start.side_effect = set_adapter
        await p.start()

    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_start_with_arp_backend():
    """When backend='arp', ARPPresenceAdapter is instantiated."""
    cfg = _make_config(backend="arp")
    p = PresencePlatform(config=cfg)

    mock_adapter = AsyncMock()
    mock_adapter.on_change = MagicMock()

    with patch("src.hal.platforms.presence.PresencePlatform._start_arp") as mock_start:

        async def set_adapter():
            p._adapter = mock_adapter

        mock_start.side_effect = set_adapter
        await p.start()

    mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_owntracks_backend_requires_mqtt_config():
    """Starting OwnTracks without mqtt_config raises ValueError (no silent defaults)."""
    cfg = _make_config(backend="owntracks")
    p = PresencePlatform(config=cfg, mqtt_config=None, home_config=_make_home_config())
    with pytest.raises(ValueError, match="mqtt_config"):
        await p._start_owntracks()


@pytest.mark.asyncio
async def test_owntracks_backend_requires_home_config():
    """Starting OwnTracks without home_config raises ValueError (no silent defaults)."""
    cfg = _make_config(backend="owntracks")
    p = PresencePlatform(config=cfg, mqtt_config=_make_mqtt_config(), home_config=None)
    with pytest.raises(ValueError, match="home_config"):
        await p._start_owntracks()


@pytest.mark.asyncio
async def test_owntracks_backend_rejects_placeholder_home_coords():
    """Reject placeholder coordinates rather than reporting people away."""
    cfg = _make_config(backend="owntracks")
    home_cfg = MagicMock()
    home_cfg.latitude = 0.0
    home_cfg.longitude = 0.0
    p = PresencePlatform(config=cfg, mqtt_config=_make_mqtt_config(), home_config=home_cfg)

    with pytest.raises(ValueError, match="non-placeholder home coordinates"):
        await p._start_owntracks()


@pytest.mark.asyncio
async def test_owntracks_backend_requires_tracked_users():
    """Empty owntracks_tracked_users is a fatal misconfig (no silent no-op)."""
    cfg = _make_config(backend="owntracks")
    cfg.owntracks_tracked_users = {}
    p = PresencePlatform(
        config=cfg, mqtt_config=_make_mqtt_config(), home_config=_make_home_config()
    )
    with pytest.raises(ValueError, match="owntracks_tracked_users"):
        await p._start_owntracks()


@pytest.mark.asyncio
async def test_start_with_invalid_backend_raises():
    """An unknown backend value raises ValueError instead of silently using ARP."""
    cfg = _make_config(backend="owntrack")  # typo
    p = PresencePlatform(config=cfg)
    with pytest.raises(ValueError, match="Unsupported presence backend"):
        await p.start()


@pytest.mark.asyncio
async def test_health_check_delegates_to_adapter():
    """health_check delegates to the adapter's health_check."""
    cfg = _make_config()
    p = PresencePlatform(config=cfg)

    mock_adapter = AsyncMock()
    mock_adapter.health_check = AsyncMock(return_value=True)
    p._adapter = mock_adapter

    result = await p.health_check()
    assert result
    mock_adapter.health_check.assert_called_once()


@pytest.mark.asyncio
async def test_health_check_returns_false_without_adapter():
    """health_check returns False if no adapter is set."""
    cfg = _make_config()
    p = PresencePlatform(config=cfg)
    result = await p.health_check()
    assert not result


@pytest.mark.asyncio
async def test_discover_devices_owntracks_platform_data():
    """OwnTracks backend includes 'backend' key in platform_data."""
    cfg = _make_config(
        devices={"person1_phone": ("Person 1", "192.0.2.50")},
        backend="owntracks",
    )
    p = PresencePlatform(config=cfg)
    devices = await p.discover_devices()
    assert len(devices) == 1
    assert devices[0].platform_data.get("backend") == "owntracks"
    assert "ip" not in devices[0].platform_data


@pytest.mark.asyncio
async def test_discover_devices_owntracks_uses_owntracks_tracked_users():
    """OwnTracks discovery iterates owntracks_tracked_users, not tracked_devices.

    A user can configure OwnTracks without populating the legacy tracked_devices
    dict at all — discovery must still produce one DeviceInfo per mapped user.
    Names fall back to the dev_id when tracked_devices has no entry.
    """
    cfg = _make_config(backend="owntracks")  # tracked_devices empty
    cfg.owntracks_tracked_users = {
        "person1/iphone": "person1_phone",
        "person2/iphone": "person2_phone",
    }
    p = PresencePlatform(config=cfg)
    devices = await p.discover_devices()
    native_ids = {d.id.native_id for d in devices}
    assert native_ids == {"person1_phone", "person2_phone"}
    assert all(d.platform_data.get("backend") == "owntracks" for d in devices)
    # No tracked_devices entry → name falls back to native_id
    person1 = next(d for d in devices if d.id.native_id == "person1_phone")
    assert person1.name == "person1_phone"


@pytest.mark.asyncio
async def test_discover_devices_arp_platform_data():
    """ARP backend includes 'ip' key in platform_data."""
    cfg = _make_config(
        devices={"person1_phone": ("Person 1", "192.0.2.50")},
        backend="arp",
    )
    p = PresencePlatform(config=cfg)
    devices = await p.discover_devices()
    assert len(devices) == 1
    assert "ip" in devices[0].platform_data
    assert "backend" not in devices[0].platform_data
