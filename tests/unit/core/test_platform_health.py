"""Unit tests for PlatformHealthMonitor — polling, status tracking, lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.platform_health import PlatformHealthMonitor
from src.core.types import HealthState, HealthStatus


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_hal():
    """Create a mock HAL with no platforms."""
    hal = MagicMock()
    hal._platforms = {}
    hal.iter_platforms.return_value = {}
    return hal


@pytest.fixture
def mock_platform():
    """Create a mock platform with async health_check and error_message."""
    platform = MagicMock()
    platform.health_check = AsyncMock(return_value=True)
    platform.error_message = "⚠ Mock platform ist offline."
    return platform


@pytest.fixture
def monitor(mock_hal):
    """Create a PlatformHealthMonitor with mocked HAL."""
    return PlatformHealthMonitor(hal=mock_hal)


# ─── Initialization Tests ──────────────────────────────────────────────────


def test_initialization_with_hal_reference(mock_hal):
    """Monitor is created with HAL reference stored."""
    monitor = PlatformHealthMonitor(hal=mock_hal)
    assert monitor._hal is mock_hal


def test_initial_state_is_empty(mock_hal):
    """Initial state has no statuses."""
    monitor = PlatformHealthMonitor(hal=mock_hal)
    assert monitor._statuses == {}
    assert monitor.get_all_statuses() == {}


# ─── Polling Logic Tests ─────────────────────────────────────────────────────


async def test_check_all_platforms_calls_health_check(mock_hal, mock_platform):
    """_check_all_platforms calls health_check() on all platforms."""
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    await monitor._check_all_platforms()

    mock_platform.health_check.assert_awaited_once()


async def test_check_all_platforms_stores_healthy_status(mock_hal, mock_platform):
    """Healthy platform stores HEALTHY status."""
    mock_platform.health_check.return_value = True
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    await monitor._check_all_platforms()

    status = monitor.get_status("frigate")
    assert status is not None
    assert status.state == HealthState.HEALTHY
    assert status.platform_name == "frigate"
    assert status.last_error is None


async def test_check_all_platforms_stores_unhealthy_status(mock_hal, mock_platform):
    """Unhealthy platform (returns False) stores UNHEALTHY status."""
    mock_platform.health_check.return_value = False
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    await monitor._check_all_platforms()

    status = monitor.get_status("frigate")
    assert status is not None
    assert status.state == HealthState.UNHEALTHY
    assert status.last_error == "Health check returned False"


async def test_check_all_platforms_handles_exception(mock_hal, mock_platform):
    """Platform raising exception stores UNHEALTHY status with error message."""
    mock_platform.health_check.side_effect = Exception("Connection timeout")
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    await monitor._check_all_platforms()

    status = monitor.get_status("frigate")
    assert status is not None
    assert status.state == HealthState.UNHEALTHY
    assert status.last_error == "Connection timeout"


async def test_check_all_platforms_multiple_platforms(mock_hal):
    """Multiple platforms are all checked and statuses stored."""
    frigate = MagicMock()
    frigate.health_check = AsyncMock(return_value=True)

    gira = MagicMock()
    gira.health_check = AsyncMock(return_value=False)

    weather = MagicMock()
    weather.health_check = AsyncMock(side_effect=Exception("API down"))

    mock_hal._platforms = {
        "frigate": frigate,
        "gira_x1": gira,
        "weather": weather,
    }
    mock_hal.iter_platforms.return_value = {
        "frigate": frigate,
        "gira_x1": gira,
        "weather": weather,
    }
    monitor = PlatformHealthMonitor(hal=mock_hal)

    await monitor._check_all_platforms()

    assert monitor.get_status("frigate").state == HealthState.HEALTHY
    assert monitor.get_status("gira_x1").state == HealthState.UNHEALTHY
    assert monitor.get_status("weather").state == HealthState.UNHEALTHY
    assert monitor.get_status("weather").last_error == "API down"


async def test_check_all_platforms_no_hal():
    """_check_all_platforms returns early if HAL is None."""
    monitor = PlatformHealthMonitor(hal=None)
    # Should not raise
    await monitor._check_all_platforms()
    assert monitor._statuses == {}


async def test_check_all_platforms_empty_platforms(mock_hal):
    """_check_all_platforms handles empty platforms dict."""
    mock_hal._platforms = {}
    mock_hal.iter_platforms.return_value = {}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    await monitor._check_all_platforms()

    assert monitor._statuses == {}


# ─── Getter Methods Tests ────────────────────────────────────────────────────


async def test_get_status_returns_correct_status(mock_hal, mock_platform):
    """get_status returns correct HealthStatus for known platform."""
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    status = monitor.get_status("frigate")

    assert isinstance(status, HealthStatus)
    assert status.platform_name == "frigate"


async def test_get_status_returns_none_for_unknown_platform(mock_hal, mock_platform):
    """get_status returns None for unknown platform."""
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    status = monitor.get_status("unknown_platform")

    assert status is None


async def test_get_all_statuses_returns_all(mock_hal):
    """get_all_statuses returns dict with all platform statuses."""
    p1 = MagicMock()
    p1.health_check = AsyncMock(return_value=True)
    p2 = MagicMock()
    p2.health_check = AsyncMock(return_value=True)

    mock_hal._platforms = {"platform_a": p1, "platform_b": p2}
    mock_hal.iter_platforms.return_value = {"platform_a": p1, "platform_b": p2}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    all_statuses = monitor.get_all_statuses()

    assert len(all_statuses) == 2
    assert "platform_a" in all_statuses
    assert "platform_b" in all_statuses
    assert all_statuses["platform_a"].state == HealthState.HEALTHY


async def test_get_all_statuses_returns_copy(mock_hal, mock_platform):
    """get_all_statuses returns a copy, not the internal dict."""
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    all_statuses = monitor.get_all_statuses()
    all_statuses["new_key"] = "should_not_affect_internal"

    assert "new_key" not in monitor._statuses


async def test_get_unhealthy_platforms_returns_only_unhealthy(mock_hal):
    """get_unhealthy_platforms returns only platforms with non-HEALTHY state."""
    healthy = MagicMock()
    healthy.health_check = AsyncMock(return_value=True)

    unhealthy = MagicMock()
    unhealthy.health_check = AsyncMock(return_value=False)

    error_platform = MagicMock()
    error_platform.health_check = AsyncMock(side_effect=Exception("Error"))

    mock_hal._platforms = {
        "healthy": healthy,
        "unhealthy": unhealthy,
        "error": error_platform,
    }
    mock_hal.iter_platforms.return_value = {
        "healthy": healthy,
        "unhealthy": unhealthy,
        "error": error_platform,
    }
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    unhealthy_list = monitor.get_unhealthy_platforms()

    assert len(unhealthy_list) == 2
    names = [s.platform_name for s in unhealthy_list]
    assert "unhealthy" in names
    assert "error" in names
    assert "healthy" not in names


async def test_get_unhealthy_platforms_empty_when_all_healthy(mock_hal):
    """get_unhealthy_platforms returns empty list when all platforms healthy."""
    p1 = MagicMock()
    p1.health_check = AsyncMock(return_value=True)
    p2 = MagicMock()
    p2.health_check = AsyncMock(return_value=True)

    mock_hal._platforms = {"p1": p1, "p2": p2}
    mock_hal.iter_platforms.return_value = {"p1": p1, "p2": p2}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    unhealthy_list = monitor.get_unhealthy_platforms()

    assert unhealthy_list == []


# ─── Formatting Tests ────────────────────────────────────────────────────────


async def test_format_unhealthy_returns_none_when_all_healthy(mock_hal):
    """format_unhealthy_for_prompt returns None when all platforms healthy."""
    p1 = MagicMock()
    p1.health_check = AsyncMock(return_value=True)

    mock_hal._platforms = {"frigate": p1}
    mock_hal.iter_platforms.return_value = {"frigate": p1}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert result is None


async def test_format_unhealthy_returns_formatted_string(mock_hal):
    """format_unhealthy_for_prompt returns correctly formatted German string."""
    unhealthy = MagicMock()
    unhealthy.health_check = AsyncMock(return_value=False)
    unhealthy.error_message = "⚠ Test Platform ist offline."

    mock_hal._platforms = {"frigate": unhealthy}
    mock_hal.iter_platforms.return_value = {"frigate": unhealthy}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert result is not None
    assert "ist offline" in result
    assert "⚠" in result


async def test_format_unhealthy_frigate_message(mock_hal):
    """Frigate has specific German message about camera data."""
    frigate = MagicMock()
    frigate.health_check = AsyncMock(return_value=False)
    frigate.error_message = "⚠ Frigate ist offline — keine Kameradaten verfügbar."

    mock_hal._platforms = {"frigate": frigate}
    mock_hal.iter_platforms.return_value = {"frigate": frigate}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "Frigate" in result
    assert "keine Kameradaten verfügbar" in result


async def test_format_unhealthy_gira_x1_message(mock_hal):
    """Gira X1 has specific German message about device control."""
    gira = MagicMock()
    gira.health_check = AsyncMock(return_value=False)
    gira.error_message = "⚠ Gira X1 ist offline — keine Gerätesteuerung möglich."

    mock_hal._platforms = {"gira_x1": gira}
    mock_hal.iter_platforms.return_value = {"gira_x1": gira}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "Gira X1" in result
    assert "keine Gerätesteuerung möglich" in result


async def test_format_unhealthy_weather_message(mock_hal):
    """Weather has specific German message about weather data."""
    weather = MagicMock()
    weather.health_check = AsyncMock(return_value=False)
    weather.error_message = "⚠ Wetterdienst ist offline — keine aktuellen Wetterdaten."

    mock_hal._platforms = {"weather": weather}
    mock_hal.iter_platforms.return_value = {"weather": weather}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "Wetterdienst" in result
    assert "keine aktuellen Wetterdaten" in result


async def test_format_unhealthy_calendar_message(mock_hal):
    """Calendar has specific German message about calendar data."""
    calendar = MagicMock()
    calendar.health_check = AsyncMock(return_value=False)
    calendar.error_message = "⚠ Kalender ist offline — keine Kalenderdaten verfügbar."

    mock_hal._platforms = {"calendar": calendar}
    mock_hal.iter_platforms.return_value = {"calendar": calendar}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "Kalender" in result
    assert "keine Kalenderdaten verfügbar" in result


async def test_format_unhealthy_presence_message(mock_hal):
    """Presence has specific German message about presence detection."""
    presence = MagicMock()
    presence.health_check = AsyncMock(return_value=False)
    presence.error_message = "⚠ Anwesenheitserkennung ist offline — Anwesenheit nicht erkannt."

    mock_hal._platforms = {"presence": presence}
    mock_hal.iter_platforms.return_value = {"presence": presence}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "Anwesenheitserkennung" in result
    assert "Anwesenheit nicht erkannt" in result


async def test_format_unhealthy_mitsubishi_message(mock_hal):
    """Mitsubishi uses generic offline message."""
    mitsubishi = MagicMock()
    mitsubishi.health_check = AsyncMock(return_value=False)
    mitsubishi.error_message = "⚠ Mitsubishi AC ist offline."

    mock_hal._platforms = {"mitsubishi": mitsubishi}
    mock_hal.iter_platforms.return_value = {"mitsubishi": mitsubishi}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "Mitsubishi AC" in result
    assert "ist offline" in result


async def test_format_unhealthy_door_station_message(mock_hal):
    """Door station uses generic offline message."""
    door = MagicMock()
    door.health_check = AsyncMock(return_value=False)
    door.error_message = "⚠ Türstation ist offline."

    mock_hal._platforms = {"door_station": door}
    mock_hal.iter_platforms.return_value = {"door_station": door}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "Türstation" in result
    assert "ist offline" in result


async def test_format_unhealthy_unknown_platform(mock_hal):
    """Unknown platform uses platform_name directly with generic message."""
    unknown = MagicMock()
    unknown.health_check = AsyncMock(return_value=False)
    unknown.error_message = "⚠ custom_platform ist offline."

    mock_hal._platforms = {"custom_platform": unknown}
    mock_hal.iter_platforms.return_value = {"custom_platform": unknown}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "custom_platform" in result
    assert "ist offline" in result


async def test_format_unhealthy_multiple_platforms(mock_hal):
    """Multiple unhealthy platforms are formatted with newlines."""
    frigate = MagicMock()
    frigate.health_check = AsyncMock(return_value=False)
    frigate.error_message = "⚠ Frigate ist offline — keine Kameradaten verfügbar."

    weather = MagicMock()
    weather.health_check = AsyncMock(return_value=False)
    weather.error_message = "⚠ Wetterdienst ist offline — keine aktuellen Wetterdaten."

    mock_hal._platforms = {"frigate": frigate, "weather": weather}
    mock_hal.iter_platforms.return_value = {"frigate": frigate, "weather": weather}
    monitor = PlatformHealthMonitor(hal=mock_hal)
    await monitor._check_all_platforms()

    result = monitor.format_unhealthy_for_prompt()

    assert "\n" in result
    assert "Frigate" in result
    assert "Wetterdienst" in result


# ─── Lifecycle Tests ───────────────────────────────────────────────────────


async def test_start_creates_asyncio_task(monitor):
    """start() creates an asyncio task."""
    monitor.start()

    assert monitor._task is not None
    assert isinstance(monitor._task, asyncio.Task)
    assert not monitor._task.done()

    monitor.stop()
    await asyncio.sleep(0)  # Allow cancellation to propagate


async def test_stop_cancels_task(monitor):
    """stop() cancels the running task."""
    monitor.start()
    await asyncio.sleep(0)  # Let task start

    monitor.stop()
    await asyncio.sleep(0.1)  # Allow cancellation to propagate

    assert monitor._task is None or monitor._task.done() or monitor._task.cancelled()


async def test_stop_sets_stop_event(monitor):
    """stop() sets the stop event."""
    assert not monitor._stop_event.is_set()

    monitor.stop()

    assert monitor._stop_event.is_set()


async def test_multiple_start_stop_cycles(mock_hal):
    """Multiple start/stop cycles work correctly."""
    monitor = PlatformHealthMonitor(hal=mock_hal)

    # First cycle
    monitor.start()
    assert monitor._task is not None
    monitor.stop()
    await asyncio.sleep(0.05)

    # Reset for second cycle
    monitor._stop_event.clear()

    # Second cycle
    monitor.start()
    assert monitor._task is not None
    monitor.stop()
    await asyncio.sleep(0.05)

    # Should be stopped
    assert monitor._stop_event.is_set()


async def test_stop_without_start_does_not_raise(mock_hal):
    """stop() can be called without start() without raising."""
    monitor = PlatformHealthMonitor(hal=mock_hal)

    # Should not raise
    monitor.stop()

    assert monitor._stop_event.is_set()


# ─── Main Loop (_run) Tests ─────────────────────────────────────────────────


async def test_run_calls_check_all_platforms_immediately(mock_hal, mock_platform):
    """_run calls _check_all_platforms immediately on start."""
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    # Start and stop quickly to trigger initial check
    monitor.start()
    await asyncio.sleep(0.05)  # Let initial check run
    monitor.stop()
    await asyncio.sleep(0)

    mock_platform.health_check.assert_awaited()


async def test_run_respects_stop_event(mock_hal):
    """_run exits when stop event is set."""
    monitor = PlatformHealthMonitor(hal=mock_hal)

    monitor.start()
    await asyncio.sleep(0.05)

    monitor.stop()
    await asyncio.sleep(0.1)

    # Task should be done or cancelled
    assert monitor._task is None or monitor._task.done() or monitor._task.cancelled()


# ─── State Change Logging Tests ──────────────────────────────────────────────


async def test_state_change_healthy_to_unhealthy_logged(mock_hal, mock_platform, caplog):
    """State change from healthy to unhealthy is logged."""
    mock_platform.health_check.return_value = True
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    # First check - healthy
    await monitor._check_all_platforms()

    # Change to unhealthy
    mock_platform.health_check.return_value = False

    # Second check - should log state change
    with caplog.at_level("WARNING"):
        await monitor._check_all_platforms()

    assert "now unhealthy" in caplog.text or "Health check returned False" in caplog.text


async def test_state_change_unhealthy_to_healthy_logged(mock_hal, mock_platform, caplog):
    """State change from unhealthy to healthy is logged."""
    mock_platform.health_check.return_value = False
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    # First check - unhealthy
    await monitor._check_all_platforms()

    # Change to healthy
    mock_platform.health_check.return_value = True

    # Second check - should log state change
    with caplog.at_level("INFO"):
        await monitor._check_all_platforms()

    assert "now healthy" in caplog.text


async def test_no_log_when_state_unchanged(mock_hal, mock_platform, caplog):
    """No state change log when status stays the same."""
    mock_platform.health_check.return_value = True
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    # First check
    await monitor._check_all_platforms()

    # Second check - same state
    with caplog.at_level("INFO"):
        caplog.clear()
        await monitor._check_all_platforms()

    # Should not log state change
    assert "now healthy" not in caplog.text
    assert "now unhealthy" not in caplog.text


# ─── Timestamp Tests ─────────────────────────────────────────────────────────


async def test_status_includes_timestamp(mock_hal, mock_platform):
    """HealthStatus includes last_checked timestamp."""
    mock_hal._platforms = {"frigate": mock_platform}
    mock_hal.iter_platforms.return_value = {"frigate": mock_platform}
    monitor = PlatformHealthMonitor(hal=mock_hal)

    before = datetime.now()
    await monitor._check_all_platforms()
    after = datetime.now()

    status = monitor.get_status("frigate")
    assert status.last_checked is not None
    assert before <= status.last_checked <= after
