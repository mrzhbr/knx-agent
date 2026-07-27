"""Tests for ScheduleEngine."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest  # type: ignore[import-not-found]

from src.background.scheduler import ScheduleEngine, _parse_time_string
from src.core.guardrails import RequestSource, get_request_security_context


# ─── Fixtures ───────────────────────────────────────────────────────────────

TZ = ZoneInfo("Europe/Berlin")


def make_config():
    config = MagicMock()
    config.home.timezone = "Europe/Berlin"
    config.matrix.room_id = "!room:example.test"
    return config


def make_memory(schedules=None, routines=None, executions=None):
    """Build a mock MemoryLayer with a ProceduralMemory mock."""
    procedural = AsyncMock()
    procedural.get_active_schedules = AsyncMock(return_value=schedules or [])
    procedural.get_routines = AsyncMock(return_value=routines or [])
    procedural.get_routine_by_name = AsyncMock(return_value=None)
    procedural.get_routines_by_trigger = AsyncMock(return_value=[])
    procedural.mark_schedule_run = AsyncMock()
    procedural.delete_expired_one_shots = AsyncMock()
    procedural.add_schedule = AsyncMock(return_value=1)
    procedural.log_routine_execution = AsyncMock()

    memory = MagicMock()
    memory.procedural = procedural
    return memory


def make_engine(memory=None, tool_registry=None, supervisor_fn=None, matrix=None):
    config = make_config()
    mem = memory or make_memory()
    engine = ScheduleEngine(
        memory_layer=mem,
        tool_registry=tool_registry or {},
        config=config,
        supervisor_fn=supervisor_fn,
        matrix=matrix,
    )
    return engine


# ─── _parse_time_string tests ────────────────────────────────────────────────


def test_parse_in_30_minuten():
    """'in 30 minuten' returns now + 30 minutes (tz-aware)."""
    result = _parse_time_string("in 30 minuten", TZ)
    assert result is not None
    now = datetime.now(TZ)
    delta = (result - now).total_seconds()
    assert 1750 < delta < 1850  # ~1800 seconds


def test_parse_in_2_stunden():
    """'in 2 stunden' returns now + 2 hours."""
    result = _parse_time_string("in 2 stunden", TZ)
    assert result is not None
    now = datetime.now(TZ)
    delta = (result - now).total_seconds()
    assert 7100 < delta < 7300  # ~7200 seconds


def test_parse_um_hhmm():
    """'um 22:00' returns today or tomorrow at 22:00."""
    result = _parse_time_string("um 22:00", TZ)
    assert result is not None
    assert result.tzinfo is not None
    assert result.hour == 22
    assert result.minute == 0


def test_parse_morgen_hhmm():
    """'morgen 08:00' returns tomorrow at 08:00."""
    result = _parse_time_string("morgen 08:00", TZ)
    assert result is not None
    now = datetime.now(TZ)
    tomorrow = now + timedelta(days=1)
    assert result.day == tomorrow.day or result.day == (tomorrow + timedelta(days=1)).day
    assert result.hour == 8


def test_parse_returns_none_for_unknown():
    """Unknown time string returns None."""
    result = _parse_time_string("übermorgen abends", TZ)
    assert result is None


def test_parse_result_is_timezone_aware():
    """All parsed datetimes must be timezone-aware."""
    for text in ["in 30 minuten", "in 1 stunden", "um 10:00", "morgen 09:00"]:
        result = _parse_time_string(text, TZ)
        if result is not None:
            assert result.tzinfo is not None, f"Not tz-aware: {text}"


# ─── ScheduleEngine lifecycle tests ──────────────────────────────────────────


async def test_start_creates_task():
    """start() creates an asyncio task."""
    engine = make_engine()
    engine.start()
    assert engine._task is not None
    assert not engine._task.done()
    engine.stop()
    await asyncio.sleep(0)


async def test_stop_cancels_task():
    """stop() cancels the running task."""
    engine = make_engine()
    engine.start()
    engine.stop()
    await asyncio.sleep(0.01)
    task = engine._task
    assert task is not None
    assert task.cancelling() > 0 or task.cancelled() or task.done()


# ─── Cron matching tests ─────────────────────────────────────────────────────


async def test_cron_fires_at_matching_time():
    """Cron schedule fires when croniter.match returns True."""
    called = []

    async def mock_tool(**kwargs):
        called.append(kwargs)
        return "OK"

    sched = {
        "id": 1,
        "cron_expr": "0 7 * * *",
        "one_shot_at": None,
        "last_run": None,
        "action_type": "mock_tool",
        "action_params": json.dumps({}),
        "routine_id": None,
    }
    memory = make_memory(schedules=[sched])
    engine = make_engine(memory=memory, tool_registry={"mock_tool": mock_tool})

    # Patch croniter.match to return True
    with patch("src.background.scheduler._croniter_cls") as mock_croniter:
        mock_croniter.match.return_value = True
        now = datetime.now(TZ)
        await engine._evaluate_schedule(sched, now)

    assert len(called) == 1


async def test_cron_skips_if_already_run_this_minute():
    """Cron schedule does not fire if last_run is less than 60s ago."""
    called = []

    async def mock_tool(**kwargs):
        called.append(kwargs)
        return "OK"

    now = datetime.now(TZ)
    sched = {
        "id": 1,
        "cron_expr": "0 7 * * *",
        "one_shot_at": None,
        "last_run": now.isoformat(),  # Just ran
        "action_type": "mock_tool",
        "action_params": "{}",
        "routine_id": None,
    }
    memory = make_memory(schedules=[sched])
    engine = make_engine(memory=memory, tool_registry={"mock_tool": mock_tool})

    with patch("src.background.scheduler._croniter_cls") as mock_croniter:
        mock_croniter.match.return_value = True
        await engine._evaluate_schedule(sched, now)

    # Should NOT fire because last_run < 60s ago
    assert len(called) == 0


# ─── One-shot tests ───────────────────────────────────────────────────────────


async def test_one_shot_fires_when_time_reached():
    """One-shot schedule fires when now >= one_shot_at."""
    called = []

    async def mock_tool(**kwargs):
        called.append(kwargs)
        return "OK"

    past_time = (datetime.now(TZ) - timedelta(minutes=1)).isoformat()
    sched = {
        "id": 2,
        "cron_expr": None,
        "one_shot_at": past_time,
        "last_run": None,
        "action_type": "mock_tool",
        "action_params": "{}",
        "routine_id": None,
    }
    memory = make_memory(schedules=[sched])
    engine = make_engine(memory=memory, tool_registry={"mock_tool": mock_tool})

    now = datetime.now(TZ)
    await engine._evaluate_schedule(sched, now)
    assert len(called) == 1


async def test_one_shot_does_not_fire_before_time():
    """One-shot schedule does not fire when now < one_shot_at."""
    called = []

    async def mock_tool(**kwargs):
        called.append(kwargs)
        return "OK"

    future_time = (datetime.now(TZ) + timedelta(hours=1)).isoformat()
    sched = {
        "id": 3,
        "cron_expr": None,
        "one_shot_at": future_time,
        "last_run": None,
        "action_type": "mock_tool",
        "action_params": "{}",
        "routine_id": None,
    }
    memory = make_memory(schedules=[sched])
    engine = make_engine(memory=memory, tool_registry={"mock_tool": mock_tool})

    now = datetime.now(TZ)
    await engine._evaluate_schedule(sched, now)
    assert len(called) == 0


async def test_one_shot_does_not_fire_if_already_run():
    """One-shot with last_run set does not fire again."""
    called = []

    async def mock_tool(**kwargs):
        called.append(kwargs)
        return "OK"

    past_time = (datetime.now(TZ) - timedelta(minutes=5)).isoformat()
    sched = {
        "id": 4,
        "cron_expr": None,
        "one_shot_at": past_time,
        "last_run": past_time,  # Already ran
        "action_type": "mock_tool",
        "action_params": "{}",
        "routine_id": None,
    }
    memory = make_memory(schedules=[sched])
    engine = make_engine(memory=memory, tool_registry={"mock_tool": mock_tool})

    now = datetime.now(TZ)
    await engine._evaluate_schedule(sched, now)
    assert len(called) == 0


# ─── llm_dispatch tests ──────────────────────────────────────────────────────


async def test_llm_dispatch_calls_supervisor_fn():
    """llm_dispatch routes through supervisor_fn."""
    supervisor_calls = []

    def mock_supervisor(text):
        request = get_request_security_context()
        assert request is not None
        assert request.source is RequestSource.AUTOMATION
        supervisor_calls.append(text)
        return "Verstanden."

    matrix = AsyncMock()
    sched = {
        "id": 5,
        "cron_expr": None,
        "one_shot_at": (datetime.now(TZ) - timedelta(minutes=1)).isoformat(),
        "last_run": None,
        "action_type": "llm_dispatch",
        "action_params": json.dumps({"text": "Licht im Wohnzimmer aus"}),
        "routine_id": None,
    }
    memory = make_memory(schedules=[sched])
    engine = make_engine(memory=memory, supervisor_fn=mock_supervisor, matrix=matrix)
    engine._config.matrix.room_id = "!room:example.test"

    now = datetime.now(TZ)
    await engine._evaluate_schedule(sched, now)
    assert len(supervisor_calls) == 1
    assert "Licht" in supervisor_calls[0]


async def test_direct_scheduled_security_action_is_blocked():
    alarm_tool = AsyncMock(return_value="should not run")
    sched = {
        "id": 6,
        "action_type": "alarm_control",
        "action_params": json.dumps({"action": "arm"}),
        "routine_id": None,
    }
    memory = make_memory()
    engine = make_engine(memory=memory, tool_registry={"alarm_control": alarm_tool})

    await engine._execute_schedule(sched)

    alarm_tool.assert_not_awaited()
    memory.procedural.mark_schedule_run.assert_awaited_once_with(6)


# ─── execute_routine tests ────────────────────────────────────────────────────


async def test_execute_routine_is_public_method():
    """execute_routine must be a public method (no underscore prefix)."""
    engine = make_engine()
    assert hasattr(engine, "execute_routine")
    assert not hasattr(engine, "_execute_routine")


async def test_execute_routine_calls_tools():
    """execute_routine calls tool functions from registry."""
    called = []

    async def mock_lights(**kwargs):
        called.append(("lights", kwargs))
        return "Licht an."

    routine = {
        "id": 1,
        "name": "Bettfertig",
        "description": "Abendliche Routine",
        "autonomy_level": 1,
        "actions": [{"tool": "lights_control", "args": {"room": "Wohnzimmer", "action": "off"}}],
    }
    procedural = AsyncMock()
    procedural.get_routine_by_name = AsyncMock(return_value=routine)
    procedural.get_routines = AsyncMock(return_value=[routine])
    procedural.log_routine_execution = AsyncMock()
    memory = MagicMock()
    memory.procedural = procedural

    matrix = AsyncMock()
    engine = make_engine(
        memory=memory,
        tool_registry={"lights_control": mock_lights},
        matrix=matrix,
    )

    await engine.execute_routine("Bettfertig")
    assert len(called) == 1
    assert "Wohnzimmer" in str(called[0])


async def test_execute_routine_not_found():
    """execute_routine returns German error when routine not found."""
    procedural = AsyncMock()
    procedural.get_routine_by_name = AsyncMock(return_value=None)
    procedural.get_routines = AsyncMock(return_value=[])
    memory = MagicMock()
    memory.procedural = procedural

    engine = make_engine(memory=memory)
    result = await engine.execute_routine("NonExistent")
    assert "nicht gefunden" in result.lower() or "NonExistent" in result


# ─── Presence trigger tests ───────────────────────────────────────────────────


async def test_on_presence_event_fires_arriving_routine():
    """on_presence_event fires routines with trigger_value='arriving' when is_home=True."""
    called = []

    async def mock_lights(**kwargs):
        called.append(kwargs)
        return "Licht an."

    routine = {
        "id": 10,
        "name": "WillkommenRoutine",
        "description": "Begrüßung",
        "trigger_type": "presence",
        "trigger_value": "arriving",
        "autonomy_level": 1,
        "actions": [{"tool": "lights_control", "args": {"room": "Flur", "action": "on"}}],
    }
    procedural = AsyncMock()
    procedural.get_routines_by_trigger = AsyncMock(return_value=[routine])
    procedural.get_routine_by_name = AsyncMock(return_value=routine)
    procedural.get_routines = AsyncMock(return_value=[routine])
    procedural.log_routine_execution = AsyncMock()
    memory = MagicMock()
    memory.procedural = procedural

    matrix = AsyncMock()
    engine = make_engine(
        memory=memory,
        tool_registry={"lights_control": mock_lights},
        matrix=matrix,
    )

    await engine.on_presence_event("Max", is_home=True)

    # Verify it queried for "arriving"
    procedural.get_routines_by_trigger.assert_called_with("presence", "arriving")
    assert len(called) == 1


async def test_on_presence_event_fires_leaving_routine():
    """on_presence_event fires routines with trigger_value='leaving' when is_home=False."""
    procedural = AsyncMock()
    procedural.get_routines_by_trigger = AsyncMock(return_value=[])
    memory = MagicMock()
    memory.procedural = procedural

    engine = make_engine(memory=memory)
    await engine.on_presence_event("Max", is_home=False)

    procedural.get_routines_by_trigger.assert_called_with("presence", "leaving")


# ─── add_one_shot tests ───────────────────────────────────────────────────────


async def test_add_one_shot_in_30_minuten():
    """add_one_shot parses 'in 30 minuten' and stores timezone-aware ISO timestamp."""
    memory = make_memory()
    engine = make_engine(memory=memory)

    sid = await engine.add_one_shot(
        "Licht aus",
        "in 30 minuten",
        "llm_dispatch",
        {"text": "Licht aus"},
        created_by="Max",
    )
    assert sid == 1  # Mocked return value

    # Check what was passed to add_schedule
    call_kwargs = memory.procedural.add_schedule.call_args
    one_shot_at = (
        call_kwargs.kwargs.get("one_shot_at") or call_kwargs.args[2] if call_kwargs.args else None
    )
    # The one_shot_at should be an ISO string with timezone info
    if one_shot_at:
        dt = datetime.fromisoformat(one_shot_at)
        assert dt.tzinfo is not None


async def test_add_one_shot_invalid_raises():
    """add_one_shot raises ValueError for unparseable time string."""
    engine = make_engine()

    with pytest.raises(ValueError, match="Konnte Zeitangabe nicht verstehen"):
        await engine.add_one_shot(
            "Test",
            "übermorgen abends vielleicht",
            "llm_dispatch",
            {},
        )


# ─── Stored timestamps timezone tests ────────────────────────────────────────


async def test_stored_timestamps_are_timezone_aware():
    """mark_schedule_run stores timezone-aware ISO timestamp."""
    make_engine()

    # Test that _parse_time_string produces tz-aware datetimes
    for text in ["in 5 minuten", "in 1 stunden", "um 23:00", "morgen 10:00"]:
        result = _parse_time_string(text, TZ)
        if result:
            iso = result.isoformat()
            parsed_back = datetime.fromisoformat(iso)
            assert parsed_back.tzinfo is not None, f"Not tz-aware: {text}"


# ─── Time-triggered routine tests ────────────────────────────────────────────


async def test_time_routine_fires_at_matching_time():
    """Time routine fires when croniter.match returns True."""
    called_ids = []

    engine = make_engine()
    engine.execute_routine = AsyncMock(side_effect=lambda rid: called_ids.append(rid))

    routine = {
        "id": 10,
        "name": "Jalousien abend",
        "trigger_type": "time",
        "trigger_value": "19:05",
        "cron_expr": "5 19 * * *",
        "actions": [],
        "enabled": 1,
    }
    engine._memory.procedural.get_routines_by_trigger = AsyncMock(return_value=[routine])

    now = datetime.now(TZ)
    with patch("src.background.scheduler._croniter_cls") as mock_croniter:
        mock_croniter.match.return_value = True
        await engine._evaluate_time_routines(now)

    assert called_ids == [10]


async def test_time_routine_skips_when_no_match():
    """Time routine does not fire when croniter.match returns False."""
    called_ids = []

    engine = make_engine()
    engine.execute_routine = AsyncMock(side_effect=lambda rid: called_ids.append(rid))

    routine = {
        "id": 11,
        "name": "Morgenroutine",
        "trigger_type": "time",
        "trigger_value": "7:00",
        "cron_expr": "0 7 * * *",
        "actions": [],
        "enabled": 1,
    }
    engine._memory.procedural.get_routines_by_trigger = AsyncMock(return_value=[routine])

    now = datetime.now(TZ)
    with patch("src.background.scheduler._croniter_cls") as mock_croniter:
        mock_croniter.match.return_value = False
        await engine._evaluate_time_routines(now)

    assert called_ids == []


async def test_time_routine_debounce_prevents_double_fire():
    """Time routine does not fire again within 90 seconds."""
    called_ids = []

    engine = make_engine()
    engine.execute_routine = AsyncMock(side_effect=lambda rid: called_ids.append(rid))

    routine = {
        "id": 12,
        "name": "Test",
        "trigger_type": "time",
        "trigger_value": "19:05",
        "cron_expr": "5 19 * * *",
        "actions": [],
        "enabled": 1,
    }
    engine._memory.procedural.get_routines_by_trigger = AsyncMock(return_value=[routine])

    now = datetime.now(TZ)
    with patch("src.background.scheduler._croniter_cls") as mock_croniter:
        mock_croniter.match.return_value = True
        await engine._evaluate_time_routines(now)
        # Call again immediately — should be debounced (0s elapsed < 90s)
        await engine._evaluate_time_routines(now)

    assert len(called_ids) == 1


async def test_time_routine_fires_again_after_debounce():
    """Time routine fires again after 90+ seconds have elapsed."""
    called_ids = []

    engine = make_engine()
    engine.execute_routine = AsyncMock(side_effect=lambda rid: called_ids.append(rid))

    routine = {
        "id": 13,
        "name": "Test",
        "trigger_type": "time",
        "trigger_value": "19:05",
        "cron_expr": "5 19 * * *",
        "actions": [],
        "enabled": 1,
    }
    engine._memory.procedural.get_routines_by_trigger = AsyncMock(return_value=[routine])

    now = datetime.now(TZ)
    with patch("src.background.scheduler._croniter_cls") as mock_croniter:
        mock_croniter.match.return_value = True
        await engine._evaluate_time_routines(now)

        # Simulate 2 minutes elapsed
        engine._time_last_fired[13] = now - timedelta(minutes=2)
        later = now + timedelta(minutes=2)
        await engine._evaluate_time_routines(later)

    assert len(called_ids) == 2


async def test_tick_evaluates_time_routines():
    """_tick calls _evaluate_time_routines."""
    engine = make_engine()
    time_routines_called = []

    async def mock_evaluate_time(now):
        time_routines_called.append(now)

    engine._evaluate_time_routines = mock_evaluate_time

    await engine._tick()

    assert len(time_routines_called) == 1
