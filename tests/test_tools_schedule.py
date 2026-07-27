"""Tests for schedule tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.schedule import (
    set_context,
    schedule_task,
    execute_routine,
    list_schedules,
    cancel_schedule,
)
import src.tools.schedule as schedule_module
from src.tools.context import ToolContext


# ─── Fixtures ────────────────────────────────────────────────────────────────


def make_mock_engine():
    engine = AsyncMock()
    engine.add_one_shot = AsyncMock(return_value=42)
    engine.execute_routine = AsyncMock(return_value="Routine ausgeführt.")
    return engine


def make_mock_memory(schedules=None, routines=None, routine=None):
    procedural = AsyncMock()
    procedural.get_active_schedules = AsyncMock(return_value=schedules or [])
    procedural.get_routines = AsyncMock(return_value=routines or [])
    procedural.get_routine_by_name = AsyncMock(return_value=routine)
    procedural.cancel_schedule = AsyncMock(return_value=True)
    memory = MagicMock()
    memory.procedural = procedural
    return memory


def _make_ctx(engine=None, memory=None) -> ToolContext:
    return ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=memory,
        schedule_engine=engine,
    )


@pytest.fixture(autouse=True)
def reset_ctx():
    """Reset module-level _ctx between tests."""
    schedule_module._ctx = None
    yield
    schedule_module._ctx = None


# ─── schedule_task tests ──────────────────────────────────────────────────────


async def test_schedule_task_stores_llm_dispatch():
    """schedule_task stores all commands as llm_dispatch, never parsing 'what'."""
    engine = make_mock_engine()
    memory = make_mock_memory()
    set_context(_make_ctx(engine=engine, memory=memory))

    await schedule_task("in 30 Minuten", "Licht im Wohnzimmer ausschalten")

    # Verify add_one_shot was called
    engine.add_one_shot.assert_called_once()
    call_kwargs = engine.add_one_shot.call_args.kwargs
    # action_type MUST be llm_dispatch
    assert call_kwargs["action_type"] == "llm_dispatch"
    # The 'what' text is preserved in action_params
    assert "Licht im Wohnzimmer ausschalten" in str(call_kwargs.get("action_params", {}))


async def test_schedule_task_never_parses_what():
    """schedule_task does NOT parse 'what' into specific action_type."""
    engine = make_mock_engine()
    memory = make_mock_memory()
    set_context(_make_ctx(engine=engine, memory=memory))

    await schedule_task("um 22:00", "Licht an und Jalousien runter")

    call_kwargs = engine.add_one_shot.call_args.kwargs
    # Never "lights_control" or anything else — always llm_dispatch
    assert call_kwargs["action_type"] == "llm_dispatch"


async def test_schedule_task_returns_confirmation_with_id():
    """schedule_task returns German confirmation including schedule ID."""
    engine = make_mock_engine()
    engine.add_one_shot = AsyncMock(return_value=7)
    memory = make_mock_memory()
    set_context(_make_ctx(engine=engine, memory=memory))

    result = await schedule_task("in 30 Minuten", "Musik stoppen")

    assert "Musik stoppen" in result or "Geplant" in result
    assert "7" in result  # Schedule ID


async def test_schedule_task_invalid_time_returns_german_error():
    """schedule_task returns German error for unrecognizable time string."""
    engine = AsyncMock()
    engine.add_one_shot = AsyncMock(
        side_effect=ValueError("Konnte Zeitangabe nicht verstehen: 'übermorgen'")
    )
    memory = make_mock_memory()
    set_context(_make_ctx(engine=engine, memory=memory))

    result = await schedule_task("übermorgen", "Licht an")

    assert "nicht" in result.lower() or "Fehler" in result or "erkannt" in result


async def test_schedule_task_no_engine_returns_error():
    """schedule_task returns error when no engine configured."""
    # No set_context call
    result = await schedule_task("um 22:00", "Test")
    assert "verfügbar" in result.lower()


# ─── execute_routine tests ────────────────────────────────────────────────────


async def test_execute_routine_calls_public_execute_routine():
    """execute_routine calls engine.execute_routine (PUBLIC, no underscore)."""
    routine = {"id": 1, "name": "Bettfertig", "actions": [], "autonomy_level": 1}
    engine = make_mock_engine()
    memory = make_mock_memory(routine=routine)
    set_context(_make_ctx(engine=engine, memory=memory))

    await execute_routine("Bettfertig")

    # execute_routine (no underscore) was called
    engine.execute_routine.assert_called_once_with("Bettfertig")


async def test_execute_routine_returns_not_found_for_unknown():
    """execute_routine returns German not-found message when routine unknown."""
    engine = make_mock_engine()
    memory = make_mock_memory(routine=None)
    set_context(_make_ctx(engine=engine, memory=memory))

    result = await execute_routine("NonExistentRoutine")

    assert "nicht gefunden" in result.lower()


async def test_execute_routine_uses_partial_name_match():
    """execute_routine passes name to get_routine_by_name (which does LIKE match)."""
    routine = {"id": 1, "name": "Bettfertig", "actions": [], "autonomy_level": 1}
    engine = make_mock_engine()
    memory = make_mock_memory(routine=routine)
    set_context(_make_ctx(engine=engine, memory=memory))

    await execute_routine("bett")

    # get_routine_by_name should have been called with "bett"
    memory.procedural.get_routine_by_name.assert_called_with("bett")


async def test_execute_routine_no_engine_returns_error():
    """execute_routine returns error when no engine configured."""
    result = await execute_routine("Bettfertig")
    assert "verfügbar" in result.lower()


# ─── list_schedules tests ─────────────────────────────────────────────────────


async def test_list_schedules_shows_active_schedules():
    """list_schedules returns formatted list of active schedules."""
    schedules = [
        {
            "id": 1,
            "description": "Licht aus",
            "cron_expr": None,
            "one_shot_at": "2026-03-22T22:00:00+01:00",
        },
        {"id": 2, "description": "Morgenroutine", "cron_expr": "0 7 * * *", "one_shot_at": None},
    ]
    routines = [{"id": 10, "name": "Bettfertig", "trigger": "22:30"}]
    engine = make_mock_engine()
    memory = make_mock_memory(schedules=schedules, routines=routines)
    set_context(_make_ctx(engine=engine, memory=memory))

    result = await list_schedules()

    assert "Licht aus" in result
    assert "Morgenroutine" in result
    assert "Bettfertig" in result
    assert "1" in result
    assert "2" in result


async def test_list_schedules_empty():
    """list_schedules handles empty schedules and routines gracefully."""
    engine = make_mock_engine()
    memory = make_mock_memory(schedules=[], routines=[])
    set_context(_make_ctx(engine=engine, memory=memory))

    result = await list_schedules()

    assert "Keine" in result


async def test_list_schedules_no_memory_returns_error():
    """list_schedules returns error when no memory configured."""
    result = await list_schedules()
    assert "verfügbar" in result.lower()


# ─── cancel_schedule tests ────────────────────────────────────────────────────


async def test_cancel_schedule_found():
    """cancel_schedule returns German confirmation when schedule found."""
    memory = make_mock_memory()
    memory.procedural.cancel_schedule = AsyncMock(return_value=True)
    engine = make_mock_engine()
    set_context(_make_ctx(engine=engine, memory=memory))

    result = await cancel_schedule(5)

    assert "5" in result
    assert "abgebrochen" in result.lower() or "abbruch" in result.lower() or "Zeitplan 5" in result


async def test_cancel_schedule_not_found():
    """cancel_schedule returns not-found message when schedule doesn't exist."""
    memory = make_mock_memory()
    memory.procedural.cancel_schedule = AsyncMock(return_value=False)
    engine = make_mock_engine()
    set_context(_make_ctx(engine=engine, memory=memory))

    result = await cancel_schedule(999)

    assert "nicht gefunden" in result.lower() or "999" in result


