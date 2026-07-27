"""Tests for the Strands Agent-based router (_route_with_agent, route())."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.router import _route_with_agent, route, _DEFAULT_ROUTING


def _make_mock_agent(json_response: dict) -> MagicMock:
    """Create a mock Strands Agent that returns a JSON classification."""
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: json.dumps(json_response)

    mock_agent = MagicMock()
    mock_agent.return_value = mock_result
    mock_agent.messages = MagicMock()  # Allow .messages.clear() call

    return mock_agent


async def test_route_with_agent_returns_correct_result():
    """_route_with_agent correctly parses JSON classification from agent."""
    mock_agent = _make_mock_agent(
        {
            "intent": "comfort",
            "complexity": 2,
            "agents": ["comfort"],
            "model": "specialists",
            "safety_critical": False,
        }
    )

    with patch(
        "src.agents.router.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
    ):
        result = await _route_with_agent("Mach das Licht an", mock_agent)

    assert result is not None
    assert result.intent == "comfort"
    assert result.complexity == 2
    assert result.agents == ["comfort"]
    assert result.model == "specialists"
    assert result.safety_critical is False


async def test_route_with_agent_clears_messages_before_call():
    """agent.messages.clear() is called before each invocation (AD-7)."""
    mock_agent = _make_mock_agent(
        {
            "intent": "energy",
            "complexity": 1,
            "agents": ["energy"],
            "model": "router",
            "safety_critical": False,
        }
    )

    with patch(
        "src.agents.router.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
    ):
        await _route_with_agent("Wie hoch ist der Stromverbrauch?", mock_agent)

    mock_agent.messages.clear.assert_called_once()


async def test_route_returns_default_when_no_agent():
    """route() returns _DEFAULT_ROUTING when router_agent is None and text is complex."""
    result = await route(
        "Erkläre mir wie die Heizung funktioniert und was ich optimieren kann.",
        router_agent=None,
        check_internet=False,
    )
    assert result.intent == _DEFAULT_ROUTING.intent
    assert result.model == _DEFAULT_ROUTING.model


async def test_route_with_agent_falls_back_on_json_error():
    """_route_with_agent returns None when agent returns non-JSON output."""
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "This is not JSON at all"
    mock_agent.return_value = mock_result
    mock_agent.messages = MagicMock()

    with patch(
        "src.agents.router.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
    ):
        result = await _route_with_agent("some text", mock_agent)

    assert result is None


async def test_route_falls_back_to_default_when_agent_returns_none():
    """route() returns _DEFAULT_ROUTING when _route_with_agent returns None."""
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: "not valid json"
    mock_agent.return_value = mock_result
    mock_agent.messages = MagicMock()

    with patch(
        "src.agents.router.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
    ):
        result = await route(
            "Some complex question without offline match",
            router_agent=mock_agent,
            check_internet=False,
        )

    # Should fall back to default routing
    assert result.intent == _DEFAULT_ROUTING.intent
    assert result.model == _DEFAULT_ROUTING.model


async def test_route_with_agent_strips_markdown_code_block():
    """_route_with_agent strips ```json...``` wrapper from agent output."""
    mock_agent = MagicMock()
    mock_result = MagicMock()
    json_data = {
        "intent": "security",
        "complexity": 4,
        "agents": ["security"],
        "model": "sonnet",
        "safety_critical": True,
    }
    mock_result.__str__ = lambda self: f"```json\n{json.dumps(json_data)}\n```"
    mock_agent.return_value = mock_result
    mock_agent.messages = MagicMock()

    with patch(
        "src.agents.router.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
    ):
        result = await _route_with_agent("Wer ist an der Tür?", mock_agent)

    assert result is not None
    assert result.intent == "security"
    assert result.safety_critical is True
