from __future__ import annotations

import json

import pytest

from src.memory.procedural import ProceduralMemory


@pytest.fixture
async def memory(tmp_path):
    """Open a fresh in-memory-like memory store for each test."""
    db_path = tmp_path / "test_brain.db"
    mem = ProceduralMemory(db_path)
    await mem.open()
    yield mem
    await mem.close()


async def test_add_and_get_schedule_one_shot(memory):
    """add_schedule creates a one-shot schedule retrievable by id."""
    sid = await memory.add_schedule(
        "Licht ausschalten",
        "llm_dispatch",
        json.dumps({"text": "Licht aus"}),
        one_shot_at="2026-03-22T22:00:00+01:00",
        created_by="Max",
    )
    assert sid is not None and sid > 0
    sched = await memory.get_schedule(sid)
    assert sched is not None
    assert sched["description"] == "Licht ausschalten"
    assert sched["one_shot_at"] == "2026-03-22T22:00:00+01:00"
    assert sched["action_type"] == "llm_dispatch"
    assert sched["enabled"] == 1


async def test_add_cron_schedule(memory):
    """add_schedule creates a cron-based schedule."""
    sid = await memory.add_schedule(
        "Tagesroutine",
        "llm_dispatch",
        json.dumps({"text": "Guten Morgen"}),
        cron_expr="0 7 * * *",
    )
    sched = await memory.get_schedule(sid)
    assert sched["cron_expr"] == "0 7 * * *"
    assert sched["one_shot_at"] is None


async def test_cancel_schedule(memory):
    """cancel_schedule sets enabled=0 and returns True."""
    sid = await memory.add_schedule(
        "Test",
        "llm_dispatch",
        "{}",
        one_shot_at="2026-03-22T10:00:00+01:00",
    )
    result = await memory.cancel_schedule(sid)
    assert result is True
    sched = await memory.get_schedule(sid)
    assert sched["enabled"] == 0


async def test_cancel_nonexistent_schedule(memory):
    """cancel_schedule returns False for unknown id."""
    result = await memory.cancel_schedule(99999)
    assert result is False


async def test_mark_schedule_run(memory):
    """mark_schedule_run updates last_run timestamp."""
    sid = await memory.add_schedule(
        "One shot",
        "llm_dispatch",
        "{}",
        one_shot_at="2026-03-22T10:00:00+01:00",
    )
    sched_before = await memory.get_schedule(sid)
    assert sched_before["last_run"] is None
    await memory.mark_schedule_run(sid)
    sched_after = await memory.get_schedule(sid)
    assert sched_after["last_run"] is not None


async def test_delete_expired_one_shots(memory):
    """delete_expired_one_shots removes one-shots that have last_run set."""
    sid = await memory.add_schedule(
        "Expired",
        "llm_dispatch",
        "{}",
        one_shot_at="2026-03-22T10:00:00+01:00",
    )
    await memory.mark_schedule_run(sid)
    count = await memory.delete_expired_one_shots()
    assert count == 1
    sched = await memory.get_schedule(sid)
    assert sched is None


async def test_get_active_schedules(memory):
    """get_active_schedules returns only enabled schedules."""
    sid1 = await memory.add_schedule(
        "Active", "llm_dispatch", "{}", one_shot_at="2026-03-22T10:00:00"
    )
    sid2 = await memory.add_schedule("ToCancel", "llm_dispatch", "{}", cron_expr="0 8 * * *")
    await memory.cancel_schedule(sid2)
    active = await memory.get_active_schedules()
    ids = [s["id"] for s in active]
    assert sid1 in ids
    assert sid2 not in ids


async def test_autonomy_level_column_exists(memory):
    """Fresh database has autonomy_level column in routines table."""
    rid = await memory.store_candidate_routine(
        name="Test",
        description="Test routine",
        trigger="22:00",
        actions=[{"description": "test"}],
        confidence=0.9,
    )
    routines = await memory.get_routines(enabled_only=False)
    routine = next((r for r in routines if r["id"] == rid), None)
    assert routine is not None
    assert "autonomy_level" in routine
    assert routine["autonomy_level"] == 0


async def test_trigger_columns_exist(memory):
    """Fresh database has trigger_type and trigger_value columns in routines table."""
    rid = await memory.store_candidate_routine(
        name="PresenceRoutine",
        description="Fires on arriving",
        trigger="arriving",
        actions=[{"tool": "lights_control", "args": {"room": "Wohnzimmer", "action": "on"}}],
        confidence=0.85,
    )
    routines = await memory.get_routines(enabled_only=False)
    routine = next((r for r in routines if r["id"] == rid), None)
    assert routine is not None
    assert "trigger_type" in routine
    assert "trigger_value" in routine
    assert routine["trigger_type"] is None  # default
    assert routine["trigger_value"] is None  # default


async def test_get_routine_by_name_partial_match(memory):
    """get_routine_by_name finds 'Bettfertig' when searching 'bett'."""
    await memory.store_candidate_routine(
        name="Bettfertig",
        description="Vorbereitung fürs Bett",
        trigger="22:30",
        actions=[{"description": "Licht aus"}],
        confidence=0.95,
    )
    result = await memory.get_routine_by_name("bett")
    assert result is not None
    assert "Bettfertig" in result["name"]


async def test_get_routine_by_name_no_match(memory):
    """get_routine_by_name returns None when no match."""
    result = await memory.get_routine_by_name("xyznonexistent")
    assert result is None


