from __future__ import annotations

from unittest.mock import MagicMock

from src.tools.security import set_context as security_set_ctx
from src.tools.presence import set_context as presence_set_ctx
from src.tools.context import ToolContext


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


def _make_ctx(hal=None, presence_manager=None) -> ToolContext:
    return ToolContext(
        hal=hal or MagicMock(),
        presence_manager=presence_manager or MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


class MockPresence:
    def format_for_prompt(self):
        return "Niemand zuhause."

    @property
    def open_windows(self):
        return []


def test_create_security_agent(monkeypatch):
    """Verify security agent is created with 7 tools."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    security_set_ctx(_make_ctx())
    presence_set_ctx(_make_ctx(presence_manager=MockPresence()))

    config = make_mock_config()

    from src.agents.security import create_security_agent

    agent = create_security_agent(config)

    assert agent is not None
    assert hasattr(agent, "tool_names")
    tool_names = agent.tool_names
    assert len(tool_names) == 7, f"Expected 7 tools, got {len(tool_names)}: {tool_names}"


def test_security_agent_tool_names(monkeypatch):
    """Check exact tool names on security agent."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    security_set_ctx(_make_ctx())
    presence_set_ctx(_make_ctx(presence_manager=MockPresence()))

    config = make_mock_config()

    from src.agents.security import create_security_agent

    agent = create_security_agent(config)

    expected_tools = {
        "alarm_control",
        "door_open",
        "get_security_status",
        "get_camera_events",
        "who_is_home",
        "get_window_states",
        "remember_correction",
    }
    assert set(agent.tool_names) == expected_tools
