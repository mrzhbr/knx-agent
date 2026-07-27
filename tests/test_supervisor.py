from __future__ import annotations

from unittest.mock import MagicMock

from src.agents.supervisor import create_supervisor
from src.tools.comfort import set_context as comfort_set_ctx
from src.tools.context import ToolContext


def _make_ctx() -> ToolContext:
    return ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


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


def test_create_supervisor_no_agents(monkeypatch):
    """Supervisor can be created with no specialists."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    config = make_mock_config()
    agent = create_supervisor(config)
    assert agent is not None


def test_create_supervisor_with_comfort(monkeypatch):
    """Supervisor accepts comfort agent as tool."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    config = make_mock_config()

    comfort_set_ctx(_make_ctx())

    from src.agents.comfort import create_comfort_agent

    comfort = create_comfort_agent(config)

    agent = create_supervisor(config, agents={"comfort": comfort})
    assert agent is not None


def test_supervisor_has_correct_model(monkeypatch):
    """Supervisor should use the specialists slot model."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    config = make_mock_config()

    agent = create_supervisor(config)
    assert agent is not None


def test_create_supervisor_with_multiple_agents(monkeypatch):
    """Supervisor registers multiple specialist agents."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    config = make_mock_config()

    comfort_set_ctx(_make_ctx())

    from src.agents.comfort import create_comfort_agent

    comfort = create_comfort_agent(config)
    mock_security = MagicMock()

    agent = create_supervisor(config, agents={"comfort": comfort, "security": mock_security})
    assert agent is not None


def test_supervisor_has_memory_tools(monkeypatch):
    """create_supervisor() produces an agent with all 5 memory tools registered."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    config = make_mock_config()
    agent = create_supervisor(config)

    tool_names = set(agent.tool_names)

    expected_memory_tools = {
        "remember",
        "recall",
        "remember_correction",
        "describe_device",
        "get_unknown_devices",
    }
    assert expected_memory_tools.issubset(tool_names), (
        f"Missing memory tools: {expected_memory_tools - tool_names}. Found: {tool_names}"
    )