async def test_get_routines_by_trigger(memory):
    """get_routines_by_trigger returns routines with matching trigger_type."""
    rid1 = await memory.store_candidate_routine(
        name="ArriveRoutine",
        description="On arrive",
        trigger="arriving",
        actions=[{"description": "lights on"}],
        confidence=0.9,
    )
    rid2 = await memory.store_candidate_routine(
        name="LeaveRoutine",
        description="On leave",
        trigger="leaving",
        actions=[{"description": "lights off"}],
        confidence=0.9,
    )
    # Update trigger_type manually via SQL to test the filter
    db = memory._db
    await db.execute(
        "UPDATE routines SET trigger_type='presence', trigger_value='arriving' WHERE id=?", (rid1,)
    )
    await db.execute(
        "UPDATE routines SET trigger_type='presence', trigger_value='leaving' WHERE id=?", (rid2,)
    )
    await db.commit()

    results = await memory.get_routines_by_trigger("presence")
    assert len(results) == 2

    arriving = await memory.get_routines_by_trigger("presence", "arriving")
    assert len(arriving) == 1
    assert arriving[0]["id"] == rid1


async def test_log_routine_execution(memory):
    """log_routine_execution writes to routine_executions table."""
    rid = await memory.store_candidate_routine(
        name="Test",
        description="Test",
        trigger="08:00",
        actions=[{"description": "something"}],
        confidence=0.8,
    )
    await memory.log_routine_execution(rid, success=True, notes="OK")
    executions = await memory.get_routine_executions(routine_id=rid)
    assert len(executions) == 1
    assert executions[0]["success"] == 1
    assert executions[0]["notes"] == "OK"


async def test_get_routine_executions_all(memory):
    """get_routine_executions without filter returns all executions."""
    rid1 = await memory.store_candidate_routine("R1", "D1", "t1", [], 0.9)
    rid2 = await memory.store_candidate_routine("R2", "D2", "t2", [], 0.8)
    await memory.log_routine_execution(rid1, success=True)
    await memory.log_routine_execution(rid2, success=False, notes="failed")
    all_execs = await memory.get_routine_executions()
    assert len(all_execs) == 2


# ─── get_routines_by_trigger enabled=1 filter tests ──────────────────────────


async def test_get_routines_by_trigger_excludes_disabled(memory):
    """get_routines_by_trigger returns empty list when matching routine is disabled."""
    # Insert a disabled routine with trigger_type="weather"
    await memory.store_candidate_routine(
        name="Markisen einfahren",
        description="Markisen bei Regen",
        trigger="Regen",
        actions=[{"tool": "blinds_control", "args": {"action": "up"}}],
        trigger_type="weather",
        trigger_value="raining",
        autonomy_level=2,
    )
    # Disable the routine
    routines_all = await memory.get_routines(enabled_only=False)
    assert len(routines_all) >= 1
    rid = routines_all[-1]["id"]
    await memory.update_routine(rid, enabled=False)

    # get_routines_by_trigger should return nothing (enabled=1 filter)
    result = await memory.get_routines_by_trigger("weather", "raining")
    assert result == [], f"Expected empty list, got {result}"


async def test_get_routines_by_trigger_returns_enabled(memory):
    """get_routines_by_trigger returns enabled routines matching trigger."""
    await memory.store_candidate_routine(
        name="Markisen bei Regen",
        description="Markisen bei Regen einfahren",
        trigger="Regen",
        actions=[{"tool": "blinds_control", "args": {"action": "up"}}],
        trigger_type="weather",
        trigger_value="raining",
        autonomy_level=2,
    )
    result = await memory.get_routines_by_trigger("weather", "raining")
    assert len(result) == 1
    assert result[0]["name"] == "Markisen bei Regen"
    assert result[0]["trigger_type"] == "weather"
    assert result[0]["trigger_value"] == "raining"


# ─── store_candidate_routine with trigger fields tests ────────────────────────


async def test_store_candidate_routine_saves_trigger_fields(memory):
    """store_candidate_routine stores trigger_type, trigger_value, autonomy_level correctly."""
    rid = await memory.store_candidate_routine(
        name="Markisen Routine",
        description="Markisen bei Regen einfahren",
        trigger="Regen",
        actions=[{"tool": "blinds_control", "args": {"action": "up"}}],
        trigger_type="weather",
        trigger_value="raining",
        autonomy_level=2,
    )
    assert rid is not None and rid > 0

    # Retrieve and verify all trigger fields
    routines = await memory.get_routines(enabled_only=False)
    routine = next((r for r in routines if r["id"] == rid), None)
    assert routine is not None, f"Routine {rid} not found in get_routines()"
    assert routine["trigger_type"] == "weather"
    assert routine["trigger_value"] == "raining"
    assert routine["autonomy_level"] == 2
    assert routine["name"] == "Markisen Routine"


async def test_store_candidate_routine_default_trigger_fields_are_none(memory):
    """store_candidate_routine without trigger fields defaults to None/0."""
    rid = await memory.store_candidate_routine(
        name="Simple Routine",
        description="Simple",
        trigger="manual",
        actions=[],
    )
    routines = await memory.get_routines(enabled_only=False)
    routine = next((r for r in routines if r["id"] == rid), None)
    assert routine is not None
    assert routine["trigger_type"] is None
    assert routine["trigger_value"] is None
    assert routine["autonomy_level"] == 0
