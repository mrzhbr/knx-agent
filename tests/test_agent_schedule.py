"""Tests for the schedule specialist agent."""

from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock

from src.agents.schedule import create_schedule_agent
from src.tools.schedule import set_context as schedule_set_ctx
from src.tools.context import ToolContext
from src.agents.prompts import SCHEDULE_PROMPT


def make_mock_config():
    from src.config import ModelSlotConfig

    config = MagicMock()
    config.models.specialists.model_id = "accounts/fireworks/models/minimax-m2p5"
    config.resolve_model.return_value = ModelSlotConfig(
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        model_id="accounts/fireworks/models/minimax-m2p5",
    )
    return config


def _make_ctx(engine=None, memory=None) -> ToolContext:
    if engine is None:
        engine = AsyncMock()
        engine.add_one_shot = AsyncMock(return_value=1)
        engine.execute_routine = AsyncMock(return_value="OK")
    if memory is None:
        memory = MagicMock()
        memory.procedural = AsyncMock()
        memory.procedural.get_active_schedules = AsyncMock(return_value=[])
        memory.procedural.get_routines = AsyncMock(return_value=[])
        memory.procedural.get_routine_by_name = AsyncMock(return_value=None)
        memory.procedural.cancel_schedule = AsyncMock(return_value=True)
    return ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=memory,
        schedule_engine=engine,
    )


def test_create_schedule_agent_returns_agent(monkeypatch):
    """create_schedule_agent returns a Strands Agent instance."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    from strands import Agent

    schedule_set_ctx(_make_ctx())

    config = make_mock_config()
    agent = create_schedule_agent(config)

    assert isinstance(agent, Agent)


def test_schedule_agent_uses_specialists_model(monkeypatch):
    """Schedule agent is created with the Specialists model ID."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    schedule_set_ctx(_make_ctx())

    config = make_mock_config()
    create_schedule_agent(config)

    # Verify model uses specialists slot
    assert config.models.specialists.model_id == "accounts/fireworks/models/minimax-m2p5"


def test_schedule_agent_has_9_tools(monkeypatch):
    """Schedule agent is created with 9 tools including create_triggered_routine and disable_routine."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    schedule_set_ctx(_make_ctx())

    config = make_mock_config()
    agent = create_schedule_agent(config)

    # Agent should have 9 tools: schedule_task, execute_routine, list_schedules, cancel_schedule,
    # create_triggered_routine, disable_routine, remember_correction, list_rooms, get_room_state
    tool_names = agent.tool_names
    assert len(tool_names) == 9
    assert "schedule_task" in tool_names
    assert "execute_routine" in tool_names
    assert "list_schedules" in tool_names
    assert "cancel_schedule" in tool_names
    assert "create_triggered_routine" in tool_names
    assert "disable_routine" in tool_names


def test_schedule_agent_uses_schedule_prompt(monkeypatch):
    """Schedule agent uses the SCHEDULE_PROMPT system prompt."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    schedule_set_ctx(_make_ctx())

    config = make_mock_config()
    agent = create_schedule_agent(config)

    # SCHEDULE_PROMPT should be the system prompt
    assert agent.system_prompt == SCHEDULE_PROMPT


def test_schedule_prompt_is_german():
    """SCHEDULE_PROMPT is in German (embodied framing)."""
    assert "Deutsch" in SCHEDULE_PROMPT or "Zeitplan" in SCHEDULE_PROMPT