async def test_cancel_schedule_no_memory_returns_error():
    """cancel_schedule returns error when no memory configured."""
    result = await cancel_schedule(1)
    assert "verfügbar" in result.lower()


# ─── create_triggered_routine tests ──────────────────────────────────────────


async def test_create_triggered_routine_stores_with_trigger_fields():
    """create_triggered_routine calls store_candidate_routine with correct trigger fields."""
    from src.tools.schedule import create_triggered_routine, set_context

    import json

    procedural = AsyncMock()
    procedural.store_candidate_routine = AsyncMock(return_value=42)
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    actions_json = json.dumps(
        [{"tool": "blinds_control", "args": {"room": "Terrasse", "action": "up"}}]
    )
    result = await create_triggered_routine(
        name="Markisen einfahren",
        description="Markisen bei Regen einfahren",
        trigger_type="weather",
        trigger_value="raining",
        actions=actions_json,
        autonomy_level=2,
    )

    assert "42" in result
    assert "weather=raining" in result
    procedural.store_candidate_routine.assert_awaited_once()
    call_kwargs = procedural.store_candidate_routine.call_args.kwargs
    assert call_kwargs["trigger_type"] == "weather"
    assert call_kwargs["trigger_value"] == "raining"
    assert call_kwargs["autonomy_level"] == 2
    assert isinstance(call_kwargs["actions"], list)


