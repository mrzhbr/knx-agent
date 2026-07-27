from __future__ import annotations

from unittest.mock import patch

from src.config import TrackedDevice, PresenceConfig
from src.integrations.arp_presence import ARPPresenceAdapter


def make_config(devices=None):
    if devices is None:
        devices = {
            "alice": TrackedDevice(ip="10.0.0.50", name="Alice Phone"),
            "bob": TrackedDevice(ip="10.0.0.51", name="Bob Phone"),
        }
    return PresenceConfig(
        poll_interval_seconds=30,
        away_threshold=3,
        tracked_devices=devices,
    )


def test_nobody_home_initially():
    config = make_config()
    adapter = ARPPresenceAdapter(config)
    assert adapter.nobody_home() is True  # No devices yet detected


def test_format_for_prompt_no_devices():
    config = PresenceConfig()
    adapter = ARPPresenceAdapter(config)
    result = adapter.format_for_prompt()
    assert "Keine" in result


async def test_fire_change_no_error_without_callbacks():
    """_fire_change should not raise when no callbacks are registered."""
    config = make_config()
    adapter = ARPPresenceAdapter(config)

    await adapter._fire_change("alice", True)
    # No exception means success.


async def test_fire_change_calls_callbacks():
    """_fire_change should call registered change callbacks."""
    config = make_config()
    adapter = ARPPresenceAdapter(config)

    called_with = []

    async def my_callback(device_id: str, is_home: bool):
        called_with.append((device_id, is_home))

    adapter.on_change(my_callback)
    await adapter._fire_change("alice", False)

    assert called_with == [("alice", False)]


async def test_poll_once_marks_seen():
    """When ping succeeds, device is marked as home."""
    config = make_config(devices={"alice": TrackedDevice(ip="10.0.0.50", name="Alice")})
    adapter = ARPPresenceAdapter(config)
    adapter._presence["alice"] = False  # Start as away

    with (
        patch.object(adapter, "_ping", return_value=True),
        patch("src.integrations.arp_presence._parse_arp_table", return_value={}),
    ):
        await adapter._poll_once()

    assert adapter._presence.get("alice") is True


async def test_poll_once_marks_away_after_threshold():
    """Device is marked away after consecutive misses >= threshold."""
    config_mod = PresenceConfig(
        poll_interval_seconds=30,
        away_threshold=2,
        tracked_devices={"alice": TrackedDevice(ip="10.0.0.50", name="Alice")},
    )
    adapter = ARPPresenceAdapter(config_mod)
    adapter._presence["alice"] = True  # Start as home
    adapter._miss_count["alice"] = 1  # Already 1 miss

    with (
        patch.object(adapter, "_ping", return_value=False),
        patch("src.integrations.arp_presence._parse_arp_table", return_value={}),
    ):
        await adapter._poll_once()

    assert adapter._presence.get("alice") is False
