"""Tests for graduated autonomy execution in ScheduleEngine."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def make_config():
    from src.config import BrainConfig
    import yaml

    with open("config.yaml.example") as f:
        cfg = BrainConfig.model_validate(yaml.safe_load(f))
    return cfg


def make_engine(memory=None, matrix=None, supervisor_fn=None):
    from src.background.scheduler import ScheduleEngine

    config = make_config()
    if memory is None:
        memory = MagicMock()
        memory.procedural = AsyncMock()
    tool_registry = {}
    engine = ScheduleEngine(
        memory_layer=memory,
        tool_registry=tool_registry,
        config=config,
        supervisor_fn=supervisor_fn,
        matrix=matrix,
    )
    return engine


def make_routine(name="Test", autonomy_level=1, actions=None, routine_id=1):
    return {
        "id": routine_id,
        "name": name,
        "autonomy_level": autonomy_level,
        "actions": actions or [],
    }


# ─── Level 0: suggest only ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_level0_sends_suggestion_to_matrix():
    matrix = AsyncMock()
    matrix.send = AsyncMock()
    config = make_config()
    config.matrix.room_id = "!room:test"
    engine = make_engine(matrix=matrix)

    routine = make_routine(autonomy_level=0)
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    memory.procedural.log_routine_execution = AsyncMock()
    engine._memory = memory

    result = await engine.execute_routine("Test")
    assert "Soll ich" in result
    assert "Test" in result
    matrix.send.assert_called_once()


@pytest.mark.asyncio
async def test_level0_does_not_execute_actions():
    engine = make_engine()
    tool_called = []

    async def fake_tool(**kwargs):
        tool_called.append(True)
        return "done"

    engine._tool_registry = {"fake_tool": fake_tool}
    routine = make_routine(
        autonomy_level=0,
        actions=[{"tool": "fake_tool", "args": {}}],
    )
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    engine._memory = memory

    await engine.execute_routine("Test")
    assert len(tool_called) == 0


@pytest.mark.asyncio
async def test_level0_no_matrix_still_returns_suggestion():
    engine = make_engine(matrix=None)
    routine = make_routine(autonomy_level=0)
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    engine._memory = memory

    result = await engine.execute_routine("Test")
    assert "Soll ich" in result


# ─── Level 1: execute + notify Matrix ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_level1_executes_and_notifies():
    matrix = AsyncMock()
    matrix.send = AsyncMock()
    config = make_config()
    config.matrix.room_id = "!room:test"
    engine = make_engine(matrix=matrix)

    async def fake_tool(**kwargs):
        return "Licht eingeschaltet"

    engine._tool_registry = {"light_on": fake_tool}
    routine = make_routine(
        autonomy_level=1,
        actions=[{"tool": "light_on", "args": {}}],
    )
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    memory.procedural.log_routine_execution = AsyncMock()
    engine._memory = memory

    result = await engine.execute_routine("Test")
    assert "Licht eingeschaltet" in result
    matrix.send.assert_called_once()
    call_msg = matrix.send.call_args[0][1]
    assert "Test" in call_msg
    assert "ausgeführt" in call_msg


@pytest.mark.asyncio
async def test_level1_notification_contains_result():
    matrix = AsyncMock()
    matrix.send = AsyncMock()
    config = make_config()
    config.matrix.room_id = "!room:test"
    engine = make_engine(matrix=matrix)

    async def fake_tool(**kwargs):
        return "Jalousien geöffnet"

    engine._tool_registry = {"blinds": fake_tool}
    routine = make_routine(
        autonomy_level=1,
        actions=[{"tool": "blinds", "args": {}}],
    )
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    memory.procedural.log_routine_execution = AsyncMock()
    engine._memory = memory

    await engine.execute_routine("Test")
    call_msg = matrix.send.call_args[0][1]
    assert "Jalousien geöffnet" in call_msg


# ─── Level 2: silent execution ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_level2_executes_without_matrix_notification():
    matrix = AsyncMock()
    matrix.send = AsyncMock()
    config = make_config()
    config.matrix.room_id = "!room:test"
    engine = make_engine(matrix=matrix)

    async def fake_tool(**kwargs):
        return "done"

    engine._tool_registry = {"fake": fake_tool}
    routine = make_routine(
        autonomy_level=2,
        actions=[{"tool": "fake", "args": {}}],
    )
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    memory.procedural.log_routine_execution = AsyncMock()
    engine._memory = memory

    result = await engine.execute_routine("Test")
    assert result == "done"
    matrix.send.assert_not_called()


@pytest.mark.asyncio
async def test_level2_still_logs_execution():
    engine = make_engine()

    async def fake_tool(**kwargs):
        return "done"

    engine._tool_registry = {"fake": fake_tool}
    routine = make_routine(autonomy_level=2, actions=[{"tool": "fake", "args": {}}])
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    memory.procedural.log_routine_execution = AsyncMock()
    engine._memory = memory

    await engine.execute_routine("Test")
    memory.procedural.log_routine_execution.assert_called_once_with(1, success=True)


# ─── Level 3: silent execution (same as Level 2 externally) ──────────────────


@pytest.mark.asyncio
async def test_level3_executes_without_matrix_notification():
    matrix = AsyncMock()
    matrix.send = AsyncMock()
    config = make_config()
    config.matrix.room_id = "!room:test"
    engine = make_engine(matrix=matrix)

    async def fake_tool(**kwargs):
        return "done"

    engine._tool_registry = {"fake": fake_tool}
    routine = make_routine(autonomy_level=3, actions=[{"tool": "fake", "args": {}}])
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    memory.procedural.log_routine_execution = AsyncMock()
    engine._memory = memory

    result = await engine.execute_routine("Test")
    assert result == "done"
    matrix.send.assert_not_called()


# ─── Security cap ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_security_action_capped_at_level0():
    matrix = AsyncMock()
    matrix.send = AsyncMock()
    config = make_config()
    config.matrix.room_id = "!room:test"
    engine = make_engine(matrix=matrix)

    # Tool called tracker
    tool_called = []

    async def alarm_control(**kwargs):
        tool_called.append(True)
        return "Alarm gesetzt"

    engine._tool_registry = {"alarm_control": alarm_control}
    routine = make_routine(
        autonomy_level=3,  # Would be silent, but security caps to 0
        actions=[{"tool": "alarm_control", "args": {}}],
    )
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    engine._memory = memory

    result = await engine.execute_routine("Test")
    # Should suggest, not execute
    assert "Soll ich" in result
    assert len(tool_called) == 0


@pytest.mark.asyncio
async def test_door_open_capped_at_level0():
    engine = make_engine()
    tool_called = []

    async def door_open(**kwargs):
        tool_called.append(True)
        return "Tür geöffnet"

    engine._tool_registry = {"door_open": door_open}
    routine = make_routine(
        autonomy_level=2,
        actions=[{"tool": "door_open", "args": {}}],
    )
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    engine._memory = memory

    result = await engine.execute_routine("Test")
    assert "Soll ich" in result
    assert len(tool_called) == 0


@pytest.mark.asyncio
async def test_security_description_keyword_capped():
    engine = make_engine()
    routine = make_routine(
        autonomy_level=2,
        actions=[{"tool": None, "description": "alarm scharf stellen", "args": {}}],
    )
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=routine)
    engine._memory = memory

    result = await engine.execute_routine("Test")
    assert "Soll ich" in result


# ─── Routine not found ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_routine_not_found_returns_error():
    engine = make_engine()
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.get_routine_by_name = AsyncMock(return_value=None)
    engine._memory = memory

    result = await engine.execute_routine("NonExistent")
    assert "nicht gefunden" in result