async def test_create_triggered_routine_invalid_json_actions():
    """create_triggered_routine returns error for invalid JSON actions."""
    from src.tools.schedule import create_triggered_routine, set_context

    procedural = AsyncMock()
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    result = await create_triggered_routine(
        name="Test",
        description="Test",
        trigger_type="weather",
        trigger_value="raining",
        actions="not valid json",
    )

    assert "Ungueltige JSON-Aktionen" in result or "Fehler" in result
    procedural.store_candidate_routine.assert_not_awaited()


async def test_create_triggered_routine_invalid_trigger_type():
    """create_triggered_routine returns error for unknown trigger_type."""
    from src.tools.schedule import create_triggered_routine, set_context
    import json

    procedural = AsyncMock()
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    result = await create_triggered_routine(
        name="Test",
        description="Test",
        trigger_type="invalid_type",
        trigger_value="something",
        actions=json.dumps([]),
    )

    assert "Unbekannter Trigger-Typ" in result
    procedural.store_candidate_routine.assert_not_awaited()


async def test_create_triggered_routine_no_context():
    """create_triggered_routine returns error when context is not set."""
    import src.tools.schedule as schedule_module
    from src.tools.schedule import create_triggered_routine
    import json

    schedule_module._ctx = None

    result = await create_triggered_routine(
        name="Test",
        description="Test",
        trigger_type="weather",
        trigger_value="raining",
        actions=json.dumps([]),
    )

    assert "nicht verfuegbar" in result or "nicht verfügbar" in result


async def test_create_triggered_routine_message_trigger_type_now_invalid():
    """create_triggered_routine rejects 'message' as trigger_type (removed in favour of scenes)."""
    import json

    from src.tools.schedule import create_triggered_routine, set_context

    procedural = AsyncMock()
    procedural.store_candidate_routine = AsyncMock(return_value=42)
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    result = await create_triggered_routine(
        name="Guten Morgen",
        description="Morgenroutine",
        trigger_type="message",
        trigger_value="guten_morgen",
        actions=json.dumps([{"tool": "blinds_control", "args": {"room": "Alle", "action": "up"}}]),
        autonomy_level=1,
    )

    # "message" trigger type is no longer valid — scenes replaced message-triggered routines
    assert "Unbekannter Trigger-Typ" in result
    assert "Erlaubt" in result
    # routine must NOT have been created
    procedural.store_candidate_routine.assert_not_called()


# ─── trigger_type="time" tests (Task 3.1) ────────────────────────────────────


