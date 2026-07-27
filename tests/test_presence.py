from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.config import TrackedDevice, PresenceConfig
from src.core.presence import PresenceManager
from src.hal.models import SensorClass
from src.integrations.arp_presence import ARPPresenceAdapter


def make_arp_adapter(home_devices=None):
    """Create an ARP adapter with preset presence state."""
    config = PresenceConfig(
        tracked_devices={
            "alice": TrackedDevice(ip="10.0.0.50", name="Alice"),
            "bob": TrackedDevice(ip="10.0.0.51", name="Bob"),
        }
    )
    adapter = ARPPresenceAdapter(config)
    if home_devices:
        for dev_id in home_devices:
            adapter._presence[dev_id] = True
        for dev_id in config.tracked_devices:
            if dev_id not in home_devices:
                adapter._presence[dev_id] = False
    else:
        # All away
        for dev_id in config.tracked_devices:
            adapter._presence[dev_id] = False
    return adapter


def test_nobody_home_when_all_away():
    arp = make_arp_adapter()
    pm = PresenceManager(arp)
    assert pm.nobody_home()


def test_someone_home():
    arp = make_arp_adapter(home_devices=["alice"])
    pm = PresenceManager(arp)
    assert not pm.nobody_home()


def test_open_windows_no_registry():
    arp = make_arp_adapter()
    pm = PresenceManager(arp)
    assert pm.open_windows == []


def test_open_windows_with_registry():
    arp = make_arp_adapter()

    # Mock house state and registry
    # After HAL migration, _open_windows is a sync cache populated by connect_hal().
    # Simulate: inject into cache directly (as if connect_hal() has run).
    pm = PresenceManager(arp)
    pm._open_windows = ["Schlafzimmer"]  # Simulate open window cached from HAL
    open_windows = pm.has_open_windows()
    assert "Schlafzimmer" in open_windows


def _window_info(device_key: str, name: str = "Fenster") -> MagicMock:
    info = MagicMock()
    info.sensor_class = SensorClass.WINDOW
    info.id.key = device_key
    info.name = name
    info.room = "Schlafzimmer"
    return info


async def test_window_cache_tracks_each_sensor_independently():
    pm = PresenceManager()
    first = _window_info("window/one", "Fenster links")
    second = _window_info("window/two", "Fenster rechts")

    await pm._on_window_state_change(first, None, {"Binary": "1"}, "test")
    await pm._on_window_state_change(second, None, {"Binary": "1"}, "test")
    await pm._on_window_state_change(first, {"Binary": "1"}, {"Binary": "0"}, "test")

    assert pm.window_state_known
    assert len(pm.open_windows) == 1
    assert "Fenster rechts" in pm.open_windows[0]


async def test_missing_window_value_is_unknown_and_unsafe():
    pm = PresenceManager()
    await pm._on_window_state_change(_window_info("window/one"), None, {}, "test")

    assert not pm.window_state_known
    assert not pm.windows_safe_for_alarm


async def test_async_nobody_home_denies_unknown_presence():
    adapter = MagicMock()
    adapter.info.name = "Person phone"
    adapter.read_state = AsyncMock(
        return_value=SimpleNamespace(state={"is_home": None, "is_fresh": False})
    )
    hal = MagicMock()
    hal.get_devices_by_type.return_value = [adapter]
    pm = PresenceManager(hal)

    assert not await pm.async_nobody_home()


async def test_async_nobody_home_allows_fresh_away_state():
    adapter = MagicMock()
    adapter.info.name = "Person phone"
    adapter.read_state = AsyncMock(
        return_value=SimpleNamespace(state={"is_home": False, "is_fresh": True})
    )
    hal = MagicMock()
    hal.get_devices_by_type.return_value = [adapter]
    pm = PresenceManager(hal)

    assert await pm.async_nobody_home()


def test_camera_zone_update_and_retrieval():
    arp = make_arp_adapter()
    pm = PresenceManager(arp)

    pm.update_camera_zone("entrance", ["entrance_zone"])
    active = pm.get_entrance_activity()
    assert "entrance" in active


def test_camera_zone_expires():
    arp = make_arp_adapter()
    pm = PresenceManager(arp)

    pm.update_camera_zone("entrance", ["zone"])
    # Manually expire it
    pm._camera_zones["entrance"]["timestamp"] = datetime.now() - timedelta(minutes=10)

    active = pm.get_entrance_activity()
    assert "entrance" not in active


def test_format_for_prompt_fused():
    arp = make_arp_adapter(home_devices=["alice"])
    pm = PresenceManager(arp)
    pm.update_camera_zone("entrance", ["eingang"])

    result = pm.format_for_prompt()
    assert "Alice" in result
    assert "entrance" in result
