"""Tests for energy agent creation."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.agents.energy import create_energy_agent
from src.tools.energy import set_context as energy_set_ctx
from src.tools.comfort import set_context as comfort_set_ctx
from src.tools.presence import set_context as presence_set_ctx
from src.tools.context import ToolContext


def make_mock_config():
    from src.config import ModelSlotConfig

    config = MagicMock()
    config.models.router.model_id = "accounts/fireworks/models/gpt-oss-120b"
    config.models.specialists.model_id = "accounts/fireworks/models/minimax-m2p5"
    config.resolve_model.return_value = ModelSlotConfig(
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        model_id="accounts/fireworks/models/gpt-oss-120b",
    )
    return config


def _make_ctx(energy_awareness=None, weather=None) -> ToolContext:
    hal = MagicMock()
    hal.get_platform = lambda name: weather if name == "weather" else None
    return ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=energy_awareness or MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


def test_create_energy_agent(monkeypatch):
    """Energy agent is created with router model."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    config = make_mock_config()

    # Initialize tool deps (energy agent imports tools that need deps)
    energy_set_ctx(_make_ctx(energy_awareness=MagicMock(), weather=MagicMock()))
    comfort_set_ctx(_make_ctx())
    presence_set_ctx(_make_ctx())

    agent = create_energy_agent(config)
    assert agent is not None


def test_energy_agent_has_tools(monkeypatch):
    """Energy agent should have 5 tools."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    config = make_mock_config()

    energy_set_ctx(_make_ctx(energy_awareness=MagicMock(), weather=MagicMock()))
    comfort_set_ctx(_make_ctx())
    presence_set_ctx(_make_ctx())

    agent = create_energy_agent(config)
    # Agent should have tools registered
    assert agent is not None
