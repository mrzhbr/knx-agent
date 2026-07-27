"""Tests for CalDAV calendar integration (Task 17.1)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


from src.config import CalDAVConfig


def make_config(**kwargs):
    defaults = {"url": "", "username": "", "password": "", "calendar_name": ""}
    defaults.update(kwargs)
    return CalDAVConfig(**defaults)


# ─── CalDAVAdapter tests ──────────────────────────────────────────────────────


async def test_empty_config_returns_empty_events():
    """CalDAVAdapter with empty URL returns empty event list without error."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config()  # Empty URL
    adapter = CalDAVAdapter(config)
    await adapter.start()
    events = await adapter.get_upcoming_events()
    assert events == []


async def test_empty_config_health_check_returns_true():
    """CalDAVAdapter with no config returns True (not configured = not failed)."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config()
    adapter = CalDAVAdapter(config)
    await adapter.start()
    result = await adapter.health_check()
    assert result is True


async def test_caldav_not_installed_returns_empty_gracefully():
    """CalDAVAdapter returns empty list gracefully when caldav library not installed."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config(url="https://nextcloud.local/dav", username="user", password="pass")
    adapter = CalDAVAdapter(config)

    # Mock caldav not installed
    with patch.dict("sys.modules", {"caldav": None}):
        await adapter.start()
        events = await adapter.get_upcoming_events()
        assert events == []


async def test_cache_prevents_redundant_fetches():
    """CalDAVAdapter uses cache for repeated calls within TTL."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config(url="https://nextcloud.local/dav", username="user", password="pass")
    adapter = CalDAVAdapter(config)

    # Seed the cache directly
    now = datetime.now()
    adapter._cache = [
        {"summary": "Meeting", "start": "14:00", "end": "15:00", "location": "", "description": ""}
    ]
    adapter._cache_time = now  # Cache is fresh
    adapter._client = MagicMock()  # Not None (so URL check passes)

    # Fetch returns from cache
    events = await adapter.get_upcoming_events()
    assert len(events) == 1
    assert events[0]["summary"] == "Meeting"


async def test_stale_cache_returned_on_api_failure():
    """CalDAVAdapter returns stale cache when API call fails."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config(url="https://nextcloud.local/dav", username="user", password="pass")
    adapter = CalDAVAdapter(config)

    # Seed stale cache
    stale_time = datetime.now() - timedelta(hours=1)  # Expired
    adapter._cache = [
        {
            "summary": "Old Event",
            "start": "10:00",
            "end": "11:00",
            "location": "",
            "description": "",
        }
    ]
    adapter._cache_time = stale_time
    adapter._client = MagicMock()

    # _fetch_events will fail (client mock raises)
    with patch.object(adapter, "_fetch_events", side_effect=Exception("Network error")):
        events = await adapter.get_upcoming_events()

    # Should return stale cache
    assert len(events) == 1
    assert events[0]["summary"] == "Old Event"


def test_format_for_prompt_empty_cache():
    """format_for_prompt returns empty string when no events."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config()
    adapter = CalDAVAdapter(config)
    adapter._cache = []

    result = adapter.format_for_prompt()
    assert result == ""


def test_format_for_prompt_with_events():
    """format_for_prompt formats events in German."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config()
    adapter = CalDAVAdapter(config)
    adapter._cache = [
        {
            "summary": "Zahnarzt",
            "start": "14:00",
            "end": "15:00",
            "location": "Praxis",
            "description": "",
        },
        {
            "summary": "Elternabend",
            "start": "18:00",
            "end": "20:00",
            "location": "",
            "description": "",
        },
    ]

    result = adapter.format_for_prompt()
    assert "14:00" in result
    assert "Zahnarzt" in result
    assert "18:00" in result
    assert "Elternabend" in result
    assert "Heute" in result


def test_format_for_prompt_none_cache():
    """format_for_prompt returns empty string when cache is None."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config()
    adapter = CalDAVAdapter(config)
    adapter._cache = None

    result = adapter.format_for_prompt()
    assert result == ""


async def test_caldav_config_added_to_brain_config():
    """BrainConfig has a caldav field of type CalDAVConfig."""
    from src.config import BrainConfig, ModelsConfig, ModelSlotConfig

    _slot = ModelSlotConfig(base_url="http://test", api_key_env="TEST_KEY", model_id="test")
    config = BrainConfig(models=ModelsConfig(router=_slot, specialists=_slot, reflection=_slot))
    assert hasattr(config, "caldav")
    assert isinstance(config.caldav, CalDAVConfig)
    assert config.caldav.url == ""


async def test_start_with_unconfigured_url_does_not_raise():
    """CalDAVAdapter.start() with empty URL does not raise any exception."""
    from src.integrations.calendar import CalDAVAdapter

    config = make_config()  # Empty URL
    adapter = CalDAVAdapter(config)
    # Must not raise
    await adapter.start()
    await adapter.stop()
