"""Tests for ScheduleEngine.on_weather_event()."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from src.background.scheduler import ScheduleEngine


TZ = ZoneInfo("Europe/Berlin")


def make_config():
    config = MagicMock()
    config.home.timezone = "Europe/Berlin"
    config.matrix.room_id = "!room:example.test"
    return config


def make_memory(routines=None):
    procedural = AsyncMock()
    procedural.get_routines_by_trigger = AsyncMock(return_value=routines or [])
    procedural.get_active_schedules = AsyncMock(return_value=[])
    procedural.mark_schedule_run = AsyncMock()
    procedural.delete_expired_one_shots = AsyncMock()
    procedural.log_routine_execution = AsyncMock()
    memory = MagicMock()
    memory.procedural = procedural
    return memory


def make_engine(memory=None):
    config = make_config()
    mem = memory or make_memory()
    return ScheduleEngine(
        memory_layer=mem,
        tool_registry={},
        config=config,
    )


def make_routine(routine_id=1, autonomy_level=2, trigger_value="raining"):
    return {
        "id": routine_id,
        "name": "Markisen einfahren",
        "description": "Markisen bei Regen einfahren",
        "trigger_type": "weather",
        "trigger_value": trigger_value,
        "autonomy_level": autonomy_level,
        "actions": [{"tool": "blinds_control", "args": {"room": "Terrasse", "action": "up"}}],
        "enabled": 1,
    }


# ─── Test: active=True fires routine ─────────────────────────────────────────


async def test_weather_event_fires_routine_when_active():
    """on_weather_event('raining', True) executes matching routines with autonomy_level >= 1."""
    routine = make_routine(autonomy_level=2)
    memory = make_memory(routines=[routine])
    engine = make_engine(memory=memory)

    engine.execute_routine = AsyncMock(return_value="OK")

    await engine.on_weather_event("raining", True)

    memory.procedural.get_routines_by_trigger.assert_awaited_once_with("weather", "raining")
    engine.execute_routine.assert_awaited_once_with(1)


# ─── Test: active=False skips execution ──────────────────────────────────────


async def test_weather_event_inactive_skips_execution():
    """on_weather_event('raining', False) does not execute any routines."""
    routine = make_routine(autonomy_level=2)
    memory = make_memory(routines=[routine])
    engine = make_engine(memory=memory)
    engine.execute_routine = AsyncMock(return_value="OK")

    await engine.on_weather_event("raining", False)

    memory.procedural.get_routines_by_trigger.assert_not_awaited()
    engine.execute_routine.assert_not_awaited()


# ─── Test: autonomy_level=0 skips execution ───────────────────────────────────


async def test_weather_event_autonomy_level_0_skips_execution():
    """Routine with autonomy_level=0 is not executed (suggest only)."""
    routine = make_routine(autonomy_level=0)
    memory = make_memory(routines=[routine])
    engine = make_engine(memory=memory)
    engine.execute_routine = AsyncMock(return_value="OK")

    await engine.on_weather_event("raining", True)

    engine.execute_routine.assert_not_awaited()


# ─── Test: debounce within 30 minutes ─────────────────────────────────────────


async def test_weather_event_debounce_within_30_minutes():
    """Second call within 30 minutes does not re-execute the same routine."""
    routine = make_routine(autonomy_level=2)
    memory = make_memory(routines=[routine])
    engine = make_engine(memory=memory)
    engine.execute_routine = AsyncMock(return_value="OK")

    await engine.on_weather_event("raining", True)
    # Second call immediately after
    await engine.on_weather_event("raining", True)

    # execute_routine should only have been called once
    assert engine.execute_routine.await_count == 1


# ─── Test: debounce expires after 30 minutes ─────────────────────────────────


async def test_weather_event_fires_again_after_debounce_expires():
    """Routine fires again after 30 minute debounce window expires."""
    routine = make_routine(routine_id=5, autonomy_level=2)
    memory = make_memory(routines=[routine])
    engine = make_engine(memory=memory)
    engine.execute_routine = AsyncMock(return_value="OK")

    await engine.on_weather_event("raining", True)
    assert engine.execute_routine.await_count == 1

    # Artificially move the last_fired time back by 31 minutes
    debounce_key = ("raining", 5)
    engine._weather_last_fired[debounce_key] = datetime.now(TZ) - timedelta(minutes=31)

    await engine.on_weather_event("raining", True)
    assert engine.execute_routine.await_count == 2


# ─── Test: routine without id is skipped ──────────────────────────────────────


async def test_weather_event_skips_routine_without_id():
    """Routine missing 'id' field is skipped gracefully."""
    routine = {"name": "No ID Routine", "autonomy_level": 2, "trigger_value": "raining"}
    memory = make_memory(routines=[routine])
    engine = make_engine(memory=memory)
    engine.execute_routine = AsyncMock(return_value="OK")

    await engine.on_weather_event("raining", True)

    engine.execute_routine.assert_not_awaited()
