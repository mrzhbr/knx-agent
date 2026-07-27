from __future__ import annotations

from unittest.mock import MagicMock

from src.tools.comfort import set_context as comfort_set_ctx
from src.tools.context import ToolContext


def make_mock_config():
    from src.config import ModelSlotConfig

    config = MagicMock()
    config.models.specialists.model_id = "accounts/fireworks/models/minimax-m2p5"
    config.models.router.model_id = "accounts/fireworks/models/gpt-oss-120b"
    config.resolve_model.return_value = ModelSlotConfig(
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        model_id="accounts/fireworks/models/minimax-m2p5",
    )
    return config


def _make_ctx() -> ToolContext:
    return ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


def test_create_comfort_agent(monkeypatch):
    """Verify comfort agent is created with 17 tools (11 core + 2 nested agents + 4 scene tools)."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    # Setup tool dependencies first
    comfort_set_ctx(_make_ctx())

    config = make_mock_config()

    from src.agents.comfort import create_comfort_agent

    agent = create_comfort_agent(config)

    assert agent is not None
    # Strands Agent exposes tool_names
    assert hasattr(agent, "tool_names")
    tool_names = agent.tool_names
    # 11 core tools (incl. get_weather) + topology_agent + rules_agent + 4 scene tools = 17
    assert len(tool_names) == 17
    assert "get_weather" in tool_names


def test_comfort_agent_has_recall_preference():
    """recall_preference is imported from src.tools.memory (no local stub)."""
    from src.tools.memory import recall_preference

    assert recall_preference is not None
    assert hasattr(recall_preference, "tool_name")
    assert recall_preference.tool_name == "recall_preference"


def test_comfort_agent_has_ac_tools(monkeypatch):
    """Comfort agent includes ac_control and get_ac_status tools."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    comfort_set_ctx(_make_ctx())

    config = make_mock_config()

    from src.agents.comfort import create_comfort_agent

    agent = create_comfort_agent(config)
    tool_names = agent.tool_names
    assert "ac_control" in tool_names
    assert "get_ac_status" in tool_names


def test_comfort_agent_has_execute_batch(monkeypatch):
    """Comfort agent includes execute_batch tool (not set_scene)."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    from src.tools.comfort import set_context

    set_context(_make_ctx())

    config = make_mock_config()

    from src.agents.comfort import create_comfort_agent

    agent = create_comfort_agent(config)
    tool_names = agent.tool_names
    assert "execute_batch" in tool_names
    assert "set_scene" not in tool_names


def test_comfort_agent_has_nested_agents(monkeypatch):
    """Comfort agent has topology_agent and rules_agent as lazy-loaded tools."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    from src.tools.comfort import set_context

    set_context(_make_ctx())

    config = make_mock_config()

    from src.agents.comfort import create_comfort_agent

    agent = create_comfort_agent(config)
    tool_names = agent.tool_names
    assert "topology_agent" in tool_names
    assert "rules_agent" in tool_names
    # Direct topology/rules tools should NOT be present
    assert "get_room_info" not in tool_names
    assert "configure_house_rule" not in tool_names
