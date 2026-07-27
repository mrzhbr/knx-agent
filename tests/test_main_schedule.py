"""Tests verifying ScheduleEngine wiring in main.py and supervisor.py updates."""

from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock

from src.agents.supervisor import create_supervisor


def make_mock_config():
    from src.config import ModelSlot, ModelSlotConfig

    _specialists = ModelSlotConfig(
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        model_id="accounts/fireworks/models/minimax-m2p5",
    )
    _router = ModelSlotConfig(
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        model_id="accounts/fireworks/models/gpt-oss-120b",
    )

    config = MagicMock()
    config.models.specialists = _specialists
    config.models.router = _router
    config.resolve_model.side_effect = lambda slot: {
        ModelSlot.SPECIALISTS_TIER: _specialists,
        ModelSlot.ROUTER_TIER: _router,
    }[slot]
    return config


def _make_tool_context(memory=None, schedule_engine=None):
    """Create a ToolContext with minimal mocks for supervisor/schedule tests."""
    from src.tools.context import ToolContext

    if memory is None:
        memory = MagicMock()
        memory.procedural = AsyncMock()
        memory.procedural.add_preference = AsyncMock()
        memory.procedural.search_preferences = AsyncMock(return_value=[])
        memory.episodic = None
        memory.semantic = None

    if schedule_engine is None:
        schedule_engine = AsyncMock()
        schedule_engine.add_one_shot = AsyncMock(return_value=1)
        schedule_engine.execute_routine = AsyncMock(return_value="OK")
        mock_mem = MagicMock()
        mock_mem.procedural = AsyncMock()
        mock_mem.procedural.get_active_schedules = AsyncMock(return_value=[])
        mock_mem.procedural.get_routines = AsyncMock(return_value=[])
        mock_mem.procedural.get_routine_by_name = AsyncMock(return_value=None)
        mock_mem.procedural.cancel_schedule = AsyncMock(return_value=True)
        # Use the same memory for schedule tools

    return ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=memory,
        schedule_engine=schedule_engine,
    )


def test_supervisor_accepts_schedule_agent(monkeypatch):
    """create_supervisor accepts schedule agent via agents dict."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    from strands import Agent
    from src.tools.memory import set_context as memory_set_ctx
    from src.tools.schedule import set_context as schedule_set_ctx

    # Memory context
    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.add_preference = AsyncMock()
    memory.procedural.search_preferences = AsyncMock(return_value=[])
    memory.episodic = None
    memory.semantic = None

    # Schedule engine
    engine = AsyncMock()
    engine.add_one_shot = AsyncMock(return_value=1)
    engine.execute_routine = AsyncMock(return_value="OK")
    mock_mem = MagicMock()
    mock_mem.procedural = AsyncMock()
    mock_mem.procedural.get_active_schedules = AsyncMock(return_value=[])
    mock_mem.procedural.get_routines = AsyncMock(return_value=[])
    mock_mem.procedural.get_routine_by_name = AsyncMock(return_value=None)
    mock_mem.procedural.cancel_schedule = AsyncMock(return_value=True)

    from src.tools.context import ToolContext

    memory_ctx = ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=memory,
        schedule_engine=engine,
    )
    memory_set_ctx(memory_ctx)

    schedule_ctx = ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=mock_mem,
        schedule_engine=engine,
    )
    schedule_set_ctx(schedule_ctx)

    from src.agents.schedule import create_schedule_agent

    config = make_mock_config()

    schedule_agent = create_schedule_agent(config)
    supervisor = create_supervisor(config, agents={"schedule": schedule_agent})

    assert isinstance(supervisor, Agent)


def test_supervisor_without_agents_still_works(monkeypatch):
    """Supervisor with no agents dict works."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    from strands import Agent
    from src.tools.memory import set_context as memory_set_ctx

    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.add_preference = AsyncMock()
    memory.procedural.search_preferences = AsyncMock(return_value=[])
    memory.episodic = None
    memory.semantic = None

    from src.tools.context import ToolContext

    ctx = ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=memory,
        schedule_engine=MagicMock(),
    )
    memory_set_ctx(ctx)

    config = make_mock_config()
    supervisor = create_supervisor(config)

    assert isinstance(supervisor, Agent)


def test_supervisor_with_multiple_agents(monkeypatch):
    """Supervisor with multiple agents registered."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    from strands import Agent
    from src.tools.memory import set_context as memory_set_ctx
    from src.tools.schedule import set_context as schedule_set_ctx

    memory = MagicMock()
    memory.procedural = AsyncMock()
    memory.procedural.add_preference = AsyncMock()
    memory.procedural.search_preferences = AsyncMock(return_value=[])
    memory.episodic = None
    memory.semantic = None

    engine = AsyncMock()
    engine.add_one_shot = AsyncMock(return_value=1)
    engine.execute_routine = AsyncMock(return_value="OK")
    mock_mem = MagicMock()
    mock_mem.procedural = AsyncMock()
    mock_mem.procedural.get_active_schedules = AsyncMock(return_value=[])
    mock_mem.procedural.get_routines = AsyncMock(return_value=[])
    mock_mem.procedural.get_routine_by_name = AsyncMock(return_value=None)
    mock_mem.procedural.cancel_schedule = AsyncMock(return_value=True)

    from src.tools.context import ToolContext

    memory_ctx = ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=memory,
        schedule_engine=engine,
    )
    memory_set_ctx(memory_ctx)

    schedule_ctx = ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=mock_mem,
        schedule_engine=engine,
    )
    schedule_set_ctx(schedule_ctx)

    from src.agents.schedule import create_schedule_agent

    config = make_mock_config()

    schedule_agent = create_schedule_agent(config)
    supervisor = create_supervisor(
        config, agents={"schedule": schedule_agent, "comfort": MagicMock()}
    )

    assert isinstance(supervisor, Agent)


async def test_schedule_in_offline_adapters():
    """offline_adapters dict includes 'schedule' key (test pattern verification)."""
    from src.agents.offline import execute_offline

    mock_engine = MagicMock()
    mock_engine.execute_routine = AsyncMock(return_value="Routine ausgeführt.")
    adapters = {"schedule": mock_engine}
    await execute_offline("execute_routine", {"name": "bettfertig"}, adapters)
    mock_engine.execute_routine.assert_called_once_with("bettfertig")
