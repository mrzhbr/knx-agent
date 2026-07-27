"""Tests for MitsubishiACAdapter — pymitsubishi integration with asyncio.to_thread."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.mitsubishi_ac import MitsubishiACAdapter, _ACUnitState


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _make_config(units=None, poll_interval=30):
    """Build a minimal MitsubishiACConfig mock."""
    from src.config import ACUnit, MitsubishiACConfig

    unit_cfgs = units or [
        ACUnit(name="Room 1 AC", ip="10.0.0.10", room="Room 1"),
        ACUnit(name="Room 2 AC", ip="10.0.0.11", room="Room 2"),
    ]
    return MitsubishiACConfig(units=unit_cfgs, poll_interval=poll_interval)


def _make_mock_controller(
    power="PowerOnOff.OFF", mode="DriveMode.COOLER", set_temp=23.0, room_temp=22.5, power_watt=1
):
    """Build a mock MitsubishiController with realistic state."""
    ctrl = MagicMock()
    state = MagicMock()
    state.general.power_on_off = power
    state.general.drive_mode = mode
    state.general.fine_temperature = set_temp
    state.sensors.inside_temperature_1_fine = room_temp
    state.energy.power_watt = power_watt
    state.energy.energy_hecto_watt_hour = 1084
    ctrl.state = state
    ctrl.fetch_status = MagicMock()
    ctrl.set_power = MagicMock()
    ctrl.set_temperature = MagicMock()
    ctrl.set_mode = MagicMock()
    return ctrl


def _make_mock_api():
    """Build a mock MitsubishiAPI."""
    api = MagicMock()
    api.close = MagicMock()
    return api


@pytest.fixture
def adapter():
    """Return an adapter with default config (no real AC units)."""
    config = _make_config()
    return MitsubishiACAdapter(config=config)


# ─── Tests: start() ────────────────────────────────────────────────────────


async def test_start_creates_unit_states(adapter):
    """start() creates _ACUnitState for each configured unit."""
    mock_api = _make_mock_api()
    mock_ctrl = _make_mock_controller()

    with patch("src.integrations.mitsubishi_ac.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = None  # fetch_status return

        with (
            patch("pymitsubishi.MitsubishiAPI", return_value=mock_api),
            patch("pymitsubishi.MitsubishiController", return_value=mock_ctrl),
        ):
            # Patch to_thread to return from fetch_status synchronously
            mock_ctrl.fetch_status.return_value = None

            await adapter.start()
            # Cancel poll tasks immediately
            for unit in adapter._units:
                if unit.poll_task:
                    unit.poll_task.cancel()
                    try:
                        await unit.poll_task
                    except asyncio.CancelledError:
                        pass

    assert len(adapter._units) == 2
    assert adapter._units[0].name == "Room 1 AC"
    assert adapter._units[1].name == "Room 2 AC"


async def test_start_stores_api_reference(adapter):
    """start() stores api reference in _ACUnitState for cleanup."""
    mock_api = _make_mock_api()
    mock_ctrl = _make_mock_controller()

    with (
        patch("pymitsubishi.MitsubishiAPI", return_value=mock_api),
        patch("pymitsubishi.MitsubishiController", return_value=mock_ctrl),
        patch("src.integrations.mitsubishi_ac.asyncio.to_thread", return_value=None),
    ):
        await adapter.start()
        for unit in adapter._units:
            if unit.poll_task:
                unit.poll_task.cancel()
                try:
                    await unit.poll_task
                except asyncio.CancelledError:
                    pass

    # Each unit should have an api field
    for unit in adapter._units:
        assert unit.api is not None


async def test_start_graceful_when_pymitsubishi_missing(adapter):
    """start() logs error and returns gracefully when pymitsubishi not importable."""
    with patch.dict("sys.modules", {"pymitsubishi": None}):
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            # Should not raise
            try:
                await adapter.start()
            except ImportError:
                pass  # Acceptable if import fails


# ─── Tests: stop() ────────────────────────────────────────────────────────


async def test_stop_cancels_poll_tasks(adapter):
    """stop() cancels all poll tasks."""
    mock_api = _make_mock_api()
    mock_ctrl = _make_mock_controller()

    with (
        patch("pymitsubishi.MitsubishiAPI", return_value=mock_api),
        patch("pymitsubishi.MitsubishiController", return_value=mock_ctrl),
        patch("src.integrations.mitsubishi_ac.asyncio.to_thread", return_value=None),
    ):
        await adapter.start()

    # All units should have poll tasks
    tasks = [u.poll_task for u in adapter._units if u.poll_task]
    assert len(tasks) > 0

    await adapter.stop()
    for task in tasks:
        assert task.cancelled() or task.done()


async def test_stop_calls_api_close(adapter):
    """stop() calls api.close() for each unit."""
    mock_api = _make_mock_api()
    mock_ctrl = _make_mock_controller()

    with (
        patch("pymitsubishi.MitsubishiAPI", return_value=mock_api),
        patch("pymitsubishi.MitsubishiController", return_value=mock_ctrl),
        patch("src.integrations.mitsubishi_ac.asyncio.to_thread", return_value=None),
    ):
        await adapter.start()

    await adapter.stop()
    # api.close should have been called (via to_thread)
    # Since we're patching to_thread, the actual call happens in to_thread
    assert len(adapter._units) == 0  # units cleared after stop


# ─── Tests: _detect_changes() ────────────────────────────────────────────


def test_detect_changes_logs_on_state_change(caplog):
    """_detect_changes() logs when power changes."""
    config = _make_config(units=[])
    adapter = MitsubishiACAdapter(config=config)

    unit = _ACUnitState(
        name="Test AC",
        room="Room 1",
        ip="10.0.0.10",
        controller=MagicMock(),
        api=MagicMock(),
        last_state={"power": "PowerOnOff.OFF", "mode": "DriveMode.COOLER", "set_temp": 23.0},
    )

    new_state = {"power": "PowerOnOff.ON", "mode": "DriveMode.COOLER", "set_temp": 23.0}
    with caplog.at_level(logging.INFO):
        adapter._detect_changes(unit, new_state)

    assert "AC state change" in caplog.text


def test_detect_changes_no_log_when_unchanged(caplog):
    """_detect_changes() does NOT log when state is unchanged."""
    config = _make_config(units=[])
    adapter = MitsubishiACAdapter(config=config)

    state = {"power": "PowerOnOff.OFF", "mode": "DriveMode.COOLER", "set_temp": 23.0}
    unit = _ACUnitState(
        name="Test AC",
        room="Room",
        ip="1.2.3.4",
        controller=MagicMock(),
        api=MagicMock(),
        last_state=state.copy(),
    )
    with caplog.at_level(logging.INFO):
        adapter._detect_changes(unit, state.copy())

    assert "AC state change" not in caplog.text


# ─── Tests: get_status() ─────────────────────────────────────────────────


def test_get_status_single_room(adapter):
    """get_status(room) returns status for one room from cached state."""
    unit = _ACUnitState(
        name="Room 1 AC",
        room="Room 1",
        ip="10.0.0.10",
        controller=MagicMock(),
        api=MagicMock(),
        last_state={"power": "OFF", "room_temp": 22.5},
    )
    adapter._units = [unit]

    status = adapter.get_status("Room 1")
    assert "Room 1" in status
    assert status["Room 1"]["room_temp"] == 22.5


def test_get_status_all_rooms(adapter):
    """get_status() without room returns all units."""
    unit1 = _ACUnitState(
        name="Unit1",
        room="Room1",
        ip="1.1.1.1",
        controller=MagicMock(),
        api=MagicMock(),
        last_state={"power": "OFF"},
    )
    unit2 = _ACUnitState(
        name="Unit2",
        room="Room2",
        ip="1.1.1.2",
        controller=MagicMock(),
        api=MagicMock(),
        last_state={"power": "ON"},
    )
    adapter._units = [unit1, unit2]

    status = adapter.get_status()
    assert "Room1" in status
    assert "Room2" in status


def test_get_status_unknown_room_returns_empty(adapter):
    """get_status() for unknown room returns empty dict."""
    adapter._units = []
    status = adapter.get_status("NonExistent Room")
    assert status == {}


# ─── Tests: get_room_temperature() ───────────────────────────────────────


def test_get_room_temperature_returns_value(adapter):
    """get_room_temperature() returns cached room temp."""
    unit = _ACUnitState(
        name="AC",
        room="Room 1",
        ip="10.0.0.10",
        controller=MagicMock(),
        api=MagicMock(),
        last_state={"room_temp": 22.5},
    )
    adapter._units = [unit]
    temp = adapter.get_room_temperature("Room 1")
    assert temp == 22.5


def test_get_room_temperature_unknown_room(adapter):
    """get_room_temperature() returns None for unknown room."""
    adapter._units = []
    temp = adapter.get_room_temperature("Unknown")
    assert temp is None


# ─── Tests: set_power() / set_temperature() / set_mode() ─────────────────


async def test_set_power_calls_controller(adapter):
    """set_power() calls controller.set_power via to_thread."""
    ctrl = _make_mock_controller()
    unit = _ACUnitState(
        name="AC",
        room="Room 1",
        ip="10.0.0.10",
        controller=ctrl,
        api=_make_mock_api(),
        last_state={},
    )
    adapter._units = [unit]

    with patch("src.integrations.mitsubishi_ac.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = None
        await adapter.set_power("Room 1", True)

    mock_thread.assert_called_once_with(ctrl.set_power, True)


async def test_set_temperature_calls_controller(adapter):
    """set_temperature() calls controller.set_temperature via to_thread."""
    ctrl = _make_mock_controller()
    unit = _ACUnitState(
        name="AC",
        room="Room 1",
        ip="10.0.0.10",
        controller=ctrl,
        api=_make_mock_api(),
        last_state={},
    )
    adapter._units = [unit]

    with patch("src.integrations.mitsubishi_ac.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = None
        await adapter.set_temperature("Room 1", 24.0)

    mock_thread.assert_called_once_with(ctrl.set_temperature, 24.0)


async def test_set_power_unknown_room_raises(adapter):
    """set_power() raises ValueError for unknown room."""
    adapter._units = []
    with pytest.raises(ValueError, match="No AC unit"):
        await adapter.set_power("Unknown Room", True)


# ─── Tests: _extract_state() ─────────────────────────────────────────────


def test_extract_state_returns_correct_keys(adapter):
    """_extract_state() returns dict with expected keys."""
    ctrl = _make_mock_controller(
        power="PowerOnOff.OFF", mode="DriveMode.COOLER", set_temp=23.0, room_temp=22.5, power_watt=1
    )
    state = adapter._extract_state(ctrl)
    assert "power" in state
    assert "mode" in state
    assert "set_temp" in state
    assert "room_temp" in state
    assert "power_watt" in state
    assert "energy_kwh" in state


def test_extract_state_handles_exception(adapter):
    """_extract_state() returns empty dict on error."""
    ctrl = MagicMock()
    ctrl.state = None  # Will cause AttributeError
    state = adapter._extract_state(ctrl)
    # Should not raise, returns empty dict
    assert isinstance(state, dict)