async def test_create_triggered_routine_time_simple_hhmm():
    """trigger_type=time with HH:MM converts to daily cron expression."""
    from src.tools.schedule import create_triggered_routine, set_context
    import json

    procedural = AsyncMock()
    procedural.store_candidate_routine = AsyncMock(return_value=55)
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    actions_json = json.dumps(
        [{"tool": "blinds_control", "args": {"room": "Zimmer 1", "action": "down"}}]
    )
    result = await create_triggered_routine(
        name="Jalousien abend",
        description="Jalousien taeglich um 19:05",
        trigger_type="time",
        trigger_value="19:05",
        actions=actions_json,
        autonomy_level=2,
    )

    assert "55" in result or "Jalousien abend" in result
    procedural.store_candidate_routine.assert_awaited_once()
    call_kwargs = procedural.store_candidate_routine.call_args.kwargs
    assert call_kwargs["cron_expr"] == "5 19 * * *"
    assert call_kwargs["trigger_type"] == "time"
    assert call_kwargs["trigger_value"] == "19:05"


async def test_create_triggered_routine_time_full_cron():
    """trigger_type=time with full cron expression stores it as-is."""
    from src.tools.schedule import create_triggered_routine, set_context
    import json

    procedural = AsyncMock()
    procedural.store_candidate_routine = AsyncMock(return_value=56)
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    actions_json = json.dumps(
        [{"tool": "blinds_control", "args": {"room": "Zimmer 1", "action": "down"}}]
    )
    result = await create_triggered_routine(
        name="Jalousien Werktage",
        description="Jalousien werktags um 19:05",
        trigger_type="time",
        trigger_value="5 19 * * 1-5",
        actions=actions_json,
        autonomy_level=2,
    )

    assert "56" in result or "Jalousien Werktage" in result
    call_kwargs = procedural.store_candidate_routine.call_args.kwargs
    assert call_kwargs["cron_expr"] == "5 19 * * 1-5"


async def test_create_triggered_routine_time_invalid_cron():
    """trigger_type=time with invalid cron returns German error, does not store."""
    from src.tools.schedule import create_triggered_routine, set_context
    import json

    procedural = AsyncMock()
    procedural.store_candidate_routine = AsyncMock(return_value=57)
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    actions_json = json.dumps(
        [{"tool": "blinds_control", "args": {"room": "Zimmer 1", "action": "down"}}]
    )
    result = await create_triggered_routine(
        name="Test",
        description="Test",
        trigger_type="time",
        trigger_value="not_a_valid_cron",
        actions=actions_json,
    )

    assert (
        "Cron" in result
        or "Ausdr" in result
        or "Fehler" in result
        or "ungueltig" in result.lower()
        or "Ungueltiger" in result
    )
    procedural.store_candidate_routine.assert_not_awaited()


async def test_create_triggered_routine_time_returns_confirmation():
    """trigger_type=time returns a confirmation string with trigger info."""
    from src.tools.schedule import create_triggered_routine, set_context
    import json

    procedural = AsyncMock()
    procedural.store_candidate_routine = AsyncMock(return_value=58)
    memory = MagicMock()
    memory.procedural = procedural
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    actions_json = json.dumps(
        [{"tool": "lights_control", "args": {"room": "Wohnzimmer", "action": "off"}}]
    )
    result = await create_triggered_routine(
        name="Licht aus abends",
        description="Licht um 22 Uhr aus",
        trigger_type="time",
        trigger_value="22:00",
        actions=actions_json,
        autonomy_level=1,
    )

    assert "time=22:00" in result or "Licht aus abends" in result
    assert "58" in result


# ─── list_schedules time routine display (Task 3.3) ──────────────────────────


async def test_list_schedules_shows_time_routine_cron():
    """list_schedules shows 'Zeit: HH:MM taeglich' for time-triggered routines with HH:MM trigger_value."""
    from src.tools.schedule import list_schedules, set_context

    routine = {
        "id": 99,
        "name": "Jalousien abend",
        "trigger_type": "time",
        "trigger_value": "19:05",
        "cron_expr": "5 19 * * *",
        "trigger": "",
        "enabled": 1,
    }
    memory = make_mock_memory(schedules=[], routines=[routine])
    ctx = _make_ctx(memory=memory)
    set_context(ctx)

    result = await list_schedules()

    assert "Jalousien abend" in result
    assert "Zeit" in result
    assert "19:05" in result or "taeglich" in result
